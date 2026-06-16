#!/usr/bin/env python3
"""
Backfill — pobiera całą dostępną historię z Profitroom (wszystkie zakładki lat)
i uzupełnia occupancy.csv od podanej daty startowej.

Użycie:
  python backfill.py                  # od 2026-03-01 do dziś
  python backfill.py 2026-01-01       # od podanej daty
"""
import sys
from datetime import date
from pathlib import Path

# Dodaj katalog skryptu do ścieżki
sys.path.insert(0, str(Path(__file__).parent))
from check_availability import log, load_csv, save_csv, fetch_availability

START_DATE = date(2026, 3, 1)

def main():
    if len(sys.argv) > 1:
        try:
            start = date.fromisoformat(sys.argv[1])
        except ValueError:
            print(f"Błąd: nieprawidłowy format daty '{sys.argv[1]}', użyj YYYY-MM-DD")
            sys.exit(1)
    else:
        start = START_DATE

    today = date.today()
    log(f"=== Backfill: {start} → {today} ===")

    # Pobierz dane ze wszystkich zakładek lat
    log("Pobieranie danych ze wszystkich lat z Profitroom...")
    try:
        fetched = fetch_availability(all_years=True)
    except Exception as e:
        log(f"BŁĄD: {e}")
        raise

    if not fetched:
        log("BŁĄD: Brak danych z Profitroom")
        sys.exit(1)

    # Filtruj tylko zakres start → dziś
    in_range = {d: s for d, s in fetched.items() if start.isoformat() <= d <= today.isoformat()}
    log(f"Dni w zakresie {start} → {today}: {len(in_range)}")

    # Wczytaj istniejące dane i połącz (fetched nadpisuje stare wpisy w zakresie)
    existing = load_csv()
    existing.update(in_range)
    save_csv(existing)

    # Podsumowanie
    occ  = sum(1 for s in in_range.values() if s == "occupied")
    ci   = sum(1 for s in in_range.values() if s == "checkin")
    co   = sum(1 for s in in_range.values() if s == "checkout")
    free = sum(1 for s in in_range.values() if s == "free")
    log(f"Zapisano: {occ} zajętych, {ci} checkin, {co} checkout, {free} wolnych")
    log(f"Łącznie w CSV: {len(existing)} dni")
    log("=== Backfill zakończony ===")


if __name__ == "__main__":
    main()
