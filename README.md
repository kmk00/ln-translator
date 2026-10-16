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
# GO Scripts
# ────────────────────────────────────────────────────────

#Convert PDF to TXT (zachowuje formatowanie)
cd scripts/converter
go run main.go sample.pdf output.txt

go run main.go --split sample.pdf output.txt  # split by chapters

```

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
├── translator.py      # główny skrypt
├── prompt.yaml        # konfiguracja promptu i ustawień
├── pyproject.toml     # definicja projektu (uv)
├── README.md
└── output/            # tłumaczenia (tworzone automatycznie)
    └── novel_pl_20250624_143022.txt
```