#!/usr/bin/env python3
"""
Profitroom Availability Monitor — Perełka Bałtyku
Pobiera zajętość z Profitroom i zapisuje do CSV.
"""
import csv, os, re, sys
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

def classify(css):
    c = css.lower()
    if any(m in c for m in ["reserved","occupied","unavailable","booked","zarezerwowany","blocked"]):
        return "occupied"
    if any(m in c for m in ["available","free","wolny","open"]):
        return "free"
    return None

MONTHS_PL = {
    "styczeń":1,"stycznia":1,"luty":2,"lutego":2,"marzec":3,"marca":3,
    "kwiecień":4,"kwietnia":4,"maj":5,"maja":5,"czerwiec":6,"czerwca":6,
    "lipiec":7,"lipca":7,"sierpień":8,"sierpnia":8,"wrzesień":9,"września":9,
    "październik":10,"października":10,"listopad":11,"listopada":11,
    "grudzień":12,"grudnia":12,
}

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

        # Poczekaj aż JavaScript wyrenderuje kalendarz
        try:
            page.wait_for_selector(
                ".calendar, table, [class*='avail'], [class*='cal'], [class*='month']",
                timeout=20_000
            )
        except PlaywrightTimeoutError:
            log("Selektor kalendarza nie znaleziony — zapisuję HTML do debugowania")

        # Dodatkowe odczekanie na JS
        page.wait_for_timeout(3_000)

        html = page.content()
        Path("data/last_page.html").write_text(html, encoding="utf-8")
        log(f"HTML zapisany ({len(html)} znaków)")

        # --- Strategia 1: atrybuty data-date ---
        cells = page.query_selector_all("[data-date]")
        if cells:
            log(f"Strategia 1: {len(cells)} komórek data-date")
            for cell in cells:
                d      = cell.get_attribute("data-date") or ""
                status = classify(cell.get_attribute("class") or "")
                if re.match(r"\d{4}-\d{2}-\d{2}", d) and status:
                    results[d] = status

        # --- Strategia 2: td/div z title ---
        if not results:
            cells = page.query_selector_all("td[title],div[title]")
            log(f"Strategia 2: {len(cells)} elementów z title")
            for cell in cells:
                title  = cell.get_attribute("title") or ""
                status = classify(cell.get_attribute("class") or "")
                m = re.search(r"(\d{4}-\d{2}-\d{2})", title) or \
                    re.search(r"(\d{1,2})\.(\d{2})\.(\d{4})", title)
                if m and status:
                    if "-" in m.group(0):
                        d = m.group(1)
                    else:
                        d = f"{m.group(3)}-{m.group(2)}-{int(m.group(1)):02d}"
                    results[d] = status

        # --- Strategia 3: tabela miesiąc-po-miesiącu ---
        if not results:
            log("Strategia 3: parsowanie tabeli")
            cur_year = date.today().year
            sections = page.query_selector_all(
                ".month-section,.month,[class*='month-wrap'],table"
            )
            for section in sections:
                hdr_el = section.query_selector(
                    "h2,h3,caption,th,[class*='month-title'],[class*='caption']"
                )
                hdr = hdr_el.inner_text().strip().lower() if hdr_el else ""
                ym  = re.search(r"\b(20\d{2})\b", hdr)
                yr  = int(ym.group(1)) if ym else cur_year
                mo  = next((v for k,v in MONTHS_PL.items() if k in hdr), None)
                if not mo:
                    continue
                for cell in section.query_selector_all("td,[class*='day']"):
                    txt = cell.inner_text().strip()
                    if re.fullmatch(r"\d{1,2}", txt):
                        day = int(txt)
                        if 1 <= day <= 31:
                            try:
                                d      = date(yr, mo, day).isoformat()
                                cls    = cell.get_attribute("class") or ""
                                status = classify(cls)
                                if status:
                                    results[d] = status
                                elif "empty" not in cls.lower() and "other" not in cls.lower():
                                    results[d] = "free"
                            except ValueError:
                                pass

        # Debug: zapisz unikalne klasy CSS żeby zobaczyć jak strona koduje zajętość
        all_classes = page.evaluate("""() => {
            const cls = new Set();
            document.querySelectorAll('*').forEach(el => {
                String(el.className || '').split(' ').forEach(c => c.trim() && cls.add(c));
            });
            return [...cls].sort().join('\\n');
        }""")
        Path("data/debug_classes.txt").write_text(all_classes, encoding="utf-8")
        log(f"Unikalne klasy CSS ({len(all_classes.splitlines())}): {', '.join(all_classes.splitlines()[:30])}")

        # Debug: fragment HTML kalendarza
        cal_html = page.evaluate("""() => {
            const sel = 'table, .calendar, [class*="cal"], [class*="avail"], [class*="month"]';
            const el = document.querySelector(sel);
            return el ? el.outerHTML.slice(0, 4000) : 'NIE ZNALEZIONO KALENDARZA';
        }""")
        Path("data/debug_calendar.html").write_text(cal_html, encoding="utf-8")
        log(f"Kalendarz HTML (pierwsze 300 znaków): {cal_html[:300]}")

        browser.close()
    log(f"Pobrano {len(results)} dat łącznie")
    return results


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
        log("OSTRZEŻENIE: Brak danych z kalendarza — sprawdź artefakt debug w Actions")
        # Nie exitujemy z kodem błędu — chcemy żeby git commit zapisał pliki debug
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
