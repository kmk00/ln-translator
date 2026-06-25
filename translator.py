#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║          LN Translator — Light Novel Translation CLI         ║
║          Ollama / DeepSeek backend | Polish output           ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import os
import time
import re
import json
import argparse
import textwrap
import unicodedata
from pathlib import Path
from datetime import datetime

CHUNK_ERROR_MARKER = "[BŁĄD TŁUMACZENIA — CHUNK"

import yaml
import requests
from dotenv import load_dotenv

# ── ANSI colors ───────────────────────────────────────────────────────────────
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
    WHITE   = "\033[97m"

def c(color: str, text: str) -> str:
    return f"{color}{text}{C.RESET}"

def header():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════════════╗
║          LN Translator v1.1  —  Light Novel → Polski         ║
║          Ollama / DeepSeek  |  Chunked + Refine              ║
╚══════════════════════════════════════════════════════════════╝{C.RESET}
""")

# ── Config loader ─────────────────────────────────────────────────────────────
def load_config(path: str = "prompt.yaml") -> dict:
    env_path = Path(path).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
        load_dotenv(override=False)

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # env > prompt.yaml
    if os.environ.get("DEEPSEEK_API_KEY"):
        cfg["deepseek_api_key"] = os.environ["DEEPSEEK_API_KEY"]
    if os.environ.get("OLLAMA_URL"):
        cfg["ollama_url"] = os.environ["OLLAMA_URL"]
    if os.environ.get("OLLAMA_MODEL"):
        cfg["ollama_model"] = os.environ["OLLAMA_MODEL"]
    if os.environ.get("LN_BACKEND"):
        cfg["backend"] = os.environ["LN_BACKEND"]

    return cfg

# ── Text chunker ──────────────────────────────────────────────────────────────
def split_into_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    paragraphs = text.split("\n")
    chunks = []
    current = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 1

        if current_len + para_len > chunk_size and current:
            chunk_text = "\n".join(current)
            chunks.append(chunk_text)

            if overlap > 0:
                overlap_text = chunk_text[-overlap:]
                last_newline = overlap_text.find("\n")
                if last_newline > 0:
                    overlap_text = overlap_text[last_newline + 1:]
                current = [overlap_text] if overlap_text.strip() else []
                current_len = len(overlap_text)
            else:
                current = []
                current_len = 0

        current.append(para)
        current_len += para_len

    if current:
        chunks.append("\n".join(current))

    return [ch for ch in chunks if ch.strip()]

# ── Prompt builder ────────────────────────────────────────────────────────────
def build_glossary_block(glossary: dict) -> str:
    if not glossary:
        return ""
    lines = ["SŁOWNIK NAZW WŁASNYCH (stosuj konsekwentnie):"]
    for orig, transl in glossary.items():
        lines.append(f'  • "{orig}" → "{transl}"')
    return "\n".join(lines) + "\n"

def build_user_prompt(cfg: dict, text: str, chunk_idx: int, total_chunks: int, prev_translation: str = "") -> str:
    template = cfg["user_prompt_template"]
    additional = cfg.get("additional_info", "").strip()
    glossary_block = build_glossary_block(cfg.get("glossary") or {})

    context_hint = ""
    if prev_translation and chunk_idx > 0:
        prev_lines = prev_translation.strip().split("\n")
        tail = "\n".join(prev_lines[-6:])
        context_hint = (
            f"KONTEKST (ostatnie zdania poprzedniego fragmentu — NIE tłumacz ponownie):\n"
            f"---\n{tail}\n---\n"
        )

    chunk_info = f"[Fragment {chunk_idx + 1} z {total_chunks}]\n" if total_chunks > 1 else ""

    return template.format(
        additional_info=additional,
        glossary_block=glossary_block,
        context_hint=context_hint,
        text=chunk_info + text,
    ).strip()

def build_refine_prompt(cfg: dict, original_en: str, draft_pl: str) -> tuple[str, str]:
    glossary_block = build_glossary_block(cfg.get("glossary") or {})
    additional = cfg.get("additional_info", "").strip()

    system = cfg.get("refine_system_prompt", "").strip()
    template = cfg.get("refine_user_prompt_template", "").strip()

    user = template.format(
        additional_info=additional,
        glossary_block=glossary_block,
        original_en=original_en,
        draft_pl=draft_pl,
    ).strip()

    return system, user

# ── Backends ──────────────────────────────────────────────────────────────────
class OllamaBackend:
    def __init__(self, cfg: dict):
        self.url = cfg["ollama_url"].rstrip("/")
        self.model = cfg["ollama_model"]
        self.temperature = cfg.get("temperature", 0.3)
        self.max_tokens = cfg.get("max_tokens", 4096)

    def check_connection(self) -> bool:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    def generate(self, system_prompt: str, user_prompt: str, on_token=None, temperature: float = None) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "stream": True,
            "options": {
                "temperature": temperature if temperature is not None else self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        response_text = []
        with requests.post(f"{self.url}/api/chat", json=payload, stream=True, timeout=300) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = data.get("message", {}).get("content", "")
                if token:
                    response_text.append(token)
                    if on_token:
                        on_token(token)
                if data.get("done"):
                    break
        return "".join(response_text)


class DeepSeekBackend:
    BASE_URL = "https://api.deepseek.com/v1"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("deepseek_api_key", "")
        self.model = cfg.get("deepseek_model", "deepseek-chat")
        self.temperature = cfg.get("temperature", 0.3)
        self.max_tokens = cfg.get("max_tokens", 4096)

        if not self.api_key:
            raise ValueError(
                "Brak klucza API DeepSeek!\n"
                "Ustaw deepseek_api_key w prompt.yaml lub DEEPSEEK_API_KEY w .env"
            )

    def check_connection(self) -> bool:
        try:
            r = requests.get(
                f"{self.BASE_URL}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=5,
            )
            return r.status_code == 200
        except Exception:
            return False

    def generate(self, system_prompt: str, user_prompt: str, on_token=None, temperature: float = None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "stream": True,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": self.max_tokens,
        }
        response_text = []
        with requests.post(
            f"{self.BASE_URL}/chat/completions",
            headers=headers, json=payload, stream=True, timeout=300
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                if line_str.startswith("data: "):
                    line_str = line_str[6:]
                if line_str == "[DONE]":
                    break
                try:
                    data = json.loads(line_str)
                except json.JSONDecodeError:
                    continue
                token = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if token:
                    response_text.append(token)
                    if on_token:
                        on_token(token)
        return "".join(response_text)


def get_backend(cfg: dict):
    name = cfg.get("backend", "ollama").lower()
    if name == "ollama":
        return OllamaBackend(cfg)
    elif name == "deepseek":
        return DeepSeekBackend(cfg)
    raise ValueError(f"Nieznany backend: {name}")

# ── Progress display ──────────────────────────────────────────────────────────
def progress_bar(current: int, total: int, width: int = 40) -> str:
    if total == 0:
        return ""
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {int(100 * current / total)}%"

class StreamPrinter:
    def __init__(self, max_width: int = 100):
        self.buffer = ""
        self.line_len = 0
        self.max_width = max_width
        self.token_count = 0

    def on_token(self, token: str):
        self.token_count += 1
        self.buffer += token
        for ch in token:
            if ch == "\n":
                print(f"{C.RESET}")
                self.line_len = 0
            else:
                if self.line_len == 0:
                    print(f"  {C.DIM}", end="")
                print(ch, end="", flush=True)
                self.line_len += 1
                if self.line_len >= self.max_width:
                    print(f"{C.RESET}")
                    self.line_len = 0
        print(C.RESET, end="", flush=True)

    def finish(self):
        if self.line_len > 0:
            print(C.RESET)
        print()

# ── Post-processing ───────────────────────────────────────────────────────────
def detect_foreign_language(text: str) -> list[tuple[int, int, str]]:
    issues = []
    pattern = re.compile(r'[\u3000-\u9FFF\uF900-\uFAFF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]+')
    for m in pattern.finditer(text):
        issues.append((m.start(), m.end(), m.group()))
    return issues

def detect_untranslated_english(text: str) -> list[tuple[int, int, str]]:
    issues = []
    for i, line in enumerate(text.split("\n")):
        stripped = line.strip()
        if not stripped:
            continue
        words = stripped.split()
        if len(words) < 3:
            continue
        ascii_words = sum(1 for w in words if all(ord(c) < 128 for c in re.sub(r'[^\w]', '', w)))
        ratio = ascii_words / len(words)
        if ratio > 0.80 and not re.match(r'^[═─=\-\*\s\d]+$', stripped):
            issues.append((i, stripped))
    return issues

def detect_duplicates(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    seen = {}
    dupes = []
    for p in paragraphs:
        if p in seen:
            dupes.append(p[:80] + "..." if len(p) > 80 else p)
        seen[p] = True
    return dupes

def run_diagnostics(text: str, verbose: bool = True) -> dict:
    foreign = detect_foreign_language(text)
    english = detect_untranslated_english(text)
    dupes = detect_duplicates(text)

    issues = {
        "foreign_chars": foreign,
        "untranslated_lines": english,
        "duplicate_paragraphs": dupes,
        "total": len(foreign) + len(english) + len(dupes),
    }

    if verbose and issues["total"] > 0:
        print(f"\n  {c(C.YELLOW, '⚠ Wykryte problemy w tłumaczeniu:')}")
        if foreign:
            print(f"    {c(C.RED, f'• Obce znaki (np. chiński/japoński): {len(foreign)} fragment(ów)')}")
        if english:
            print(f"    {c(C.RED, f'• Nieprzetłumaczone linie EN: {len(english)}')}")
        if dupes:
            print(f"    {c(C.YELLOW, f'• Zduplikowane akapity: {len(dupes)}')}")
        print()

    return issues

def postprocess(text: str) -> str:
    meta_patterns = [
        r"^(Oto|Oczywiście|Proszę|Poniżej)[^:]*:\s*",
        r"^(Here is|Here's)[^:]*:\s*",
        r"^Translation:\s*",
        r"^Tłumaczenie:\s*",
        r"^Korekta:\s*",
        r"^---+\s*",
        r"\s*---+\s*$",
        r"^═+\s*",
        r"\s*═+\s*$",
        r"^\[Fragment \d+ z \d+\]\s*",
        r"^—Koniec fragmentu.*$",
    ]
    for pat in meta_patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.MULTILINE)

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def stitch_chunks(chunks: list[str], overlap: int) -> str:
    if not chunks:
        return ""
    if len(chunks) == 1:
        return chunks[0]

    result = chunks[0]
    for chunk in chunks[1:]:
        if overlap > 0 and len(result) > overlap:
            tail = result[-overlap * 2:]
            last_para = tail.rfind("\n\n")
            if last_para > 0:
                result = result[:-(overlap * 2) + last_para + 2]
        result = result.rstrip() + "\n\n" + chunk.lstrip()

    return result

# ── Statystyki ────────────────────────────────────────────────────────────────
def print_stats(input_chars, output_chars, elapsed, chunk_count, total_tokens, label="Statystyki tłumaczenia"):
    ratio = output_chars / input_chars if input_chars else 0
    chars_per_sec = output_chars / elapsed if elapsed else 0

    print(f"\n{C.CYAN}{'─' * 62}{C.RESET}")
    print(f"{C.BOLD}  {label}:{C.RESET}")
    print(f"  {'Fragmenty (chunki):':<30} {c(C.GREEN, str(chunk_count))}")
    print(f"  {'Znaki wejściowe:':<30} {c(C.YELLOW, f'{input_chars:,}')}")
    print(f"  {'Znaki wyjściowe:':<30} {c(C.YELLOW, f'{output_chars:,}')}")
    print(f"  {'Stosunek PL/EN:':<30} {c(C.CYAN, f'{ratio:.2f}x')}")
    print(f"  {'Czas:':<30} {c(C.GREEN, f'{elapsed:.1f}s')}")
    print(f"  {'Znaki/sek:':<30} {c(C.GREEN, f'{chars_per_sec:.0f}')}")
    print(f"  {'Tokeny (przybliżone):':<30} {c(C.MAGENTA, f'~{total_tokens:,}')}")
    print(f"{C.CYAN}{'─' * 62}{C.RESET}\n")

# ── Retry wrapper ─────────────────────────────────────────────────────────────
def generate_with_retry(backend, system_prompt, user_prompt, cfg, on_token=None, temperature=None) -> str:
    max_retries = cfg.get("max_retries", 3)
    retry_delay = cfg.get("retry_delay", 5)

    for attempt in range(1, max_retries + 1):
        try:
            return backend.generate(system_prompt, user_prompt, on_token, temperature)
        except requests.exceptions.Timeout:
            print(c(C.YELLOW, f"\n  ⚠ Timeout (próba {attempt}/{max_retries})"))
        except requests.exceptions.HTTPError as e:
            print(c(C.RED, f"\n  ✗ HTTP {e.response.status_code} (próba {attempt}/{max_retries})"))
        except Exception as e:
            print(c(C.RED, f"\n  ✗ {e} (próba {attempt}/{max_retries})"))

        if attempt < max_retries:
            print(c(C.DIM, f"  Czekam {retry_delay}s..."))
            time.sleep(retry_delay)

    return ""  

# ── Session & Retry Functions ─────────────────────────────────────────────────
def load_session(output_path: str) -> dict:
    """Wczytuje metadane sesji z katalogu chunks."""
    out = Path(output_path)
    chunks_dir = out.parent / f"{out.stem}_chunks"
    session_file = chunks_dir / "session.json"
    if not session_file.exists():
        print(c(C.RED, f"✗ Brak pliku sesji: {session_file}"))
        sys.exit(1)
    return json.loads(session_file.read_text(encoding="utf-8"))

def detect_error_chunks(output_path: str) -> list[int]:
    """Wykrywa błędne chunki na podstawie pliku sesji i obecności markerów w tekście."""
    out = Path(output_path)
    chunks_dir = out.parent / f"{out.stem}_chunks"
    if not chunks_dir.exists():
        return []

    failed = set()
    session_file = chunks_dir / "session.json"
    if session_file.exists():
        meta = json.loads(session_file.read_text(encoding="utf-8"))
        for n in meta.get("failed_chunks", []):
            failed.add(n)

    for chunk_file in sorted(chunks_dir.glob("chunk_*.txt")):
        text = chunk_file.read_text(encoding="utf-8")
        if CHUNK_ERROR_MARKER in text:
            try:
                n = int(chunk_file.stem.split("_")[1])
                failed.add(n)
            except (IndexError, ValueError):
                pass
    return sorted(failed)

def retry_chunks(output_path: str, chunk_numbers: list[int], cfg: dict, verbose: bool = True) -> None:
    """Regeneruje wskazane chunki i scala je z powrotem do pliku wyjściowego."""
    out = Path(output_path)
    if not out.exists():
        print(c(C.RED, f"✗ Plik wyjściowy nie istnieje: {output_path}"))
        sys.exit(1)

    meta = load_session(output_path)
    chunks_dir = Path(meta["chunks_dir"])
    orig_dir = chunks_dir / "original"
    total_chunks = meta["total_chunks"]
    overlap = meta["overlap"]

    invalid = [n for n in chunk_numbers if n < 1 or n > total_chunks]
    if invalid:
        print(c(C.RED, f"✗ Nieprawidłowe numery chunków: {invalid} (zakres: 1–{total_chunks})"))
        sys.exit(1)

    backend = get_backend(cfg)
    system_prompt = cfg["system_prompt"].strip()

    if verbose:
        print(f"  {c(C.CYAN, '🔄 Regeneracja chunków:')} {c(C.WHITE, str(chunk_numbers))}")

    print(f"  {c(C.DIM, 'Sprawdzam połączenie...')}", end=" ", flush=True)
    if backend.check_connection():
        print(c(C.GREEN, "✓ OK"))
    else:
        print(c(C.RED, "✗ BŁĄD"))
        sys.exit(1)

    print()
    start_time = time.time()
    total_tokens = 0
    successfully_retried = []

    for chunk_num in chunk_numbers:
        orig_file = orig_dir / f"chunk_{chunk_num:03d}.txt"
        if not orig_file.exists():
            print(c(C.RED, f"  ✗ Brak oryginału EN dla chunk {chunk_num}: {orig_file}"))
            continue

        orig_chunk = orig_file.read_text(encoding="utf-8")
        prev_translation = ""
        if chunk_num > 1:
            prev_file = chunks_dir / f"chunk_{chunk_num-1:03d}.txt"
            if prev_file.exists():
                prev_text = prev_file.read_text(encoding="utf-8")
                prev_lines = prev_text.strip().split("\n")
                prev_translation = "\n".join(prev_lines[-6:])

        print(f"{C.BOLD}{C.YELLOW}┌─ Regeneracja chunk {chunk_num}/{total_chunks} "
              f"{'─' * max(0, 40 - len(str(chunk_num)) - len(str(total_chunks)))}┐{C.RESET}")
        print(f"{C.YELLOW}│{C.RESET}  {c(C.DIM, f'Rozmiar: {len(orig_chunk):,} znaków')}")
        print(f"{C.YELLOW}└{'─' * 54}┘{C.RESET}\n")

        user_prompt = build_user_prompt(cfg, orig_chunk, chunk_num - 1, total_chunks, prev_translation)

        printer = StreamPrinter()
        result = generate_with_retry(backend, system_prompt, user_prompt, cfg, printer.on_token)
        printer.finish()
        total_tokens += printer.token_count

        if not result:
            print(c(C.RED, f"  ✗ Regeneracja chunk {chunk_num} nieudana — zachowuję stary"))
            continue

        result = postprocess(result)
        
        # Sprawdzanie ilości linii
        orig_lines = len([l for l in orig_chunk.split('\n') if l.strip()])
        trans_lines = len([l for l in result.split('\n') if l.strip()])
        
        if orig_lines != trans_lines:
            print(c(C.RED, f"  ✗ Błąd regeneracji! Niezgodność liczby linii (EN: {orig_lines}, PL: {trans_lines})"))
            result = f"{CHUNK_ERROR_MARKER} {chunk_num}]\n\n{result}"
            chunk_file = chunks_dir / f"chunk_{chunk_num:03d}.txt"
            chunk_file.write_text(result, encoding="utf-8")
            continue

        chunk_file = chunks_dir / f"chunk_{chunk_num:03d}.txt"
        chunk_file.write_text(result, encoding="utf-8")
        print(f"  {c(C.GREEN, '✓')} Chunk {chunk_num} zaktualizowany\n")
        successfully_retried.append(chunk_num)

    all_chunks = []
    for i in range(1, total_chunks + 1):
        cf = chunks_dir / f"chunk_{i:03d}.txt"
        if cf.exists():
            all_chunks.append(cf.read_text(encoding="utf-8"))

    if all_chunks:
        final_text = stitch_chunks(all_chunks, overlap)
        out.write_text(final_text, encoding="utf-8")

        # Usuń z listy "failed_chunks" tylko te chunki, które się faktycznie naprawiły
        meta["failed_chunks"] = [n for n in meta.get("failed_chunks", []) if n not in successfully_retried]
        (chunks_dir / "session.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"{C.GREEN}{C.BOLD}╔══════════════════════════════════════════════════╗")
        print(f"║  ✓ Regeneracja zakończona!                       ║")
        print(f"╚══════════════════════════════════════════════════╝{C.RESET}")
        print(f"\n  {c(C.CYAN, '💾 Zaktualizowano:')} {c(C.WHITE, output_path)}")

# ── Core translation pipeline ─────────────────────────────────────────────────
def translate_file(input_path: str, cfg: dict, output_path: str = None, dry_run: bool = False, verbose: bool = True) -> str:
    path = Path(input_path)
    if not path.exists():
        print(c(C.RED, f"✗ Plik nie istnieje: {input_path}"))
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    input_chars = len(text)

    if verbose:
        print(f"  {c(C.CYAN, '📖 Plik:')} {c(C.WHITE, str(path.resolve()))}")
        print(f"  {c(C.CYAN, '📏 Rozmiar:')} {c(C.YELLOW, f'{input_chars:,} znaków')}")

    backend = get_backend(cfg)
    backend_name = cfg.get("backend", "ollama")

    if not dry_run:
        print(f"\n  {c(C.DIM, 'Sprawdzam połączenie...')}", end=" ", flush=True)
        if backend.check_connection():
            print(c(C.GREEN, "✓ OK"))
        else:
            print(c(C.RED, "✗ BŁĄD"))
            sys.exit(1)

    chunk_size = cfg.get("chunk_size", 2500)
    overlap = cfg.get("chunk_overlap", 200)
    chunks = split_into_chunks(text, chunk_size, overlap)
    total_chunks = len(chunks)

    if dry_run:
        print(f"\n{c(C.YELLOW, '  [DRY RUN] Nie wysyłam zapytań do modelu.')}")
        return ""

    if not output_path:
        out_dir = Path(cfg.get("output_dir", "output"))
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = cfg.get("output_format", "txt")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(out_dir / f"{path.stem}_pl_{ts}.{ext}")

    chunks_dir = Path(output_path).parent / f"{Path(output_path).stem}_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    orig_chunks_dir = chunks_dir / "original"
    orig_chunks_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = cfg["system_prompt"].strip()
    translated_chunks = []
    prev_translation = ""
    total_tokens = 0
    failed_chunks = []
    start_time = time.time()

    print()

    for i, chunk in enumerate(chunks):
        elapsed = time.time() - start_time
        eta = (elapsed / (i + 0.001)) * (total_chunks - i) if i > 0 else 0

        print(f"{C.BOLD}{C.CYAN}┌─ Fragment {i+1}/{total_chunks} "
              f"{'─' * max(0, 50 - len(str(i+1)) - len(str(total_chunks)))}┐{C.RESET}")
        print(f"{C.CYAN}│{C.RESET}  {progress_bar(i, total_chunks, 38)}  "
              f"{c(C.DIM, f'ETA: {int(eta)}s') if eta > 0 else ''}")
        print(f"{C.CYAN}└{'─' * 54}┘{C.RESET}\n")

        # Zapisanie oryginału (wymagane do ponawiania)
        (orig_chunks_dir / f"chunk_{i+1:03d}.txt").write_text(chunk, encoding="utf-8")

        user_prompt = build_user_prompt(cfg, chunk, i, total_chunks, prev_translation)

        printer = StreamPrinter()
        result = generate_with_retry(backend, system_prompt, user_prompt, cfg, printer.on_token)
        printer.finish()
        total_tokens += printer.token_count

        chunk_failed = False
        if not result:
            print(c(C.RED, f"  ✗ Błąd! Chunk {i+1} się nie powiódł (pusta odpowiedź)."))
            result = f"{CHUNK_ERROR_MARKER} {i+1}]\n\n{chunk}"
            failed_chunks.append(i+1)
            chunk_failed = True
        else:
            result = postprocess(result)
            
            # Sprawdzanie ilości linii (ignorując całkowicie puste akapity)
            orig_lines = len([l for l in chunk.split('\n') if l.strip()])
            trans_lines = len([l for l in result.split('\n') if l.strip()])
            
            if orig_lines != trans_lines:
                print(c(C.RED, f"  ✗ Błąd! Niezgodność liczby linii (EN: {orig_lines}, PL: {trans_lines})"))
                # Doklejamy marker z błędem, ale zostawiamy też wygenerowany tekst
                result = f"{CHUNK_ERROR_MARKER} {i+1}]\n\n{result}"
                failed_chunks.append(i+1)
                chunk_failed = True

        translated_chunks.append(result)
        prev_translation = result

        # Zapisanie wyniku PL
        (chunks_dir / f"chunk_{i+1:03d}.txt").write_text(result, encoding="utf-8")

        status_icon = c(C.RED, "✗") if chunk_failed else c(C.GREEN, "✓")
        print(f"  {status_icon} Fragment {i+1}/{total_chunks} ukończony\n")

    final_text = stitch_chunks(translated_chunks, overlap)
    output_chars = len(final_text)
    total_elapsed = time.time() - start_time

    Path(output_path).write_text(final_text, encoding="utf-8")

    # Zapis sesji (wymagany do ponawiania)
    meta = {
        "input_path": str(path.resolve()),
        "output_path": str(Path(output_path).resolve()),
        "chunks_dir": str(chunks_dir.resolve()),
        "chunk_size": chunk_size,
        "overlap": overlap,
        "total_chunks": total_chunks,
        "failed_chunks": failed_chunks,
    }
    (chunks_dir / "session.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{C.GREEN}{C.BOLD}╔══════════════════════════════════════════════════╗")
    print(f"║  ✓ Tłumaczenie zakończone!                       ║")
    print(f"╚══════════════════════════════════════════════════╝{C.RESET}")
    print(f"\n  {c(C.CYAN, '💾 Zapisano:')} {c(C.WHITE, output_path)}")

    # Opcja autozregenerowania
    if failed_chunks:
        print(f"\n  {c(C.RED, C.BOLD + '⚠ UWAGA! Niektóre fragmenty zawierają błędy/ucięcia tekstu:' + C.RESET)}")
        for n in failed_chunks:
            print(f"    {c(C.RED, f'• Chunk {n}')}")
        try:
            answer = input(f"\n  {c(C.YELLOW, '→ Czy chcesz teraz spróbować zregenerować te chunki? [t/N]: ')}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer in ("t", "tak", "y", "yes"):
            retry_chunks(output_path, failed_chunks, cfg, verbose)
        else:
            print(c(C.DIM, f"\n  Możesz to zrobić później używając komendy:"))
            print(c(C.DIM, f"  uv run translator retry {output_path}"))

    print_stats(input_chars, output_chars, total_elapsed, total_chunks, total_tokens)
    return output_path

# ── Refine pipeline ───────────────────────────────────────────────────────────
def refine_file(draft_path: str, original_path: str = None, cfg: dict = None, output_path: str = None, verbose: bool = True) -> str:
    draft_path = Path(draft_path)
    if not draft_path.exists():
        sys.exit(1)
    draft_text = draft_path.read_text(encoding="utf-8")

    original_text = ""
    if original_path and Path(original_path).exists():
        original_text = Path(original_path).read_text(encoding="utf-8")

    backend = get_backend(cfg)
    if not backend.check_connection():
        sys.exit(1)

    refine_chunk_size = cfg.get("refine_chunk_size", cfg.get("chunk_size", 2500) * 2)
    overlap = cfg.get("chunk_overlap", 200)

    draft_chunks = split_into_chunks(draft_text, refine_chunk_size, overlap)
    total_chunks = len(draft_chunks)

    if original_text:
        orig_chunks = split_into_chunks(original_text, refine_chunk_size, overlap)
        while len(orig_chunks) < len(draft_chunks): orig_chunks.append("")
        orig_chunks = orig_chunks[:len(draft_chunks)]
    else:
        orig_chunks = [""] * total_chunks

    if not output_path:
        out_dir = Path(cfg.get("output_dir", "output"))
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = draft_path.stem.replace("_pl_", "_").split("_")[0]
        output_path = str(out_dir / f"{stem}_pl_refined_{ts}.txt")

    refine_system = cfg.get("refine_system_prompt", "").strip()
    refine_template = cfg.get("refine_user_prompt_template", "").strip()
    refine_temp = cfg.get("refine_temperature", 0.2)

    refined_chunks = []
    
    for i, (draft_chunk, orig_chunk) in enumerate(zip(draft_chunks, orig_chunks)):
        print(f"  {c(C.DIM, f'Korekta chunk {i+1}/{total_chunks}...')}")
        user_prompt = refine_template.format(
            additional_info=cfg.get("additional_info", "").strip(),
            glossary_block=build_glossary_block(cfg.get("glossary") or {}),
            original_en=orig_chunk if orig_chunk else "(brak oryginału)",
            draft_pl=draft_chunk,
        ).strip()

        printer = StreamPrinter()
        result = generate_with_retry(backend, refine_system, user_prompt, cfg, printer.on_token, temperature=refine_temp)
        printer.finish()

        if not result:
            result = draft_chunk
        refined_chunks.append(postprocess(result))

    final_text = stitch_chunks(refined_chunks, overlap)
    Path(output_path).write_text(final_text, encoding="utf-8")
    print(f"\n  {c(C.CYAN, '💾 Zapisano refine:')} {c(C.WHITE, output_path)}")
    return output_path

# ── CLI handlers ──────────────────────────────────────────────────────────────
def cmd_translate(args):
    cfg = load_config(args.config)
    if args.model: cfg["ollama_model"] = args.model
    if args.backend: cfg["backend"] = args.backend
    if args.chunk_size: cfg["chunk_size"] = args.chunk_size
    if args.temperature is not None: cfg["temperature"] = args.temperature

    out = translate_file(args.input, cfg, args.output, args.dry_run, not args.quiet)

    if args.refine and out:
        refine_file(out, args.input, cfg, verbose=not args.quiet)

def cmd_refine(args):
    cfg = load_config(args.config)
    if args.model: cfg["ollama_model"] = args.model
    refine_file(args.draft, args.original, cfg, args.output, not args.quiet)

def cmd_diagnose(args):
    path = Path(args.file)
    if not path.exists(): sys.exit(1)
    run_diagnostics(path.read_text(encoding="utf-8"), True)

def cmd_info(args):
    cfg = load_config(args.config)
    print(f"Backend: {cfg.get('backend', 'ollama')}")

def cmd_retry(args):
    cfg = load_config(args.config)
    if args.model: cfg["ollama_model"] = args.model
    if args.backend: cfg["backend"] = args.backend

    # Znajdź chunki
    if args.chunks:
        chunks_to_retry = args.chunks
    else:
        chunks_to_retry = detect_error_chunks(args.output)
        if not chunks_to_retry:
            print(c(C.GREEN, f"✓ Brak błędnych chunków do zregenerowania w: {args.output}"))
            return

    retry_chunks(args.output, chunks_to_retry, cfg, verbose=True)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    header()

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", "-c", default="prompt.yaml")
    sub = parser.add_subparsers(dest="command")

    p_t = sub.add_parser("translate", aliases=["t"])
    p_t.add_argument("input")
    p_t.add_argument("-o", "--output", default=None)
    p_t.add_argument("--model", "-m", default=None)
    p_t.add_argument("--backend", "-b", choices=["ollama", "deepseek"], default=None)
    p_t.add_argument("--chunk-size", type=int, default=None)
    p_t.add_argument("--temperature", type=float, default=None)
    p_t.add_argument("--dry-run", action="store_true")
    p_t.add_argument("--refine", action="store_true")
    p_t.add_argument("-q", "--quiet", action="store_true")
    p_t.set_defaults(func=cmd_translate)

    p_r = sub.add_parser("refine", aliases=["r"])
    p_r.add_argument("draft")
    p_r.add_argument("-e", "--original", default=None)
    p_r.add_argument("-o", "--output", default=None)
    p_r.add_argument("--model", "-m", default=None)
    p_r.add_argument("--backend", "-b", choices=["ollama", "deepseek"], default=None)
    p_r.add_argument("-q", "--quiet", action="store_true")
    p_r.set_defaults(func=cmd_refine)

    p_d = sub.add_parser("diagnose", aliases=["d"])
    p_d.add_argument("file")
    p_d.set_defaults(func=cmd_diagnose)

    p_i = sub.add_parser("info", aliases=["i"])
    p_i.set_defaults(func=cmd_info)

    # Nowa komenda RETRY
    p_retry = sub.add_parser("retry")
    p_retry.add_argument("output", help="Wygenerowany plik PL (żeby połączyć wygenerowane chunki)")
    p_retry.add_argument("--chunks", type=int, nargs="+", help="Wybierz konkretne numery do naprawy (np. --chunks 6 8)")
    p_retry.add_argument("--model", "-m", default=None)
    p_retry.add_argument("--backend", "-b", choices=["ollama", "deepseek"], default=None)
    p_retry.set_defaults(func=cmd_retry)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)

if __name__ == "__main__":
    main()