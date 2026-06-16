# 🏖️ Monitor zajętości — Perełka Bałtyku

Automatyczny monitoring zajętości apartamentu.  
GitHub Actions codziennie pobiera dane z Profitroom → zapisuje do CSV → `index.html` wizualizuje wszystko w przeglądarce.

## Konfiguracja (10 minut)

### 1. Utwórz repozytorium na GitHub

- github.com → **New repository**
- Nazwa: `apartment-monitor`
- Ustaw jako **Public** (wymagane dla darmowego GitHub Pages)
- ✅ Zaznacz "Add a README file"

### 2. Wgraj pliki

Wgraj całą zawartość tego ZIPa do repozytorium:
**Add file → Upload files**, zachowaj strukturę folderów.

### 3. Włącz GitHub Pages

W repozytorium: **Settings → Pages**  
- Source: **Deploy from a branch**  
- Branch: **main** / folder: **/ (root)**  
- Kliknij **Save**

Po chwili strona będzie dostępna pod adresem:  
`https://<twoja-nazwa>.github.io/apartment-monitor/`

### 4. Uruchom pierwszy raz (test)

**Actions → Sprawdź zajętość → Run workflow**

Po ~2 minutach odśwież stronę GitHub Pages — pojawią się pierwsze dane.

### 5. Gotowe

Od teraz Actions uruchamia się automatycznie **codziennie o 8:00** (czas polski).  
Strona aktualizuje się automatycznie — wystarczy ją odświeżyć.

---

## Jak używać weryfikacji

1. Otwórz zakładkę **🔍 Weryfikacja**
2. Wybierz miesiąc
3. Kliknij **Wprowadź zajęte dni…**
4. Otwórz kalendarz Profitroom, wpisz numery zajętych dni
5. Kliknij **Porównaj**

Różnice są podświetlone na żółto w tabeli i oznaczone kropką w kalendarzu.

---

## Pliki

| Plik | Opis |
|------|------|
| `index.html` | Dashboard — otwórz w przeglądarce |
| `check_availability.py` | Skrypt pobierający dane z Profitroom |
| `.github/workflows/daily.yml` | Harmonogram GitHub Actions (codziennie 8:00) |
| `data/occupancy.csv` | Historia zajętości (rośnie codziennie) |
| `data/last_page.html` | Ostatnia pobrana strona Profitroom (debug) |
| `data/run_log.txt` | Log wykonań |

## Rozwiązywanie problemów

**Status zawsze "unknown" / brak danych**  
Sprawdź zakładkę **Actions → ostatnie uruchomienie → Artifacts → last-page-xxx**.  
Pobierz `last_page.html` i sprawdź jak wygląda strona Profitroom — może zmienili strukturę HTML.

**Strona nie działa (błąd wczytywania CSV)**  
Upewnij się że GitHub Pages jest włączone i Actions wykonały się co najmniej raz.  
CSV musi istnieć w `data/occupancy.csv`.
