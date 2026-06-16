#!/usr/bin/env python3
"""
Profitroom Availability Monitor — Perełka Bałtyku
Pobiera zajętość z Profitroom (Handsontable) i zapisuje do CSV.
"""
import csv, os, re, sys, json
from datetime import date, datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL       = "https://5806.center-inner.profitroom.com/avail-review/pl/room/474980/474980/9bd957797b4da9c0d9a417d4da8813cdcbb1ef9f"
DATA_FILE = Path("data/occupancy.csv")
LOG_FILE  = Path("data/run_log.txt")

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def load_csv():
    if not DATA_FILE.exists():
        return {}
    with open(DATA_FILE, newline="", encoding="utf-8") as f:
        return {r["date"]: r["status"] for r in csv.DictReader(f)}

def save_csv(records):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date","status","updated_at"])
        w.writeheader()
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for d in sorted(records):
            w.writerow({"date": d, "status": records[d], "updated_at": ts})

def fetch_availability():
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox","--disable-setuid-sandbox"])
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        log(f"Ładowanie: {URL}")
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeoutError:
            log("Timeout przy goto — próbuję mimo to")

        # Czekamy na Handsontable
        try:
            page.wait_for_selector("table.htCore", timeout=20_000)
            log("Znaleziono table.htCore")
        except PlaywrightTimeoutError:
            log("Brak table.htCore po 20s")

        page.wait_for_timeout(2_000)

        # Zapisz pełny HTML
        html = page.content()
        Path("data/last_page.html").write_text(html, encoding="utf-8")
        log(f"HTML zapisany ({len(html)} znaków)")

        # Wyciągnij strukturę Handsontable przez JS
        # Handsontable trzyma dane w instancji — szukamy jej przez Handsontable.instances
        data = page.evaluate("""() => {
            // Próba 1: przez globalny obiekt Handsontable
            if (typeof Handsontable !== 'undefined') {
                const instances = Handsontable.instances || [];
                const results = [];
                for (const ht of instances) {
                    try {
                        const data = ht.getData();
                        const colHeaders = ht.getColHeader();
                        const rowHeaders = ht.getRowHeader ? ht.getRowHeader() : [];
                        results.push({ data, colHeaders, rowHeaders });
                    } catch(e) {}
                }
                if (results.length) return { source: 'Handsontable.instances', results };
            }

            // Próba 2: przez element DOM — Handsontable zapisuje instancję na elemencie
            const tables = document.querySelectorAll('.handsontable');
            for (const el of tables) {
                if (el.hotInstance) {
                    try {
                        const ht = el.hotInstance;
                        const data = ht.getData();
                        const colHeaders = ht.getColHeader ? ht.getColHeader() : [];
                        return { source: 'hotInstance', data, colHeaders };
                    } catch(e) {}
                }
            }

            // Próba 3: bezpośredni zapis struktury tabeli HTML
            // Nagłówki kolumn — daty w <thead>
            const headers = [];
            document.querySelectorAll('table.htCore thead th').forEach(th => {
                headers.push(th.innerText.trim());
            });

            // Wiersze — klasy i tytuły komórek
            const rows = [];
            document.querySelectorAll('table.htCore tbody tr').forEach(tr => {
                const rowHeader = tr.querySelector('th') ? tr.querySelector('th').innerText.trim() : '';
                const cells = [];
                tr.querySelectorAll('td').forEach(td => {
                    cells.push({
                        text: td.innerText.trim(),
                        cls: td.className,
                        title: td.getAttribute('title') || ''
                    });
                });
                rows.push({ rowHeader, cells });
            });

            return { source: 'DOM', headers, rows };
        }""")

        dump = json.dumps(data, ensure_ascii=False, indent=2)
        Path("data/debug_ht.json").write_text(dump, encoding="utf-8")
        log(f"Handsontable dump zapisany ({len(dump)} znaków), source: {data.get('source','?')}")
        log(f"Podgląd: {dump[:800]}")

        # Parsowanie wyników
        source = data.get("source", "")

        if source == "DOM":
            results = parse_dom_structure(data)
        elif "results" in data and data["results"]:
            results = parse_ht_instances(data["results"])
        elif "data" in data and "colHeaders" in data:
            results = parse_ht_instance(data["data"], data["colHeaders"])

        browser.close()

    log(f"Pobrano {len(results)} dat łącznie")
    return results


def parse_dom_structure(data):
    """
    Parsuje strukturę DOM Handsontable.
    Nagłówki kolumn to numery dni, wiersze to miesiące lub pokoje.
    W Profitroom avail-review: wiersze = pokoje (tu mamy jeden), kolumny = dni.
    Klasy komórek: 'not_exist'=wolny(?), 'current'=zajęty(?), 'past'=przeszły, 'other'=inny miesiąc
    """
    results = {}
    headers = data.get("headers", [])
    rows = data.get("rows", [])

    log(f"DOM: {len(headers)} nagłówków, {len(rows)} wierszy")
    log(f"Nagłówki (pierwsze 20): {headers[:20]}")
    if rows:
        log(f"Pierwszy wiersz: rowHeader={rows[0].get('rowHeader','')}, "
            f"pierwsza komórka: {rows[0]['cells'][0] if rows[0]['cells'] else 'brak'}")

    # Handsontable avail-review: kolumny = dni, nagłówki = numery dni lub daty
    # Szukamy roku i miesiąca — mogą być w nagłówkach zakładek lub gdzie indziej
    # Spróbuj wyciągnąć daty z tytułów komórek
    for row in rows:
        for i, cell in enumerate(row.get("cells", [])):
            title = cell.get("title", "")
            cls   = cell.get("cls", "")
            text  = cell.get("text", "")

            # Szukaj daty w title
            m = re.search(r"(\d{4}-\d{2}-\d{2})", title)
            if not m:
                m = re.search(r"(\d{1,2})\.(\d{2})\.(\d{4})", title)
                if m:
                    d_str = f"{m.group(3)}-{m.group(2)}-{int(m.group(1)):02d}"
                else:
                    d_str = None
            else:
                d_str = m.group(1)

            if d_str:
                status = classify_profitroom(cls)
                if status:
                    results[d_str] = status

    if not results:
        log("Brak dat w title — próbuję rekonstrukcji z nagłówków kolumn")
        results = reconstruct_from_headers(headers, rows)

    return results


def reconstruct_from_headers(headers, rows):
    """
    Handsontable avail-review Profitroom ma kolumny = dni miesiąca.
    Nagłówki mogą być numerami 1-31 lub datami.
    Szukamy aktywnego roku/miesiąca z URL lub ze strony.
    """
    results = {}
    today = date.today()

    # Nagłówki mogą być np. ["", "1", "2", ..., "31"] lub datami
    # Spróbuj interpretować jako numery dni
    day_cols = []
    for i, h in enumerate(headers):
        h = str(h).strip()
        if re.fullmatch(r"\d{1,2}", h) and 1 <= int(h) <= 31:
            day_cols.append((i, int(h)))

    if not day_cols:
        log(f"Nie można zidentyfikować kolumn dni z nagłówków: {headers}")
        return results

    log(f"Znaleziono {len(day_cols)} kolumn dni")

    # Zakładamy bieżący i kolejne miesiące
    # W każdym wierszu szukamy wzorca: past=poprzedni miesiąc, current/not_exist=bieżący
    for row in rows:
        cells = row.get("cells", [])
        for col_i, day_num in day_cols:
            if col_i >= len(cells):
                continue
            cell = cells[col_i]
            cls  = cell.get("cls", "")
            # Ustal rok i miesiąc na podstawie kontekstu
            # (uproszczenie: używamy bieżącego miesiąca)
            try:
                d_str  = date(today.year, today.month, day_num).isoformat()
                status = classify_profitroom(cls)
                if status:
                    results[d_str] = status
            except ValueError:
                pass

    return results


def parse_ht_instances(ht_results):
    results = {}
    for ht in ht_results:
        r = parse_ht_instance(ht.get("data",[]), ht.get("colHeaders",[]))
        results.update(r)
    return results


def parse_ht_instance(data_rows, col_headers):
    results = {}
    log(f"HT instance: {len(data_rows)} wierszy, {len(col_headers)} nagłówków")
    # col_headers mogą zawierać daty
    for i, h in enumerate(col_headers):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", str(h))
        if not m:
            continue
        d_str = m.group(1)
        for row in data_rows:
            if i < len(row):
                val = str(row[i]).lower()
                if any(x in val for x in ["reserved","zajęty","1","true","yes"]):
                    results[d_str] = "occupied"
                elif any(x in val for x in ["free","wolny","0","false","no",""]):
                    results[d_str] = "free"
    return results


def classify_profitroom(css_classes):
    """
    Klasy Profitroom Handsontable:
    - 'current' = bieżący miesiąc, komórka aktywna
    - 'not_exist' = wolny / niedostępny do rezerwacji  
    - 'disabled' lub brak = zajęty / zarezerwowany
    - 'past' = przeszły dzień
    - 'other' = inny miesiąc
    Trzeba to zweryfikować po zobaczeniu debug_ht.json
    """
    c = css_classes.lower()
    # Pominięte komórki
    if "other" in c or "corner" in c or "rowheader" in c or "colheader" in c:
        return None
    # Zajęty
    if "disabled" in c:
        return "occupied"
    # Wolny (w Profitroom avail-review "not_exist" = wolny tzn. można zarezerwować?
    # lub odwrotnie — weryfikacja po debug_ht.json)
    if "not_exist" in c or "current" in c:
        return "free"
    return None


def main():
    today_str = date.today().isoformat()
    log(f"=== Start: {today_str} ===")
    records = load_csv()

    try:
        fetched = fetch_availability()
    except Exception as e:
        log(f"BŁĄD: {e}")
        raise

    if not fetched:
        log("OSTRZEŻENIE: Brak danych — sprawdź artefakt debug_ht.json w Actions")
        gha = os.environ.get("GITHUB_OUTPUT", "")
        if gha:
            with open(gha, "a") as f:
                f.write(f"status=unknown\ndate={today_str}\n")
        log("=== Koniec (brak danych) ===")
        return

    records.update(fetched)
    save_csv(records)

    status = records.get(today_str, "unknown")
    log(f"Status dziś ({today_str}): {status}")

    gha = os.environ.get("GITHUB_OUTPUT", "")
    if gha:
        with open(gha, "a") as f:
            f.write(f"status={status}\ndate={today_str}\n")

    log("=== Koniec ===")


if __name__ == "__main__":
    sys.exit(main())
