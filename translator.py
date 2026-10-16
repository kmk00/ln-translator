#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║          LN Translator — Light Novel Translation CLI         ║
║          Ollama / DeepSeek backend | Polish output           ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import uuid
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
    if os.environ.get("OPENCODE_API_KEY"):
        cfg["opencode_go_api_key"] = os.environ["OPENCODE_API_KEY"]
    if os.environ.get("OLLAMA_URL"):
        cfg["ollama_url"] = os.environ["OLLAMA_URL"]
    if os.environ.get("OLLAMA_MODEL"):
        cfg["ollama_model"] = os.environ["OLLAMA_MODEL"]
    if os.environ.get("LN_BACKEND"):
        cfg["backend"] = os.environ["LN_BACKEND"]
    if os.environ.get("LN_REASONING_EFFORT"):
        cfg["reasoning_effort"] = os.environ["LN_REASONING_EFFORT"]

    return cfg

# ── Text chunker ──────────────────────────────────────────────────────────────
import bisect as _bisect

_ABBREVS = {
    "np", "itd", "itp", "tzn", "tj", "tzw", "ok", "ul", "al", "dr", "prof",
    "mgr", "hab", "pkt", "str", "jw", "nr", "godz", "pol", "ang", "min", "maks",
}

_SENT_END_RE = re.compile(r'([.!?…])([”"»\')\]]*)(?=\s|$)')

def _is_sentence_cut(text: str, end: int) -> bool:
    """Czy pozycja `end` (excl., tuż za kropką/cudzysłowem) to prawdziwy koniec zdania."""
    tokens = text[:end].split()
    if not tokens:
        return False
    token = tokens[-1].strip('”"»\')].,!?…')
    low = token.lower()
    if low in _ABBREVS:          # skróty: "np.", "itd."
        return False
    if len(token) == 1 and token.isupper():   # inicjały: "J."
        return False
    if token.isdigit() and len(token) <= 2:   # numeracja: "5."
        return False
    return True

def _sentence_cut_points(text: str) -> list[int]:
    """Pozycje (excl.), gdzie można bezpiecznie uciąć — konce zdań."""
    return [m.end() for m in _SENT_END_RE.finditer(text) if _is_sentence_cut(text, m.end())]

def split_into_chunks(text: str, chunk_size: int, overlap: int = 0) -> list[str]:
    """
    Dzieli tekst na ciągłe spojne fragmenty bez zmiany treści ("".join == oryginał).
    - granice padają na pełne zdania (preferowana granica akapitu)
    - overlap ignorowany w treści (ciągłość: 'prev_translation' w build_user_prompt)
    - awaryjnie (brak zdania w oknie): ostatnia granica akapitu w oknie
    """
    if chunk_size <= 0:
        chunk_size = 2500

    n = len(text)
    cuts = _sentence_cut_points(text)
    chunks = []
    prev = 0
    while prev < n:
        k = _bisect.bisect_right(cuts, prev)
        best = None
        for j in range(k, len(cuts)):
            if cuts[j] > prev + chunk_size:
                break
            best = cuts[j]
        if best is None:
            # okno bez ani jednego końca zdania: tnij po ostatnim \n w oknie
            window_end = min(prev + chunk_size, n)
            nl = text.rfind("\n", prev, window_end)
            best = nl + 1 if nl > prev else window_end
        chunk = text[prev:best]
        # puste chunki (czysto białe) dołącz do poprzedniego — zachowujemy znak-for-znak
        if not chunk.strip() and chunks:
            chunks[-1] += chunk
        else:
            chunks.append(chunk)
        prev = best
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

    line_rule, n_lines = build_line_rule(text)
    body = number_lines(text) if n_lines > 1 else text
    if chunk_info:
        line_rule = chunk_info + line_rule

    class _SafeDict(dict):
        def __missing__(self, key):  # backward-compatible with older prompt.yaml
            return ""

    return template.format_map(_SafeDict(
        additional_info=additional,
        glossary_block=glossary_block,
        context_hint=context_hint,
        line_rule=line_rule,
        text=body,
    )).strip()


def _translate_block(backend, system_prompt, cfg, chunk, chunk_idx, total_chunks,
                     prev_translation, on_token, line_retries):
    """One generation pass + up to `line_retries` format-correction re-asks."""
    orig_lines = count_lines(chunk)
    user_prompt = build_user_prompt(cfg, chunk, chunk_idx, total_chunks, prev_translation)
    result = generate_with_retry(backend, system_prompt, user_prompt, cfg, on_token)
    result = postprocess(result) if result else result
    attempt = 0
    while result and orig_lines > 1:
        got = count_lines(result)
        if got == orig_lines or attempt >= line_retries:
            break
        attempt += 1
        fix_prompt = (
            user_prompt
            + f"\n\nPOPRAWKA FORMATU: poprzednia próba miała {got} linii, a wymagane jest "
              f"DOKŁADNIE {orig_lines}. Zwróć ponownie CAŁY przetłumaczony fragment jako "
              f"dokładnie {orig_lines} linii (\"1. …\" … \"{orig_lines}. …\"), "
              f"bez łączenia i dzielenia linii."
        )
        result = generate_with_retry(backend, system_prompt, fix_prompt, cfg, on_token)
        result = postprocess(result) if result else result
    return result


def generate_translation(backend, system_prompt, cfg, chunk, chunk_idx, total_chunks,
                         prev_translation, on_token=None, line_retries=None, depth=0):
    """Generate a translation that is guaranteed 1:1 with the input lines.

    Strategy: ask for a numbered, line-by-line translation; if the model still
    merges/splits lines, re-ask with explicit feedback; if it still fails, split
    the block in half and translate the halves recursively (down to single lines).
    Returns (result, format_corrections_used).
    """
    if line_retries is None:
        line_retries = int(cfg.get("line_retries", 2))
    orig_lines = count_lines(chunk)
    result = _translate_block(backend, system_prompt, cfg, chunk, chunk_idx,
                              total_chunks, prev_translation, on_token, line_retries)
    if not result or orig_lines <= 1 or count_lines(result) == orig_lines or depth >= 6:
        return result, 0

    lines = [l for l in chunk.split("\n") if l.strip()]
    if len(lines) < 2:
        return result, 0
    mid = len(lines) // 2
    left, _ = generate_translation(backend, system_prompt, cfg, "\n".join(lines[:mid]),
                                   chunk_idx, total_chunks, prev_translation, on_token,
                                   line_retries, depth + 1)
    right, _ = generate_translation(backend, system_prompt, cfg, "\n".join(lines[mid:]),
                                    chunk_idx, total_chunks, left or prev_translation,
                                    on_token, line_retries, depth + 1)
    merged = "\n".join(p for p in ((left or "").strip(), (right or "").strip()) if p)
    return merged, depth + 1

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
            "think": False,  # modele rozumujące (np. abliterated) palą cały budżet na thinking
            "options": {
                "temperature": temperature if temperature is not None else self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        response_text = []
        with requests.post(f"{self.url}/api/chat", json=payload, stream=True, timeout=300) as resp:
            if resp.status_code == 400 and b"does not support thinking" in (resp.content or b""):
                payload.pop("think")
            else:
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


class OpenCodeGoBackend:
    BASE_URL = "https://opencode.ai/zen/go/v1"

    def __init__(self, cfg: dict):
        self.api_key = cfg.get("opencode_go_api_key", "")
        self.model = cfg.get("opencode_go_model", "glm-5.3-flash")
        self.temperature = cfg.get("temperature", 0.3)
        self.max_tokens = cfg.get("max_tokens", 4096)
        self.reasoning_effort = (cfg.get("reasoning_effort") or "").strip().lower()
        self.session_id = str(uuid.uuid4())

        if not self.api_key:
            raise ValueError(
                "Brak klucza API OpenCode Go!\n"
                "Ustaw opencode_go_api_key w prompt.yaml lub OPENCODE_API_KEY w .env"
            )

    def check_connection(self) -> bool:
        try:
            r = requests.get(
                f"{self.BASE_URL}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=5,
            )
            if r.status_code == 401:
                print(c(C.RED, "\n  ✗ OpenCode Go: nieprawidłowy klucz API — skopiuj świeży z https://opencode.ai/auth"))
                return False
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            r = requests.get(
                f"{self.BASE_URL}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=5,
            )
            data = r.json()
            return [m["id"] for m in data.get("data", data if isinstance(data, list) else []) if isinstance(m, dict) and m.get("id")]
        except Exception:
            return []

    def generate(self, system_prompt: str, user_prompt: str, on_token=None, temperature: float = None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "x-opencode-session": self.session_id,
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
        # GLM-5.3 ma wymuszony thinking (nie da się wyłączyć) — kontrola tylko przez effort
        if self.reasoning_effort in ("low", "medium", "high", "max"):
            payload["reasoning_effort"] = self.reasoning_effort

        response_text = []
        reasoning_tokens = 0
        finish_reason = None
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
                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                # reasoning (thinking) streamowany osobno — liczymy, żeby umieć zdiagnozować puste odpowiedzi
                if delta.get("reasoning_content"):
                    reasoning_tokens += 1
                token = delta.get("content") or ""
                if token:
                    response_text.append(token)
                    if on_token:
                        on_token(token)
                fr = choices[0].get("finish_reason")
                if fr:
                    finish_reason = fr

        if not response_text:
            raise RuntimeError(
                f"pusta odpowiedź — thinking zjadł ~{reasoning_tokens} tokenów, "
                f"finish_reason={finish_reason or '?'}, "
                f"reasoning_effort={self.reasoning_effort or 'domyślny serwera (max)'}"
            )
        return "".join(response_text)


def get_backend(cfg: dict):
    name = cfg.get("backend", "ollama").lower()
    if name == "ollama":
        return OllamaBackend(cfg)
    elif name == "deepseek":
        return DeepSeekBackend(cfg)
    elif name in ("opencode-go", "opencode_go", "go"):
        return OpenCodeGoBackend(cfg)
    raise ValueError(f"Nieznany backend: {name}")

# ── Progress display ──────────────────────────────────────────────────────────
def progress_bar(current: int, total: int, width: int = 40) -> str:
    if total == 0:
        return ""
    filled = int(width * current / total)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {int(100 * current / total)}%"

class StreamPrinter:
    PREVIEW_LIMIT = 400  # znaki streamowane na żywo; dalej licznik

    def __init__(self, max_width: int = 96):
        self.buffer = ""
        self.line_len = 0
        self.max_width = max_width
        self.token_count = 0
        self.shown = 0
        self.collapsed = False

    def on_token(self, token: str):
        self.token_count += 1
        self.buffer += token
        for ch in token:
            if self.shown >= self.PREVIEW_LIMIT and not self.collapsed:
                print(f"{C.DIM} … (reszta streamu skrócona)", end="", flush=True)
                self.collapsed = True
            if self.collapsed:
                continue
            self.shown += 1
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

    def finish(self, label: str = None):
        if self.collapsed:
            print(f"  {C.DIM}… pełny tekst: {label or 'chunk file'}{C.RESET}")
        elif self.line_len > 0:
            print(C.RESET)
        print()


def line_mismatch_report(orig_chunk: str, result: str, max_lines: int = 2) -> list[str]:
    """
    Raport gdzie dokładnie różni się liczba linii: lokalizuje pierwszy akapit
    (rozdzielony pustą linią), w którym liczby linii EN/PL rozjeżdżają się.
    """
    import difflib
    en_paras = [p for p in orig_chunk.split("\n\n") if p.strip()]
    pl_paras = [p for p in result.split("\n\n") if p.strip()]
    en_lines = lambda p: [l for l in p.split("\n") if l.strip()]
    report = []

    if len(en_paras) == len(pl_paras):
        for j, (pe, pp) in enumerate(zip(en_paras, pl_paras)):
            ne, np_ = len(en_lines(pe)), len(en_lines(pp))
            if ne != np_:
                report.append(c(C.DIM, f"    ─ pierwszy rozjazd w akapicie #{j+1}: EN {ne} linii vs PL {np_}"))
                for line in en_lines(pe)[:max_lines]:
                    report.append(f"    {c(C.RED, 'EN')} {line.strip()[:88]}")
                for line in en_lines(pp)[:max_lines]:
                    report.append(f"    {c(C.GREEN, 'PL')} {line.strip()[:88]}")
                return report
        # akapity się zgadzają liczebnie, ale suma nie — tail
    else:
        report.append(c(C.DIM, f"    ─ liczba akapitów: EN {len(en_paras)} vs PL {len(pl_paras)}"))

    # fallback: pokaż końcówki obu tekstów (tam najczęściej jest problem)
    report.append(c(C.DIM, "    ─ ostatnie linie oryginału EN:"))
    for line in en_lines(en_paras[-1])[:max_lines]:
        report.append(f"    {c(C.RED, 'EN')} {line.strip()[:88]}")
    report.append(c(C.DIM, "    ─ ostatnie linie tłumaczenia PL:"))
    for line in en_lines(pl_paras[-1])[:max_lines]:
        report.append(f"    {c(C.GREEN, 'PL')} {line.strip()[:88]}")
    return report

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

# ── line-by-line enforcement ────────────────────────────────────────────────
# matches the numbering we add (`12. `) and the model's mangled variants (`1p. `, `3re. `)
LINE_NUM_RE = re.compile(r"^\s*\d{1,4}[a-z]{0,3}[\.\)]\s+(.+)$")
TERMINAL_RE = re.compile(r"[.!?…][\"”’\)\]]?$")


def count_lines(text: str) -> int:
    """Number of non-empty lines (same metric the gates use)."""
    return len([l for l in text.split("\n") if l.strip()])


def number_lines(text: str) -> str:
    """Prefix every non-empty line with `N. ` so the model must answer 1:1."""
    out, n = [], 0
    for line in text.split("\n"):
        if line.strip():
            n += 1
            out.append(f"{n}. {line.strip()}")
        else:
            out.append("")
    return "\n".join(out)


def strip_line_numbers(text: str) -> str:
    """Remove the `N. ` / `N) ` prefixes (and mangled `1p. ` variants)."""
    out = []
    for line in text.split("\n"):
        if not line.strip():
            out.append("")
            continue
        m = LINE_NUM_RE.match(line)
        out.append(m.group(1).strip() if m else line.strip())
    return "\n".join(out)


def ensure_terminal_punctuation(text: str) -> str:
    """Every source line ends with terminal punctuation — keep it that way in PL."""
    out = []
    for line in text.split("\n"):
        s = line.rstrip()
        if not s:
            out.append("")
            continue
        if not TERMINAL_RE.search(s):
            if s[-1] in ",;:":
                s = s[:-1] + "."
            elif s[-1] in "\"”’)]":
                s = s[:-1] + "." + s[-1]
            elif s[-1].isalnum():
                s += "."
        out.append(s)
    return "\n".join(out)


def build_line_rule(text: str) -> tuple[str, int]:
    """Instruction forcing one output line per input line (empty for single-line input)."""
    n = count_lines(text)
    if n <= 1:
        return "", n
    return (
        f"FORMAT WEJŚCIA: dokładnie {n} ponumerowanych linii (1–{n}).\n"
        f"WYMAGANE WYJŚCIE: dokładnie {n} linii, każda w formacie \"N. tłumaczenie\", "
        f"w tej samej kolejności. Jedna linia wejścia = jedna linia wyjścia.\n"
        f"NIE łącz linii, NIE dziel linii, NIE dodawaj pustych linii ani numerów spoza 1–{n}."
    ), n


def postprocess(text: str) -> str:
    meta_patterns = [
        r"^(Oto|Oczywiście|Proszę|Poniżej)[^\n:]*:\s*",
        r"^(Here is|Here's)[^\n:]*:\s*",
        r"^Translation:\s*",
        r"^Tłumaczenie:\s*",
        r"^Korekta:\s*",
        r"^---+[ \t]*$",
        r"^═+[ \t]*$",
        r"^\[Fragment \d+ z \d+\][ \t]*$",
        r"^—Koniec fragmentu.*$",
    ]
    for pat in meta_patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.MULTILINE)

    text = strip_line_numbers(text)
    text = ensure_terminal_punctuation(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _stitch_by_source(pl_chunks: list[str], en_chunks: list[str]) -> str:
    """Rebuild the PL text aligned 1:1 with the SOURCE lines.

    The chunker cuts at sentence boundaries and may split ONE source line across
    two chunks (`"".join(en_chunks) == source`). A chunk boundary that falls
    strictly inside a source line means that line's translation is split over two
    PL chunks — those pieces are joined with a space. Every other non-empty source
    line maps to exactly one PL line; blank lines are preserved. No invented blank
    lines, no lost lines.
    """
    source = "".join(en_chunks)
    bounds: list[int] = []
    pos = 0
    for en in en_chunks[:-1]:
        pos += len(en)
        bounds.append(pos)

    pl_pieces: list[str] = []
    for pl in pl_chunks:
        pl_pieces += [l for l in pl.strip("\n").split("\n") if l.strip()]

    out: list[str] = []
    idx = 0
    line_start = 0
    for line in source.split("\n"):
        line_end = line_start + len(line)
        if line.strip():
            n = 1 + sum(1 for b in bounds if line_start < b < line_end)
            out.append(" ".join(p.strip() for p in pl_pieces[idx:idx + n]))
            idx += n
        else:
            out.append("")
        line_start = line_end + 1
    return "\n".join(out)


def stitch_chunks(chunks: list[str], overlap: int, en_chunks: list[str] | None = None) -> str:
    """Join translated chunks without inventing blank lines or losing lines.

    When the EN chunks are known, the result is aligned line-for-line with the
    source; otherwise the chunks are joined with a single newline.
    """
    if not chunks:
        return ""
    if len(chunks) == 1:
        return chunks[0]
    if en_chunks and len(en_chunks) == len(chunks):
        return _stitch_by_source(chunks, en_chunks)
    return "\n".join(c.strip("\n") for c in chunks)

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
            body = ""
            try:
                body = e.response.text[:300]
            except Exception:
                pass
            print(c(C.RED, f"\n  ✗ HTTP {e.response.status_code} (próba {attempt}/{max_retries})"))
            if body:
                print(c(C.RED, f"    {body}"))
        except Exception as e:
            print(c(C.RED, f"\n  ✗ {e} (próba {attempt}/{max_retries})"))
            # Thinking zjadł cały budżet max_tokens (finish_reason=length, 0 treści) —
            # ponawianie bez zmian nic nie da; podnosimy limit, żeby zmieścić odpowiedź.
            if "pusta odpowiedź" in str(e) and getattr(backend, "max_tokens", None):
                new_limit = min(backend.max_tokens * 2, 32768)
                if new_limit > backend.max_tokens:
                    backend.max_tokens = new_limit
                    print(c(C.YELLOW, f"  ↑ Podnoszę max_tokens do {new_limit}"))

        if attempt < max_retries:
            print(c(C.DIM, f"  Czekam {retry_delay}s..."))
            time.sleep(retry_delay)

    return ""  

# ── Error log (.md) ───────────────────────────────────────────────────────────
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)

def write_error_log(output_path: str, failures: dict, input_path: str = "", total_chunks: int = 0) -> str | None:
    """Zapisuje raport błędów do errors.md obok pliku wyjściowego. Zwraca ścieżkę lub None."""
    if not failures:
        return None
    log_path = Path(output_path).parent / "errors.md"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 🐛 Raport błędów tłumaczenia",
        "",
        f"- **Plik źródłowy:** `{input_path or '?'}`",
        f"- **Plik wyjściowy:** `{output_path}`",
        f"- **Chunki z błędami:** {len(failures)}" + (f" / {total_chunks}" if total_chunks else ""),
        f"- **Aktualizacja:** {ts}",
        "",
        "Ponów próbę: `uv run translator.py retry <plik wyjściowy>`",
        "",
        "---",
        "",
    ]
    for num in sorted(failures, key=lambda x: int(x)):
        entry = failures[num]
        if isinstance(entry, str):
            entry = {"reason": entry, "details": []}
        lines.append(f"## Chunk {num} — {entry.get('reason', 'błąd')}")
        lines.append("")
        details = [strip_ansi(d) for d in (entry.get("details") or [])]
        if details:
            lines.append("```")
            lines.extend(details)
            lines.append("```")
            lines.append("")
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return str(log_path)

# ── Session & Retry Functions ─────────────────────────────────────────────────
def find_chunks_dir(output_path: str) -> Path | None:
    """Znajduje katalog chunków dla pliku wyjściowego (nowy i stary layout)."""
    out = Path(output_path)
    for d in (out.parent / "chunks", out.parent / f"{out.stem}_chunks"):
        if (d / "session.json").exists():
            return d
    return None

def load_session(output_path: str) -> dict:
    """Wczytuje metadane sesji z katalogu chunks."""
    chunks_dir = find_chunks_dir(output_path)
    if not chunks_dir:
        print(c(C.RED, f"✗ Brak pliku sesji (session.json) dla: {output_path}"))
        sys.exit(1)
    return json.loads((chunks_dir / "session.json").read_text(encoding="utf-8"))

def detect_error_chunks(output_path: str) -> list[int]:
    """Wykrywa błędne chunki na podstawie pliku sesji i obecności markerów w tekście."""
    chunks_dir = find_chunks_dir(output_path)
    if not chunks_dir:
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
    failure_reasons = {}

    for chunk_num in chunk_numbers:
        orig_file = orig_dir / f"chunk_{chunk_num:03d}.txt"
        if not orig_file.exists():
            print(c(C.RED, f"  ✗ Brak oryginału EN dla chunk {chunk_num}: {orig_file}"))
            continue

        chunk_started = time.time()
        orig_chunk = orig_file.read_text(encoding="utf-8")
        prev_translation = ""
        if chunk_num > 1:
            prev_file = chunks_dir / f"chunk_{chunk_num-1:03d}.txt"
            if prev_file.exists():
                prev_text = prev_file.read_text(encoding="utf-8")
                prev_lines = prev_text.strip().split("\n")
                prev_translation = "\n".join(prev_lines[-6:])

        header = f"Regeneracja chunk {chunk_num}/{total_chunks}"
        print(f"{C.BOLD}{C.YELLOW}┌─ {header} {'─' * max(0, 54-len(header)-2)}┐{C.RESET}")
        print(f"{C.YELLOW}│{C.RESET}  {c(C.DIM, f'Rozmiar: {len(orig_chunk):,} znaków')}")
        print(f"{C.YELLOW}└{'─' * 54}┘{C.RESET}\n")

        printer = StreamPrinter()
        result, _fmt_attempts = generate_translation(
            backend, system_prompt, cfg, orig_chunk, chunk_num - 1, total_chunks,
            prev_translation, printer.on_token)
        printer.finish(f"chunk_{chunk_num:03d}.txt")
        total_tokens += printer.token_count
        took = time.time() - chunk_started

        if not result:
            print(c(C.RED, f"  ✗ Chunk {chunk_num}: pusta odpowiedź (think/budżet modelu?) — zachowuję stary"))
            failure_reasons[chunk_num] = {
                "reason": "pusta odpowiedź",
                "details": [],
            }
            continue

        result = postprocess(result)

        # Sprawdzanie ilości linii
        orig_lines = len([l for l in orig_chunk.split('\n') if l.strip()])
        trans_lines = len([l for l in result.split('\n') if l.strip()])

        if orig_lines != trans_lines:
            reason = f"niezgodność linii: EN {orig_lines} ≠ PL {trans_lines}"
            print(c(C.RED, f"  ✗ Chunk {chunk_num}: {reason} (po {took:.1f}s)"))
            details = line_mismatch_report(orig_chunk, result)
            for l in details:
                print(l)
            result = f"{CHUNK_ERROR_MARKER} {chunk_num}]\n\n{result}"
            chunk_file = chunks_dir / f"chunk_{chunk_num:03d}.txt"
            chunk_file.write_text(result, encoding="utf-8")
            failure_reasons[chunk_num] = {"reason": reason, "details": details}
            continue

        chunk_file = chunks_dir / f"chunk_{chunk_num:03d}.txt"
        chunk_file.write_text(result, encoding="utf-8")
        print(f"  {c(C.GREEN, f'✓ Chunk {chunk_num} zaktualizowany')} "
              f"{c(C.DIM, f'— {len(result):,} znaków, {took:.1f}s, ~{printer.token_count} tok.')}\n")
        successfully_retried.append(chunk_num)

    all_chunks = []
    all_en = []
    for i in range(1, total_chunks + 1):
        cf = chunks_dir / f"chunk_{i:03d}.txt"
        if cf.exists():
            all_chunks.append(cf.read_text(encoding="utf-8"))
            ef = orig_dir / f"chunk_{i:03d}.txt"
            all_en.append(ef.read_text(encoding="utf-8") if ef.exists() else "")

    if all_chunks:
        final_text = stitch_chunks(all_chunks, overlap, all_en)
        out.write_text(final_text, encoding="utf-8")

        # Usuń z listy "failed_chunks" tylko te chunki, które się faktycznie naprawiły
        meta["failed_chunks"] = [n for n in meta.get("failed_chunks", []) if n not in successfully_retried]

        # Scal stare i nowe błędy → errors.md
        merged_failures = {
            str(k): v for k, v in meta.get("failures", {}).items() if int(k) not in successfully_retried
        }
        for num, entry in failure_reasons.items():
            merged_failures[str(num)] = entry
        meta["failures"] = merged_failures
        (chunks_dir / "session.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        log_path = write_error_log(output_path, merged_failures, meta.get("input_path", ""), total_chunks)

        print(f"{C.GREEN}{C.BOLD}╔══════════════════════════════════════════════════╗")
        print(f"║  ✓ Regeneracja zakończona!                       ║")
        print(f"╚══════════════════════════════════════════════════╝{C.RESET}")
        print(f"\n  {c(C.CYAN, '💾 Zaktualizowano:')} {c(C.WHITE, output_path)}")
        if failure_reasons:
            print(f"\n  {c(C.RED, '⚠ Nadal wadliwe chunki:')}")
            for num, entry in failure_reasons.items():
                reason = entry["reason"] if isinstance(entry, dict) else entry
                print(f"    {c(C.RED, f'• Chunk {num}')} {c(C.DIM, '—')} {reason}")
        if log_path:
            print(f"\n  {c(C.CYAN, '📝 Raport błędów:')} {c(C.WHITE, log_path)}")
        print(f"\n  Ponów próbę: {c(C.WHITE, f'uv run translator.py retry {output_path}')}")

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
        ext = cfg.get("output_format", "txt")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(out_dir / f"{path.stem}_pl_{ts}" / f"{path.stem}_pl_{ts}.{ext}")
    run_dir = Path(output_path).parent
    run_dir.mkdir(parents=True, exist_ok=True)

    chunks_dir = run_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    orig_chunks_dir = chunks_dir / "original"
    orig_chunks_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = cfg["system_prompt"].strip()
    translated_chunks = []
    prev_translation = ""
    total_tokens = 0
    failed_chunks = []
    failure_log = {}
    start_time = time.time()

    print()

    for i, chunk in enumerate(chunks):
        elapsed = time.time() - start_time
        eta = (elapsed / (i + 0.001)) * (total_chunks - i) if i > 0 else 0

        header = f"Chunk {i+1}/{total_chunks}"
        print(f"{C.BOLD}{C.CYAN}┌─ {header} {'─' * max(0, 54-len(header)-2)}┐{C.RESET}")
        print(f"{C.CYAN}│{C.RESET}  {progress_bar(i, total_chunks, 38)}  "
              f"{c(C.DIM, f'ETA: {int(eta)}s') if eta > 0 else ''}")
        print(f"{C.CYAN}└{'─' * 54}┘{C.RESET}\n")

        chunk_started = time.time()
        # Zapisanie oryginału (wymagane do ponawiania)
        (orig_chunks_dir / f"chunk_{i+1:03d}.txt").write_text(chunk, encoding="utf-8")

        printer = StreamPrinter()
        result, _fmt_attempts = generate_translation(
            backend, system_prompt, cfg, chunk, i, total_chunks, prev_translation,
            printer.on_token)
        printer.finish(f"chunk_{i+1:03d}.txt")
        total_tokens += printer.token_count
        took = time.time() - chunk_started

        chunk_failed = False
        failure_reason = ""
        failure_details = []
        if not result:
            failure_reason = "pusta odpowiedź (model zmieścił wszystko w thinking / HTTP?)"
            print(c(C.RED, f"  ✗ Chunk {i+1}: {failure_reason} (po {took:.1f}s)"))
            result = f"{CHUNK_ERROR_MARKER} {i+1}]\n\n{chunk}"
            failed_chunks.append(i+1)
            chunk_failed = True
        else:
            result = postprocess(result)

            # Sprawdzanie ilości linii (ignorując całkowicie puste akapity)
            orig_lines = len([l for l in chunk.split('\n') if l.strip()])
            trans_lines = len([l for l in result.split('\n') if l.strip()])

            if orig_lines != trans_lines:
                failure_reason = f"niezgodność linii: EN {orig_lines} ≠ PL {trans_lines}"
                print(c(C.RED, f"  ✗ Chunk {i+1}: {failure_reason} (po {took:.1f}s)"))
                failure_details = line_mismatch_report(chunk, result)
                for l in failure_details:
                    print(l)
                # Doklejamy marker z błędem, ale zostawiamy też wygenerowany tekst
                result = f"{CHUNK_ERROR_MARKER} {i+1}]\n\n{result}"
                failed_chunks.append(i+1)
                chunk_failed = True

        translated_chunks.append(result)
        prev_translation = result

        # Zapisanie wyniku PL
        (chunks_dir / f"chunk_{i+1:03d}.txt").write_text(result, encoding="utf-8")

        if chunk_failed:
            failure_log[i+1] = {"reason": failure_reason, "details": failure_details}
        else:
            print(f"  {c(C.GREEN, f'✓ Chunk {i+1}/{total_chunks}')}"
                  f"{c(C.DIM, f' — {len(result):,} znaków, {took:.1f}s, ~{printer.token_count} tok.')}")
        print()

    final_text = stitch_chunks(translated_chunks, overlap, chunks)
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
        "failures": {str(k): v for k, v in failure_log.items()},
    }
    (chunks_dir / "session.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{C.GREEN}{C.BOLD}╔══════════════════════════════════════════════════╗")
    print(f"║  ✓ Tłumaczenie zakończone!                       ║")
    print(f"╚══════════════════════════════════════════════════╝{C.RESET}")
    print(f"\n  {c(C.CYAN, '💾 Zapisano:')} {c(C.WHITE, output_path)}")
    print(f"  {c(C.CYAN, '📁 Folder:')}    {c(C.WHITE, str(run_dir.resolve()))}")

    # Opcja autozregenerowania
    if failed_chunks:
        log_path = write_error_log(output_path, failure_log, str(path.resolve()), total_chunks)
        if log_path:
            print(f"  {c(C.CYAN, '📝 Raport błędów:')} {c(C.WHITE, log_path)}")
        print(f"\n  {c(C.RED, '⚠ Chunks z błędami (klucz → powód):')}")
        for n in failed_chunks:
            reason = failure_log[n]["reason"]
            print(f"    {c(C.RED, f'• Chunk {n}/{total_chunks}')} {c(C.DIM, '—')} {reason}")
        try:
            answer = input(f"\n  {c(C.YELLOW, '→ Spróbować zregenerować te chunki teraz? [t/N]: ')}").strip().lower()
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
        printer.finish(f"refine text")

        if not result:
            result = draft_chunk
        refined_chunks.append(postprocess(result))

    final_text = stitch_chunks(refined_chunks, overlap)
    Path(output_path).write_text(final_text, encoding="utf-8")
    print(f"\n  {c(C.CYAN, '💾 Zapisano refine:')} {c(C.WHITE, output_path)}")
    return output_path

# ── Interactive menu ─────────────────────────────────────────────────────────
def pick_from_menu(title: str, options: list[str], default: str = None) -> str:
    """Numerowane menu wyboru; Enter = default (zaznaczony ← domyślny)."""
    print(f"\n{c(C.MAGENTA, title)}")
    for i, opt in enumerate(options, 1):
        mark = ""
        col = C.WHITE
        if default is not None and opt == default:
            mark = f"  {c(C.DIM, '← domyślny')}"
            col = C.GREEN
        print(f"  {c(C.CYAN, str(i))}) {c(col, opt)}{mark}")

    while True:
        try:
            choice = input(f"{c(C.WHITE, 'Wybór')} [{options.index(default) + 1 if default in options else ''}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(1)

        if not choice:
            if default is not None and default in options:
                return default
            if default is not None and default not in options:
                # default to wartość ręczna spoza listy (np. nieznany model z yaml)
                return default
            continue

        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]

        if choice in options:
            return choice

        print(c(C.RED, f"  Nieprawidłowy wybór: {choice} (podaj numer 1-{len(options)})"))


def ask_custom_model(default: str = "", hint: str = "") -> str:
    prompt = f"  {c(C.WHITE, 'Nazwa modelu (Enter = domyślny: ')}{c(C.GREEN, default or 'brak')}{c(C.WHITE, ')')}{hint}{c(C.WHITE, ': ')}"
    try:
        val = input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(1)
    return val or default


def interactive_backend_model(cfg: dict) -> None:
    """Interaktywny wybór backendu i modelu (modyfikuje cfg in-place)."""
    backend = pick_from_menu("Backend:", ["ollama", "deepseek", "opencode-go"], cfg.get("backend", "ollama"))
    cfg["backend"] = backend

    if backend == "ollama":
        default_model = cfg.get("ollama_model", "gemma3")
        cfg_for_probe = dict(cfg)
        models = OllamaBackend(cfg_for_probe).list_models()
        if models:
            model = pick_from_menu(f"Modele Ollama ({cfg['ollama_url']}):", models, default_model if default_model in models else None)
        else:
            print(c(C.YELLOW, "  Nie udało się pobrać listy modeli z Ollama."))
            model = ask_custom_model(default_model)
        cfg["ollama_model"] = model

    elif backend == "deepseek":
        default_model = cfg.get("deepseek_model", "deepseek-chat")
        models = ["deepseek-chat", "deepseek-reasoner"]
        cfg["deepseek_model"] = pick_from_menu("Modele DeepSeek:", models, default_model if default_model in models else None)

    else:  # opencode-go
        default_model = cfg.get("opencode_go_model", "glm-5.3-flash")
        models = []
        if cfg.get("opencode_go_api_key"):
            try:
                models = OpenCodeGoBackend(cfg).list_models()
            except Exception:
                models = []
        if models:
            cfg["opencode_go_model"] = pick_from_menu("Modele OpenCode Go:", models, default_model if default_model in models else None)
        else:
            print(c(C.YELLOW, "  Nie udało się pobrać listy modeli z OpenCode Go (brak klucza lub połączenia)."))
            cfg["opencode_go_model"] = ask_custom_model(default_model)

        # GLM-5.3 ma wymuszony thinking — wybieramy tylko poziom mocy rozumowania
        efforts = ["low", "medium", "high", "max", "(domyślny serwera)"]
        default_effort = cfg.get("reasoning_effort") or "(domyślny serwera)"
        model_name_for_effort = cfg["opencode_go_model"].lower()
        if model_name_for_effort.startswith("glm-5"):
            effort = pick_from_menu("Poziom deep thinking (GLM-5.3 ma wymuszony thinking):", efforts,
                                    default_effort if default_effort in efforts else None)
            cfg["reasoning_effort"] = "" if effort == "(domyślny serwera)" else effort
        else:
            cfg["reasoning_effort"] = default_effort if default_effort != "(domyślny serwera)" else ""

    model_name = {
        "ollama":       cfg.get("ollama_model"),
        "deepseek":     cfg.get("deepseek_model"),
        "opencode-go":  cfg.get("opencode_go_model"),
    }[backend]
    print(f"  {c(C.DIM, '→')}{c(C.WHITE, ' backend')} {c(C.GREEN, backend)}  {c(C.DIM, '|')}{c(C.WHITE, ' model')} {c(C.GREEN, model_name)}")

def print_run_config(cfg: dict, input_path: str = None, output_path: str = None, dry_run: bool = False, refine: bool = False) -> None:
    """Podsumowanie ustawień przed startem generowania."""
    backend = cfg.get("backend", "ollama")
    model = {
        "ollama":       cfg.get("ollama_model"),
        "deepseek":     cfg.get("deepseek_model"),
        "opencode-go":  cfg.get("opencode_go_model"),
    }.get(backend)

    url = {
        "ollama":       cfg.get("ollama_url"),
        "deepseek":     DeepSeekBackend.BASE_URL,
        "opencode-go":  OpenCodeGoBackend.BASE_URL,
    }.get(backend)

    rows = [
        ("Backend", f"{backend}  ({url})", True),
        ("Model",   model or "?", True),
        ("Temperature", str(cfg.get("temperature", 0.3)), False),
        ("Max tokens",  str(cfg.get("max_tokens", 4096)), False),
        ("Chunk",  f"{cfg.get('chunk_size', 2500)} znaków (overlap {cfg.get('chunk_overlap', 200)})", False),
    ]
    if backend == "opencode-go":
        effort = cfg.get("reasoning_effort") or "domyślny serwera (max)"
        rows.append(("Thinking", f"reasoning_effort: {effort}", False))
    if refine:
        rows.append(("Refine", f"chunk {cfg.get('refine_chunk_size', 5000)} znaków, temp {cfg.get('refine_temperature', 0.2)}", False))
    if input_path is not None:
        rows.append(("Plik", str(input_path), False))
    if output_path is not None:
        rows.append(("Wyjście", str(output_path), False))
    if dry_run:
        rows.append(("Tryb", "DRY RUN (bez zapytań do modelu)", False))

    print(f"\n{c(C.CYAN, '⚙ Konfiguracja generowania:')}")
    for key, val, green in rows:
        print(f"  {c(C.DIM, key + ':')} {' ' * (12 - len(key))}{c(C.GREEN if green else C.WHITE, val)}")

# ── CLI handlers ──────────────────────────────────────────────────────────────
def cmd_translate(args):
    cfg = load_config(args.config)
    if sys.stdin.isatty() and not args.quiet and not args.model and not args.backend:
        interactive_backend_model(cfg)
    if args.backend: cfg["backend"] = args.backend
    if args.model:
        cfg["ollama_model"] = args.model
        cfg["deepseek_model"] = args.model
        cfg["opencode_go_model"] = args.model
    if getattr(args, "reasoning", None): cfg["reasoning_effort"] = args.reasoning
    if args.chunk_size: cfg["chunk_size"] = args.chunk_size
    if args.temperature is not None: cfg["temperature"] = args.temperature
    print_run_config(cfg, args.input, args.output, args.dry_run, refine=args.refine)

    out = translate_file(args.input, cfg, args.output, args.dry_run, not args.quiet)

    if args.refine and out:
        refine_file(out, args.input, cfg, verbose=not args.quiet)

def cmd_refine(args):
    cfg = load_config(args.config)
    if sys.stdin.isatty() and not args.quiet and not args.model and not args.backend:
        interactive_backend_model(cfg)
    if args.backend: cfg["backend"] = args.backend
    if args.model:
        cfg["ollama_model"] = args.model
        cfg["deepseek_model"] = args.model
        cfg["opencode_go_model"] = args.model
    if getattr(args, "reasoning", None): cfg["reasoning_effort"] = args.reasoning
    print_run_config(cfg, args.draft, args.output, refine=True)
    refine_file(args.draft, args.original, cfg, args.output, not args.quiet)

def cmd_diagnose(args):
    path = Path(args.file)
    if not path.exists(): sys.exit(1)
    run_diagnostics(path.read_text(encoding="utf-8"), True)

def cmd_info(args):
    cfg = load_config(args.config)
    backend = cfg.get("backend", "ollama")
    model = {
        "ollama":       cfg.get("ollama_model"),
        "deepseek":     cfg.get("deepseek_model"),
        "opencode-go":  cfg.get("opencode_go_model"),
    }.get(backend)
    print(f"Backend: {backend}")
    print(c(C.WHITE, f"  Model: {c(C.GREEN, model or '?')}"))

def cmd_retry(args):
    cfg = load_config(args.config)
    if sys.stdin.isatty() and not args.chunks and not args.model and not args.backend:
        interactive_backend_model(cfg)
    if args.backend: cfg["backend"] = args.backend
    if args.model:
        cfg["ollama_model"] = args.model
        cfg["deepseek_model"] = args.model
        cfg["opencode_go_model"] = args.model
    if getattr(args, "reasoning", None): cfg["reasoning_effort"] = args.reasoning
    print_run_config(cfg, args.output, refine=False)

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
    p_t.add_argument("--backend", "-b", choices=["ollama", "deepseek", "opencode-go"], default=None)
    p_t.add_argument("--reasoning", "-r", choices=["low", "medium", "high", "max"], default=None)
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
    p_r.add_argument("--backend", "-b", choices=["ollama", "deepseek", "opencode-go"], default=None)
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
    p_retry.add_argument("--backend", "-b", choices=["ollama", "deepseek", "opencode-go"], default=None)
    p_retry.add_argument("--reasoning", "-r", choices=["low", "medium", "high", "max"], default=None)
    p_retry.set_defaults(func=cmd_retry)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)

if __name__ == "__main__":
    main()