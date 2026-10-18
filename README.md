# LN Translator

Konsolowe narzędzie do tłumaczenia light novel z angielskiego na polski.
Obsługuje lokalne modele przez **Ollama** oraz **DeepSeek API**.

## Wymagania

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Instalacja

```bash
# Zainstaluj uv (jeśli jeszcze nie masz)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sklonuj / pobierz projekt, wejdź do katalogu
cd ln-translator

# Zainstaluj zależności i utwórz venv automatycznie
uv sync
```

## Użycie

```bash
# Sprawdź konfigurację i dostępne modele Ollamy
uv run translator info

# Przetłumacz plik
uv run translator translate <plik_wejściowy>

# Podaj ścieżkę wyjściową
uv run translator translate <plik_wejściowy> -o output/<plik_wyjściowy>

# Nadpisz model bez edycji yaml
uv run translator translate <plik_wejściowy> --model qwen2.5:32b

# Użyj DeepSeek zamiast Ollamy
uv run translator translate <plik_wejściowy> --backend deepseek

# Podgląd planu podziału na chunki bez wysyłania do modelu
uv run translator translate <plik_wejściowy> --dry-run

# Dostosuj chunk i temperaturę w locie
uv run translator translate <plik_wejściowy> --chunk-size 3000 --temperature 0.2

# ────────────────────────────────────────────────────────
# GUI — podgląd tłumaczenia na żywo (EN | PL)
# ────────────────────────────────────────────────────────

# Parametry ustawiasz w konsoli, okno pokazuje postęp chunk po chunku
uv run translator translate <plik_wejściowy> --gui

# Z pełnym zestawem parametrów
uv run translator translate <plik_wejściowy> --gui --backend opencode-go --model glm-5.3-flash

# Przed startem otwórz edytor prompt.yaml (zapis = update na stałe,
# użyty w tym i wszystkich kolejnych tłumaczeniach; działa też bez --gui)
uv run translator translate <plik_wejściowy> --prompt
uv run translator translate <plik_wejściowy> --gui --prompt

# ────────────────────────────────────────────────────────
# DIAGNOSTYKA, KOREKTA I NAPRAWA BŁĘDÓW
# ────────────────────────────────────────────────────────

# Przetłumacz i od razu uruchom tryb korekty (refine)
uv run translator translate <plik_wejściowy> --refine

# Zdiagnozuj problemy w tekście bez uruchamiania modelu
uv run translator diagnose output/<plik_wyjściowy>

# Ręcznie skoryguj istniejące tłumaczenie z oryginałem EN
uv run translator refine output/<plik_wyjściowy> -e <plik_wejściowy>

# Automatycznie zregeneruj błędne chunki (jeśli tłumaczenie zostało przerwane/zawiodło)
uv run translator retry output/<plik_wyjściowy>

# Zregeneruj tylko wybrane chunki (6, 8) ręcznie
uv run translator retry output/<plik_wyjściowy> --chunks 6 8

# ────────────────────────────────────────────────────────
# STOP I WZNAWIANIE (limit czasu / restart komputera)
# ────────────────────────────────────────────────────────

# Auto-stop po N sekundach (limit czasu na właśnie ten przebieg)
uv run translator translate <plik_wejściowy> --max-time 7200

# Ctrl+C w trakcie też zapisuje sesję (state="stopped") zamiast nic nie zostawiać

# Wznowienie przerwanej sesji (generuje TYLKO brakujące chunki)
uv run translator resume output/<run>/<run>.txt

# Po katalogu sesji też działa; z kolejnym limitem czasu
uv run translator resume output/<run> --max-time 3600

# ────────────────────────────────────────────────────────
# GO Scripts
# ────────────────────────────────────────────────────────

#Convert PDF to TXT (zachowuje formatowanie)
cd scripts/converter
go run main.go sample.pdf output.txt

go run main.go --split sample.pdf output.txt  # split by chapters

```

## GUI — podgląd tłumaczenia na żywo

Flaga `--gui` otwiera okno z podglądem tłumaczenia w trakcie pracy silnika.
Parametry (backend, model, temperatura, chunk size) ustawiasz jak dotychczas
w konsoli — okno tylko pokazuje wynik:

```bash
uv run translator translate "Fate Strange Fake - Vol.8-plain.txt" --gui --backend opencode-go
```

### Co pokazuje okno

- **Widok side-by-side** — lewa kolumna to oryginał EN, prawa tłumaczenie PL,
  ze zsynchronizowanym scrollem i numeracją linii (pipelina wymusza 1:1 linii,
  więc pary linii są na tej samej wysokości).
- **Pasek chunków** — każdy chunk to przycisk z kolorem statusu: szary
  (oczekuje), niebieski pulsujący (generowanie), zielony (gotowy), czerwony
  (błąd). Klik = skok do chunka.
- **Wynik po zakończeniu chunka** — treść pojawia się w całości dopiero po
  ukończeniu generowania (nie streamuje w trakcie), z krótkim rozbłyskiem
  podświetlenia.
- **Panel statystyk** — postęp, ukończone/błędy, linie EN/PL, znaki, tokeny,
  prędkość, średni czas chunka, ETA, plik wyjściowy.
- **Panel błędów** — nieudane chunki z powodem + przycisk *ponów* (per chunk
  lub wszystkie naraz); naprawiony chunk od razu odświeża widok.

### Nawigacja

| Klawisz | Akcja |
|---|---|
| `←` / `→` | poprzedni / następny chunk |
| `PgUp` / `PgDn` | skok o 10 chunków |
| `Home` / `End` | pierwszy / ostatni chunk |
| `Ctrl+S` | zapis oczekujących poprawek bieżącego chunka |

Checkbox **„podążaj za postępem"** (domyślnie włączony) przewiduje widok do
chunka, który właśnie się skończył — ręczny skok go wyłącza.

### Ręczna korekta chunków i scalanie

Panel PL jest **edytowalny dla ukończonych chunków** — możesz poprawiać
tłumaczenie na żywo, podczas gdy kolejne chunki się generują (fokus na panel
wyłącza auto-podążanie, żeby nic nie skoczyło pod kursor).

- **Autozapis** — poprawki zapisują się ~0,8 s po skończeniu pisania, przy
  zmianie chunka, przez `Ctrl+S` i przed scaleniem; do skutku zapisu info-chunk
  pokazuje „• niezapisane".
- **Walidacja 1:1** — po zapisie GUI sprawdza liczbę linii: zgodna → chunk
  dostaje bursztynowy status „edytowany"; rozjazd → chunk wraca na czerwono
  z powodem „edycja: EN x ≠ PL y". Poprawka błędnego chunka czyści jego błąd.
- **Odroczone scalanie** — GUI nie skleja pliku automatycznie. Po ostatnim
  chunku okno pyta „Scalić teraz?", a w sidebarze czeka przycisk
  **„Scal do pliku"** (po scaleniu: „Scal ponownie" — każdy kolejny klik
  uwzględnia najnowsze poprawki).
- **Scalanie z rozjazdami** — jeśli któryś chunk jest czerwony, okno wyświetla
  listę i pyta „scalić mimo to?".
- **Retry vs edycje** — ponowienie chunka edytowanego ręcznie wymaga
  potwierdzenia (nadpisze zmiany); w trakcie retry edycja jest zablokowana.
- **Zamykanie** — przy niescalonych chunkach okno proponuje scalenie przed
  wyjściem, więc nie zostaniesz bez pliku wyjściowego.

### Stop i wznowienie ("pauza")

Silnik zapisuje każdy chunk od razu na dysk (`chunks/chunk_NNN.txt` + świeże
`session.json`), więc tłumaczenie można przerwać w dowolnym momencie
i wznowić później — nawet po zamknięciu programu lub restarcie komputera:

- **⏹ Zatrzymaj** (sidebar, sekcja STEROWANIE) — natychmiast przerywa
  generowanie bieżącego chunka (abort streamu). Przerwany chunk nie zostaje
  zapisany; wznowienie wygeneruje go od zera.
- **Auto-stop po X min** — spinbox w sekcji STEROWANIE; limit sprawdza między
  chunkami (ustawiane przed startem / wznowieniem).
- **Zamknięcie okna** w trakcie tłumaczenia robi łagodny stop — zamiast
  ubijać wątek na twardo, ustawia stop i czeka do 8 s na zapis sesji.
- **Wznowienie po restarcie komputera** — przy uruchomieniu:
  `uv run translator translate <plik> --gui` wykryje niedokończoną sesję
  tego pliku i się zapyta, czy wznowić od miejsca przerwania. W otwartym
  oknie zatrzymanej sesji służy przycisk **„Wznów”** (aktywny po zatrzymaniu
  albo błędzie wątku roboczego).
- **Chunki już gotowe (nawet z błędem)** nie przepisują się przy wznowieniu;
  błędy traktuj dotychczasowo: `retry`, raport `errors.md` lub ręczna poprawka
  w edytorze GUI.
- **Plik wejściowy zmieniony od czasu sesji?** Wznowienie tego pilnuje
  (sha256 oryginału w sesji) i nie pozwoli sklejać rozjechanych chunków.

### Flaga --prompt (edytor prompt.yaml)

`--prompt` przed startem otwiera okno edytora z pełną treścią `prompt.yaml`:

- **Zapisz i kontynuuj** — waliduje YAML i zapisuje plik; zmiany obowiązują
  w tym tłumaczeniu i wszystkich kolejnych,
- **Anuluj** — kończy bez zmian,
- zapis z błędnym YAML-em zostanie odrzucony z komunikatem.

Flaga działa też bez `--gui` (samo okno edytora, dalej tłumaczenie w konsoli).

## Format 1:1 (linijka po linijce)

Źródła `*-plain.txt` są **zdanie-w-linii**, więc silnik wymusza **jedną linię
wyjścia na jedną linię wejścia**:

1. wejście chunku jest numerowane (`1. …`, `2. …`), a model dostaje twarde
   polecenie oddania dokładnie tej samej liczby linii (`line_rule` w prompcie);
2. jeśli liczba linii się rozjedzie, prompt jest powtarzany z informacją zwrotną
   („miałeś N linii, wymagane M”) — `line_retries` w `prompt.yaml`;
3. jeśli to nie pomoże, blok jest dzielony **rekurencyjnie** (aż do pojedynczych
   linii) i składany z powrotem — gwarantuje to zgodność 1:1.

Dzięki temu `uv run translator retry <plik>` sam domyka wadliwe chunki,
a zewnętrzna bramka `tools/fix_errors.py verify <plik>` przechodzi jako `OK`.

**Stitch wyrównany do źródła.** Chunki są cięte na granicach zdań, więc jedna
linia źródła może zostać przecięta na pół. `stitch_chunks()` składa PL linia po
linii względem źródła EN: łączy połówki rozciętych linii spacją i nie dodaje
pustych linii. Liczba niepustych linii wyjścia = liczba niepustych linii wejścia.

> **Naprawiony bug:** `postprocess()` używał wzorca `[^:]*` bez wykluczenia `\n`,
> przez co potrafił zjeść kilka linii aż do najbliższego dwukropka i sam tworzył
> rozjazdy linii. Wzorce są teraz ograniczone do jednej linii, a nagłówek
> `[Fragment N z M]` nie jest wstawiany do tekstu do tłumaczenia.

## Konfiguracja (prompt.yaml)

Edytuj `prompt.yaml` przed pierwszym uruchomieniem:

```yaml
# Wybierz backend
backend: "ollama"         # lub "deepseek"
ollama_model: "gemma3:27b"

# Dla DeepSeek:
# backend: "deepseek"
# deepseek_api_key: "sk-..."   # lub env: DEEPSEEK_API_KEY
```

### Słownik nazw własnych (Glossary)

```yaml
glossary:
  "Demon King": "Król Demonów"
  "Hero": "Bohater"
  "Sword Saint": "Święty Miecza"
```

Model dostanie te tłumaczenia w każdym prompcie i zachowa spójność przez cały plik.

## Zmienne środowiskowe

```bash
export DEEPSEEK_API_KEY="sk-..."
export OLLAMA_URL="http://192.168.1.100:11434"  # zdalne Ollama
export OLLAMA_MODEL="gemma4:26b"
```

## Struktura projektu

```
ln-translator/
├── translator.py      # główny skrypt (CLI + silnik tłumaczenia)
├── gui.py             # okno podglądu tłumaczenia (PySide6) + edytor prompt.yaml
├── prompt.yaml        # konfiguracja promptu i ustawień
├── pyproject.toml     # definicja projektu (uv)
├── README.md
└── output/            # tłumaczenia (tworzone automatycznie)
    └── novel_pl_20250624_143022.txt
```