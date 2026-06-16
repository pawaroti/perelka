#!/usr/bin/env python3
"""
Profitroom Availability Monitor — Perełka Bałtyku
Uruchamiany codziennie przez GitHub Actions o 20:00.
"""
import csv, os, re, sys
from datetime import date, datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL       = "https://5806.center-inner.profitroom.com/avail-review/pl/room/474980/474980/9bd957797b4da9c0d9a417d4da8813cdcbb1ef9f"
DATA_FILE = Path("data/occupancy.csv")
LOG_FILE  = Path("data/run_log.txt")

MONTH_ABBR = {
    "STY":1,"LUT":2,"MAR":3,"KWI":4,"MAJ":5,"CZE":6,
    "LIP":7,"SIE":8,"WRZ":9,"PAZ":10,"LIS":11,"GRU":12
}

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
        w = csv.DictWriter(f, fieldnames=["date", "status", "updated_at"])
        w.writeheader()
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for d in sorted(records):
            w.writerow({"date": d, "status": records[d], "updated_at": ts})

def detect_transition_days(results: dict) -> dict:
    """
    Checkin  = pierwszy dzień rezerwacji po wolnym (rano wolny, klienci od ~16:00).
    Checkout = ostatni dzień rezerwacji przed wolnym (klienci do ~11:00, od 16:00 wolny).
    """
    updated = dict(results)
    dates = sorted(results.keys())
    for i, d in enumerate(dates):
        if results[d] != "occupied":
            continue
        try:
            d_obj = date.fromisoformat(d)
        except ValueError:
            continue
        # checkout: następny dzień wolny
        if i + 1 < len(dates):
            nd = dates[i + 1]
            try:
                if (date.fromisoformat(nd) - d_obj).days == 1 and results[nd] == "free":
                    updated[d] = "checkout"
                    continue
            except ValueError:
                pass
        # checkin: poprzedni dzień wolny
        if i > 0:
            pd = dates[i - 1]
            try:
                if (d_obj - date.fromisoformat(pd)).days == 1 and results[pd] == "free":
                    updated[d] = "checkin"
            except ValueError:
                pass
    return updated

def scrape_year(page, year: int) -> dict:
    """Pobiera dane z aktywnej zakładki roku."""
    table_data = page.evaluate("""() => {
        const headers = [];
        document.querySelectorAll('table.htCore thead th').forEach(th => {
            headers.push(th.innerText.trim());
        });
        const rows = [];
        document.querySelectorAll('table.htCore tbody tr').forEach(tr => {
            const th = tr.querySelector('th');
            const rowHeader = th ? th.innerText.trim() : '';
            const cells = [];
            tr.querySelectorAll('td').forEach(td => {
                cells.push(td.className);
            });
            rows.push({ rowHeader, cells });
        });
        return { headers, rows };
    }""")

    # headers[0] = "" (kolumna rowHeader w thead), headers[1]="1"...headers[31]="31"
    # komórki w tbody nie mają th wliczonego — indeks komórki = indeks nagłówka - 1
    day_map = {}
    for i, h in enumerate(table_data["headers"]):
        h = str(h).strip()
        if re.fullmatch(r"\d{1,2}", h) and 1 <= int(h) <= 31:
            day_map[i - 1] = int(h)

    results = {}
    for row in table_data["rows"]:
        month_num = MONTH_ABBR.get(row["rowHeader"].upper()[:3])
        if not month_num:
            continue
        for col_i, day_num in day_map.items():
            if col_i >= len(row["cells"]):
                continue
            cls = row["cells"][col_i]
            if "not_exist" in cls:
                continue
            if "disabled" in cls:
                status = "occupied"
            elif "active" in cls:
                status = "free"
            else:
                continue
            try:
                results[date(year, month_num, day_num).isoformat()] = status
            except ValueError:
                pass
    return results

def fetch_availability(all_years=False) -> dict:
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        log(f"Ładowanie: {URL}")
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        except PlaywrightTimeoutError:
            log("Timeout przy goto — próbuję mimo to")

        try:
            page.wait_for_selector("table.htCore", timeout=20_000)
        except PlaywrightTimeoutError:
            log("Brak table.htCore po 20s")
        page.wait_for_timeout(2_000)

        if all_years:
            # Pobierz wszystkie dostępne zakładki lat
            year_tabs = page.evaluate("""() => {
                const tabs = document.querySelectorAll('.mws-tabs li a, .ui-tabs-nav li a, [role="tab"]');
                const years = [];
                for (const tab of tabs) {
                    const m = tab.textContent.match(/20\\d{2}/);
                    if (m) years.push({ year: parseInt(m[0]), selector: tab.getAttribute('href') || tab.getAttribute('data-target') });
                }
                return years;
            }""")
            log(f"Znalezione zakładki lat: {[t['year'] for t in year_tabs]}")

            for tab in year_tabs:
                yr = tab["year"]
                sel = tab.get("selector", "")
                log(f"Przełączam na rok {yr}")
                try:
                    # Kliknij zakładkę roku
                    clicked = page.evaluate(f"""() => {{
                        const tabs = document.querySelectorAll('.mws-tabs li a, .ui-tabs-nav li a, [role="tab"]');
                        for (const tab of tabs) {{
                            if (tab.textContent.includes('{yr}')) {{
                                tab.click();
                                return true;
                            }}
                        }}
                        return false;
                    }}""")
                    if clicked:
                        page.wait_for_timeout(1_500)
                        yr_results = scrape_year(page, yr)
                        log(f"Rok {yr}: {len(yr_results)} dni")
                        results.update(yr_results)
                except Exception as e:
                    log(f"Błąd przy roku {yr}: {e}")
        else:
            # Tylko aktywna zakładka (bieżący rok)
            active_year = page.evaluate("""() => {
                const tabs = document.querySelectorAll('.mws-tabs li, .ui-tabs-nav li, [role="tab"]');
                for (const tab of tabs) {
                    if (tab.classList.contains('active') || tab.classList.contains('ui-state-active')) {
                        const m = tab.textContent.match(/20\\d{2}/);
                        if (m) return parseInt(m[0]);
                    }
                }
                return null;
            }""")
            if not active_year:
                active_year = date.today().year
            log(f"Aktywny rok: {active_year}")
            results = scrape_year(page, active_year)

        browser.close()

    results = detect_transition_days(results)
    occ  = sum(1 for s in results.values() if s == "occupied")
    ci   = sum(1 for s in results.values() if s == "checkin")
    co   = sum(1 for s in results.values() if s == "checkout")
    free = sum(1 for s in results.values() if s == "free")
    log(f"Łącznie {len(results)} dni: {occ} zajętych, {ci} checkin, {co} checkout, {free} wolnych")
    return results


def main():
    today_str = date.today().isoformat()
    log(f"=== Start: {today_str} ===")
    records = load_csv()

    try:
        fetched = fetch_availability(all_years=False)
    except Exception as e:
        log(f"BŁĄD: {e}")
        raise

    if not fetched:
        log("OSTRZEŻENIE: Brak danych z kalendarza")
        gha = os.environ.get("GITHUB_OUTPUT", "")
        if gha:
            with open(gha, "a") as f:
                f.write(f"status=unknown\ndate={today_str}\n")
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
