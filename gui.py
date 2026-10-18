#!/usr/bin/env python3
"""
LN Translator — GUI (PySide6)

Okno podglądu tłumaczenia chunk po chunku:
  - lewa kolumna: oryginał EN, prawa: tłumaczenie PL (zsynchronizowany scroll)
  - pasek chunków ze statusami (pending / generowanie / ok / błąd)
  - statystyki (postęp, linie, znaki, tokeny, ETA) i ponawianie błędnych chunków
  - wynik chunka pojawia się w całości dopiero po zakończeniu generowania

Uruchamianie:  uv run translator translate <plik> --gui [--prompt]
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import yaml

import translator as tr

from PySide6.QtCore import (QObject, QRect, QSize, Qt, QThread, QTimer, QUrl,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QKeySequence, QPainter,
                           QPalette, QShortcut)
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QVBoxLayout, QWidget)

# ── Motyw ─────────────────────────────────────────────────────────────────────
BG      = "#15171b"
PANEL   = "#1d2025"
PANEL_2 = "#22262c"
BORDER  = "#2d323a"
TEXT    = "#d7dae0"
DIM     = "#8a919b"
ACCENT  = "#4d9fff"
OK      = "#3fb950"
ERR     = "#f0554d"


def build_qss() -> str:
    return f"""
    QWidget {{
        background: {BG};
        color: {TEXT};
        font-size: 12px;
    }}
    QMainWindow, QDialog {{ background: {BG}; }}

    QFrame#topBar, QFrame#navBar, QFrame#strip, QFrame#pane, QFrame#sidebar {{
        background: {PANEL};
        border: 1px solid {BORDER};
        border-radius: 8px;
    }}
    QFrame#errFrame {{
        background: {PANEL_2};
        border: 1px solid {BORDER};
        border-radius: 6px;
    }}

    QLabel#topTitle {{ font-size: 13px; font-weight: 600; background: transparent; }}
    QLabel#topModel, QLabel#chunkInfo, QLabel#hint, QLabel#dialogInfo {{
        color: {DIM}; background: transparent;
    }}
    QLabel#statusText {{ font-weight: 600; background: transparent; }}
    QLabel#paneTitle {{
        color: {DIM}; font-size: 11px; font-weight: 700;
        letter-spacing: 1px; background: transparent;
    }}
    QLabel#navChunk {{ font-size: 13px; font-weight: 600; background: transparent; }}
    QLabel#panelHeader {{
        color: {DIM}; font-size: 11px; font-weight: 700; letter-spacing: 1px;
        background: transparent;
    }}
    QLabel#statKey {{ color: {DIM}; background: transparent; }}
    QLabel#statVal {{ font-weight: 600; background: transparent; }}
    QLabel#outPath {{ background: transparent; }}
    QLabel#errRow {{ background: transparent; }}
    QLabel#errNone {{ color: {DIM}; font-style: italic; background: transparent; }}

    QPlainTextEdit {{
        background: {PANEL_2};
        border: 1px solid {BORDER};
        border-radius: 6px;
        color: {TEXT};
        selection-background-color: #2d4a6b;
        font-family: 'JetBrains Mono', 'Fira Code', 'DejaVu Sans Mono', monospace;
    }}

    QPushButton {{
        background: {PANEL_2};
        border: 1px solid {BORDER};
        border-radius: 5px;
        padding: 5px 12px;
        color: {TEXT};
    }}
    QPushButton:hover {{ border-color: {ACCENT}; color: #ffffff; }}
    QPushButton:pressed {{ background: #2b3037; }}
    QPushButton:disabled {{ color: #5a616b; border-color: {BORDER}; }}
    QPushButton#primary {{
        background: #1f3d63; border-color: #2f5d8f; color: #cfe4ff; font-weight: 600;
    }}
    QPushButton#primary:hover {{ background: #264a75; }}

    QProgressBar {{
        background: {PANEL_2};
        border: 1px solid {BORDER};
        border-radius: 5px;
        text-align: center;
        color: {TEXT};
        font-size: 11px;
    }}
    QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

    QCheckBox {{ color: {DIM}; background: transparent; spacing: 6px; }}
    QCheckBox::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {BORDER};
        border-radius: 4px;
        background: {PANEL_2};
    }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: #3a4048; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: #4a525c; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{ background: #3a4048; border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QSplitter::handle {{ background: {BG}; }}
    QSplitter::handle:horizontal {{ width: 6px; }}

    /* ── pasek chunków ─────────────────────────────────────────────────── */
    QPushButton[chunkRole="chunk"] {{
        padding: 0;
        min-width: 44px; max-width: 44px;
        min-height: 24px; max-height: 24px;
        font-size: 11px;
        border-radius: 5px;
    }}
    QPushButton[chunkRole="chunk"][chunkState="pending"] {{
        background: {PANEL_2}; color: #6d747e; border: 1px solid {BORDER};
    }}
    QPushButton[chunkRole="chunk"][chunkState="running_a"] {{
        background: #1e3a5f; color: #bcd7ff; border: 1px solid #3b6ea8;
    }}
    QPushButton[chunkRole="chunk"][chunkState="running_b"] {{
        background: #16293f; color: #8fb8e8; border: 1px solid #2e517c;
    }}
    QPushButton[chunkRole="chunk"][chunkState="ok"] {{
        background: #17281c; color: {OK}; border: 1px solid #2a4d31;
    }}
    QPushButton[chunkRole="chunk"][chunkState="error"] {{
        background: #341a1b; color: {ERR}; border: 1px solid #6e2c2c;
    }}
    QPushButton[chunkRole="chunk"][chunkState="edited"] {{
        background: #33290f; color: #e3b341; border: 1px solid #5c4a16;
    }}
    QPushButton[chunkRole="chunk"][flash="true"] {{ border: 1px solid #dfe6ee; }}
    QPushButton[chunkRole="chunk"][current="true"] {{ border: 1px solid {ACCENT}; color: #ffffff; }}

    /* edytowalny panel PL — cieplejsza ramka sygnalizuje tryb edycji */
    QPlainTextEdit[editable="true"] {{ border: 1px solid #3d5a7c; }}
    """


def _polish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def mono_font(size: int = 10) -> QFont:
    f = QFont()
    f.setFamilies(["JetBrainsMono Nerd Font", "JetBrains Mono", "Fira Code",
                   "Cascadia Mono", "DejaVu Sans Mono", "Liberation Mono",
                   "Noto Sans Mono"])
    f.setStyleHint(QFont.Monospace)
    f.setFixedPitch(True)
    f.setPointSize(size)
    return f


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _fmt_eta(sec: float) -> str:
    sec = max(0, int(sec))
    if sec < 90:
        return f"{sec} s"
    m, s = divmod(sec, 60)
    if m < 90:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def _elide(text: str, width: int) -> str:
    return QApplication.fontMetrics().elidedText(text, Qt.ElideMiddle, width)


def _header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("panelHeader")
    return lbl


def align_pl_to_en(en: str, pl: str) -> str:
    """Składa linie PL w strukturę akapitów EN.

    Silnik gwarantuje zgodność liczby linii NIEPUSTYCH (1:1), ale model nie
    zawsze oddaje puste linie między akapitami — tutaj rozmieszczamy tłumaczenie
    wg układu źródła: pusta linia EN = pusta linia PL, dzięki czemu obie kolumny
    są wyrównane wiersz w wiersz.
    """
    pl_lines = [l for l in pl.split("\n") if l.strip()]
    out: list[str] = []
    idx = 0
    for line in en.split("\n"):
        if line.strip():
            out.append(pl_lines[idx] if idx < len(pl_lines) else "")
            idx += 1
        else:
            out.append("")
    if idx < len(pl_lines):  # nadmiarowe linie PL (rozjazd) — nie gubimy ich
        out.extend(pl_lines[idx:])
    return "\n".join(out)


def _clean_pl(text: str) -> str:
    """Usuwa marker błędu doklejany do zapisanego chunka (nie dotyczy podglądu)."""
    if tr.CHUNK_ERROR_MARKER not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    skipping = True
    for line in lines:
        if skipping and (line.startswith("[BŁĄD") or not line.strip()):
            continue
        skipping = False
        out.append(line)
    return "\n".join(out)


# ── Edytor z numeracją linii ─────────────────────────────────────────────────
class _LineNumberArea(QWidget):
    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_area_width(), 0)

    def paintEvent(self, event):  # noqa: N802 (Qt API)
        self.editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """QPlainTextEdit tylko-do-czytania z numerami linii po lewej."""

    def __init__(self):
        super().__init__()
        self.setFont(mono_font(10))
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_area_width)
        self.updateRequest.connect(self._update_area)
        self._update_area_width()

    def line_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_area_width(self, *_):
        self.setViewportMargins(self.line_area_width(), 0, 0, 0)

    def _update_area(self, rect, dy):
        if dy:
            self._area.scroll(0, dy)
        else:
            self._area.update(0, rect.y(), self._area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_area_width()

    def resizeEvent(self, event):  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._area.setGeometry(QRect(cr.left(), cr.top(), self.line_area_width(), cr.height()))

    def paint_line_numbers(self, event):
        painter = QPainter(self._area)
        painter.fillRect(event.rect(), QColor("#1a1d22"))
        painter.setPen(QColor(BORDER))
        painter.drawLine(event.rect().topRight(), event.rect().bottomRight())

        block = self.firstVisibleBlock()
        n = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        painter.setPen(QColor(DIM))
        painter.setFont(self.font())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(0, top, self._area.width() - 8,
                                 self.fontMetrics().height(), Qt.AlignRight, str(n + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            n += 1


# ── Pasek chunków ─────────────────────────────────────────────────────────────
class ChunkStrip(QFrame):
    chunkSelected = Signal(int)

    COLS = 14

    def __init__(self):
        super().__init__()
        self.setObjectName("strip")
        self.setFixedHeight(132)
        self.buttons: list[QPushButton] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 4)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        self.scroll.setWidget(self.container)
        outer.addWidget(self.scroll)

    def build(self, total: int) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.buttons = []
        for i in range(total):
            btn = QPushButton(str(i + 1))
            btn.setProperty("chunkRole", "chunk")
            btn.setProperty("chunkState", "pending")
            btn.setProperty("current", False)
            btn.setProperty("flash", False)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(f"Chunk {i + 1}")
            btn.clicked.connect(lambda _=False, idx=i + 1: self.chunkSelected.emit(idx))
            r, c = divmod(i, self.COLS)
            self.grid.addWidget(btn, r, c)
            self.buttons.append(btn)

    def set_state(self, idx: int, state: str) -> None:
        if not 1 <= idx <= len(self.buttons):
            return
        btn = self.buttons[idx - 1]
        btn.setProperty("chunkState", state)
        _polish(btn)

    def set_current(self, idx: int) -> None:
        for i, b in enumerate(self.buttons, 1):
            cur = (i == idx)
            if b.property("current") != cur:
                b.setProperty("current", cur)
                _polish(b)
        if 1 <= idx <= len(self.buttons):
            self.scroll.ensureWidgetVisible(self.buttons[idx - 1], 24, 12)

    def flash(self, idx: int) -> None:
        if not 1 <= idx <= len(self.buttons):
            return
        btn = self.buttons[idx - 1]

        def off():
            btn.setProperty("flash", False)
            _polish(btn)

        btn.setProperty("flash", True)
        _polish(btn)
        QTimer.singleShot(650, off)


# ── Wątki robocze ─────────────────────────────────────────────────────────────
class _WorkerSignals(QObject):
    event = Signal(dict)


class TranslateWorker(QThread):
    def __init__(self, input_path: str, cfg: dict, output_path: str | None,
                 max_time: int | None = None):
        super().__init__()
        self.signals = _WorkerSignals()
        self.input_path = input_path
        self.cfg = cfg
        self.output_path = output_path
        self.stop_event = threading.Event()
        self.max_time = max_time

    def run(self) -> None:
        try:
            tr.translate_file(self.input_path, self.cfg, self.output_path,
                              verbose=False, interactive=False, finalize=False,
                              on_event=self.signals.event.emit,
                              stop_event=self.stop_event, max_time=self.max_time)
        except SystemExit as e:
            self.signals.event.emit({"type": "worker_error",
                                     "message": f"tłumaczenie przerwane (exit {e.code})"})
        except Exception as e:
            self.signals.event.emit({"type": "worker_error",
                                     "message": f"{type(e).__name__}: {e}"})


class ResumeWorker(QThread):
    """Wznawia przerwaną sesję (stop / limit czasu / restart komputera)."""
    def __init__(self, output_path: str, cfg: dict, max_time: int | None = None):
        super().__init__()
        self.signals = _WorkerSignals()
        self.output_path = output_path
        self.cfg = cfg
        self.stop_event = threading.Event()
        self.max_time = max_time

    def run(self) -> None:
        try:
            tr.resume_translation(self.output_path, self.cfg, verbose=False,
                                  on_event=self.signals.event.emit,
                                  finalize=False,
                                  stop_event=self.stop_event, max_time=self.max_time)
        except SystemExit as e:
            self.signals.event.emit({"type": "worker_error",
                                     "message": f"wznawianie przerwane (exit {e.code})"})
        except Exception as e:
            self.signals.event.emit({"type": "worker_error",
                                     "message": f"{type(e).__name__}: {e}"})


class RetryWorker(QThread):
    def __init__(self, output_path: str, chunks: list[int], cfg: dict):
        super().__init__()
        self.signals = _WorkerSignals()
        self.output_path = output_path
        self.chunks = chunks
        self.cfg = cfg

    def run(self) -> None:
        try:
            tr.retry_chunks(self.output_path, self.chunks, self.cfg,
                            verbose=False, on_event=self.signals.event.emit,
                            finalize=False)
            self.signals.event.emit({"type": "retry_finished", "chunks": self.chunks})
        except SystemExit as e:
            self.signals.event.emit({"type": "worker_error",
                                     "message": f"ponawianie przerwane (exit {e.code})"})
        except Exception as e:
            self.signals.event.emit({"type": "worker_error",
                                     "message": f"{type(e).__name__}: {e}"})


# ── Okno główne ───────────────────────────────────────────────────────────────
class TranslatorWindow(QMainWindow):
    def __init__(self, args, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.input_path = str(args.input)
        self.output_path = getattr(args, "output", None)

        self.total = 0
        self.states: list[dict] = []
        self.current_idx = 0
        self.t_start: float | None = None
        self.translate_worker: TranslateWorker | None = None
        self.retry_worker: RetryWorker | None = None
        self.resume_worker: ResumeWorker | None = None
        self.resume_run_dir: str | None = None   # katalog wznawianej sesji
        self._stop_requested = False             # lokalny stop this run
        self.finalized = False          # czy chunki zostały sklejone do pliku wyjściowego
        self._gen_idx = 0
        self._pulse_flip = False
        self._dot_n = 0
        self._syncing = False
        self._loading = False           # guard: setPlainText podczas przełączania chunka
        self._anim: QVariantAnimation | None = None

        self.setWindowTitle(f"LN Translator — {Path(self.input_path).name}")
        self.resize(1480, 900)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        root.addWidget(self._build_top_bar())
        self.strip = ChunkStrip()
        self.strip.chunkSelected.connect(lambda idx: self._goto(idx))
        root.addWidget(self.strip)
        root.addWidget(self._build_nav_bar())

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_editors())
        split.addWidget(self._build_sidebar())
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1160, 300])
        root.addWidget(split, 1)

        self.setCentralWidget(central)

        self.pulse_timer = QTimer(self, interval=450, timeout=self._pulse_tick)
        self.clock_timer = QTimer(self, interval=1000, timeout=self._refresh_stats)
        self.save_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save_current_edit)

        self._install_shortcuts()

        QTimer.singleShot(150, self._start_worker)

    # ── budowa UI ────────────────────────────────────────────────────────────
    def _build_top_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(1)
        self.title_label = QLabel(_elide(self.input_path, 420))
        self.title_label.setObjectName("topTitle")
        self.model_label = QLabel("przygotowanie…")
        self.model_label.setObjectName("topModel")
        left.addWidget(self.title_label)
        left.addWidget(self.model_label)
        lay.addLayout(left)

        lay.addStretch(1)

        self.status_dot = QLabel()
        self.status_dot.setFixedSize(10, 10)
        self.status_dot.setStyleSheet(f"background: {DIM}; border-radius: 5px;")
        self.status_text = QLabel("oczekuje")
        self.status_text.setObjectName("statusText")
        lay.addWidget(self.status_dot)
        lay.addWidget(self.status_text)
        lay.addSpacing(16)

        self.progress = QProgressBar()
        self.progress.setFixedSize(240, 16)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("")
        lay.addWidget(self.progress)
        lay.addSpacing(16)

        self.follow = QCheckBox("podążaj za postępem")
        self.follow.setChecked(True)
        lay.addWidget(self.follow)
        return bar

    def _build_nav_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("navBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 5, 12, 5)
        lay.setSpacing(10)

        self.btn_prev = QPushButton("‹  poprzedni")
        self.btn_prev.clicked.connect(lambda: self._goto(self.current_idx - 1))
        self.btn_next = QPushButton("następny  ›")
        self.btn_next.clicked.connect(lambda: self._goto(self.current_idx + 1))
        lay.addWidget(self.btn_prev)

        self.nav_label = QLabel("—")
        self.nav_label.setObjectName("navChunk")
        self.nav_label.setAlignment(Qt.AlignCenter)
        self.nav_label.setFixedWidth(140)
        lay.addWidget(self.nav_label)
        lay.addWidget(self.btn_next)

        self.chunk_info = QLabel("")
        self.chunk_info.setObjectName("chunkInfo")
        lay.addSpacing(10)
        lay.addWidget(self.chunk_info, 1)

        hint = QLabel("←/→  chunk   ·   PgUp/PgDn  ±10   ·   Home/End  początek/koniec")
        hint.setObjectName("hint")
        lay.addWidget(hint)
        return bar

    def _build_editors(self) -> QWidget:
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        def pane(title: str) -> tuple[QFrame, CodeEditor]:
            f = QFrame()
            f.setObjectName("pane")
            v = QVBoxLayout(f)
            v.setContentsMargins(8, 6, 8, 8)
            v.setSpacing(4)
            lbl = QLabel(title)
            lbl.setObjectName("paneTitle")
            ed = CodeEditor()
            v.addWidget(lbl)
            v.addWidget(ed, 1)
            return f, ed

        self.en_frame, self.en_view = pane("ORYGINAŁ  ·  EN")
        self.pl_frame, self.pl_view = pane("TŁUMACZENIE  ·  PL")
        self.pl_view.setObjectName("plView")
        lay.addWidget(self.en_frame, 1)
        lay.addWidget(self.pl_frame, 1)

        pal = self.pl_view.palette()
        pal.setColor(QPalette.PlaceholderText, QColor("#5f6873"))
        self.pl_view.setPalette(pal)

        self.en_view.verticalScrollBar().valueChanged.connect(self._sync_from_en)
        self.pl_view.verticalScrollBar().valueChanged.connect(self._sync_from_pl)
        self.pl_view.textChanged.connect(self._pl_text_changed)
        self.pl_view.installEventFilter(self)  # FocusIn → wyłącz auto-podążanie
        return wrap

    def _build_sidebar(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        panel = QFrame()
        panel.setObjectName("sidebar")
        panel.setFixedWidth(272)
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        v.addWidget(_header("STATYSTYKI"))
        self.stats: dict[str, QLabel] = {}
        for key, label in [
            ("postep", "Postęp"), ("done", "Ukończone"), ("err", "Błędy"),
            ("len_en", "Linie EN"), ("len_pl", "Linie PL"),
            ("chars", "Znaki PL"), ("tokens", "Tokeny"),
            ("speed", "Prędkość"), ("avg", "Śr. czas chunka"), ("eta", "Pozostało (ETA)"),
        ]:
            row = QHBoxLayout()
            k = QLabel(label)
            k.setObjectName("statKey")
            val = QLabel("—")
            val.setObjectName("statVal")
            row.addWidget(k)
            row.addStretch(1)
            row.addWidget(val)
            v.addLayout(row)
            self.stats[key] = val

        v.addSpacing(8)
        v.addWidget(_header("PLIK WYJŚCIOWY"))
        self.out_label = QLabel("—")
        self.out_label.setObjectName("outPath")
        self.out_label.setWordWrap(True)
        v.addWidget(self.out_label)
        self.merge_btn = QPushButton("Scal do pliku")
        self.merge_btn.setObjectName("primary")
        self.merge_btn.setEnabled(False)
        self.merge_btn.clicked.connect(self._merge)
        v.addWidget(self.merge_btn)
        self.open_btn = QPushButton("Otwórz katalog")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_output_dir)
        v.addWidget(self.open_btn)

        v.addSpacing(8)
        v.addWidget(_header("STEROWANIE"))
        row = QHBoxLayout()
        row.setSpacing(4)
        lbl = QLabel("auto-stop po")
        lbl.setObjectName("statKey")
        self.stop_after = QSpinBox()
        self.stop_after.setRange(0, 24 * 60)
        self.stop_after.setValue(0)
        self.stop_after.setSpecialValueText("brak")
        self.stop_after.setSuffix(" min")
        self.stop_after.setFixedWidth(104)
        row.addWidget(lbl)
        row.addStretch(1)
        row.addWidget(self.stop_after)
        v.addLayout(row)

        self.btn_stop = QPushButton("⏹ Zatrzymaj")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_translation)
        v.addWidget(self.btn_stop)

        self.btn_resume = QPushButton("Wznów")
        self.btn_resume.setEnabled(False)
        self.btn_resume.clicked.connect(self._resume_session)
        v.addWidget(self.btn_resume)

        v.addSpacing(8)
        self.errors_header = _header("BŁĘDY (0)")
        v.addWidget(self.errors_header)
        self.errors_box = QVBoxLayout()
        self.errors_box.setSpacing(4)
        v.addLayout(self.errors_box)
        v.addStretch(1)

        scroll.setWidget(panel)
        return scroll

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self._goto(self.current_idx - 1))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self._goto(self.current_idx + 1))
        QShortcut(QKeySequence(Qt.Key_PageUp), self, activated=lambda: self._goto(self.current_idx - 10))
        QShortcut(QKeySequence(Qt.Key_PageDown), self, activated=lambda: self._goto(self.current_idx + 10))
        QShortcut(QKeySequence(Qt.Key_Home), self, activated=lambda: self._goto(1))
        QShortcut(QKeySequence(Qt.Key_End), self, activated=lambda: self._goto(self.total))
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save_current_edit)

    # ── nawigacja / podgląd ──────────────────────────────────────────────────
    def _goto(self, idx: int, user: bool = True) -> None:
        if not self.states:
            return
        idx = max(1, min(idx, self.total or len(self.states)))
        if user and self.follow.isChecked():
            self.follow.setChecked(False)
        self._show_chunk(idx)

    def _show_chunk(self, idx: int) -> None:
        if not 1 <= idx <= len(self.states):
            return
        self._save_current_edit()  # oczekujące poprawki poprzedniego chunka
        self.current_idx = idx
        self.strip.set_current(idx)
        s = self.states[idx - 1]

        self.en_view.setPlainText(s["en"])

        self._loading = True
        if s["pl"]:
            self.pl_view.setPlaceholderText("")
            self.pl_view.setPlainText(align_pl_to_en(s["en"], _clean_pl(s["pl"])))
        else:
            self.pl_view.setPlainText("")
            if s["status"] == "running":
                self.pl_view.setPlaceholderText(
                    "Generowanie…   (wynik pojawi się po zakończeniu fragmentu)")
            else:
                self.pl_view.setPlaceholderText(
                    "(oczekuje — fragment zostanie przetłumaczony po osiągnięciu tego miejsca)")
        self._loading = False

        # Edycja: ukończone chunki (ok / edited / error z treścią), nie w trakcie retry
        editable = bool(s["pl"]) and self.retry_worker is None
        self.pl_view.setReadOnly(not editable)
        self.pl_view.setProperty("editable", "true" if editable else "false")
        _polish(self.pl_view)

        self.nav_label.setText(f"chunk {idx} / {self.total}")
        self.chunk_info.setText(self._info_text(idx))

        self.btn_prev.setEnabled(idx > 1)
        self.btn_next.setEnabled(idx < self.total)

    def _info_text(self, idx: int) -> str:
        s = self.states[idx - 1]
        if s["status"] == "pending":
            info = "oczekuje"
        elif s["status"] == "running":
            info = "generowanie…"
        else:
            info = (f'linie EN {s["lines_en"]} · PL {s["lines_pl"]}'
                    f' · {s["took"]:.1f} s · ~{_num(s["tokens"])} tok.')
            if s.get("edited"):
                info += "  ·  edytowany ręcznie"
            if s.get("dirty"):
                info += "  ·  niezapisane"
            if s["status"] == "error" and s["reason"]:
                info += f"  —  {s['reason']}"
        return info

    # ── synchronizacja scrolla ───────────────────────────────────────────────
    def _sync_from_en(self, value: int) -> None:
        self._sync(self.en_view, self.pl_view, value)

    def _sync_from_pl(self, value: int) -> None:
        self._sync(self.pl_view, self.en_view, value)

    def _sync(self, src: QPlainTextEdit, dst: QPlainTextEdit, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            sb = src.verticalScrollBar()
            db = dst.verticalScrollBar()
            db.setValue(round(db.maximum() * sb.value() / sb.maximum()) if sb.maximum() else 0)
        finally:
            self._syncing = False

    # ── edycja PL ────────────────────────────────────────────────────────────
    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        # fokus na panel PL = użytkownik zaczyna poprawki → nie skaczemy mu pod kursor
        if obj is self.pl_view and event.type() == event.Type.FocusIn:
            if self.follow.isChecked():
                self.follow.setChecked(False)
        return super().eventFilter(obj, event)

    def _pl_text_changed(self) -> None:
        if self._loading or not self.states or not self.current_idx:
            return
        if self.pl_view.isReadOnly():
            return
        s = self.states[self.current_idx - 1]
        s["dirty"] = True
        self.chunk_info.setText(self._info_text(self.current_idx))
        self.save_timer.start()  # restart debouncu autozapisu

    def _chunks_dir(self) -> Path | None:
        if not self.output_path:
            return None
        return Path(self.output_path).parent / "chunks"

    def _save_current_edit(self) -> None:
        """Zapisuje ręczne poprawki bieżącego chunka do chunks/chunk_NNN.txt."""
        self.save_timer.stop()
        idx = self.current_idx
        if not idx or not self.states or not self.states[idx - 1].get("dirty"):
            return
        s = self.states[idx - 1]
        chunks_dir = self._chunks_dir()
        if not chunks_dir:
            return
        text = self.pl_view.toPlainText()
        try:
            (chunks_dir / f"chunk_{idx:03d}.txt").write_text(text, encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "Zapis", f"Nie udało się zapisać chunka {idx}: {e}")
            return
        s["pl"] = text
        s["dirty"] = False
        s["edited"] = True
        s["lines_pl"] = tr.count_lines(text)
        if s["lines_pl"] != s["lines_en"]:
            s["status"] = "error"
            s["reason"] = f"edycja: niezgodność linii EN {s['lines_en']} ≠ PL {s['lines_pl']}"
        else:
            s["status"] = "edited"
            s["reason"] = ""
        self.strip.set_state(idx, s["status"])
        self.chunk_info.setText(self._info_text(idx))
        self._refresh_stats()
        self._rebuild_errors()

    # ── status / animacje ────────────────────────────────────────────────────
    def _set_status(self, text: str, kind: str = "idle") -> None:
        color = {"idle": DIM, "run": ACCENT, "ok": OK, "err": ERR,
                 "wait": "#e3b341"}.get(kind, DIM)
        self.status_dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        self.status_text.setText(text)

    def _pulse_tick(self) -> None:
        self._pulse_flip = not self._pulse_flip
        state = "running_a" if self._pulse_flip else "running_b"
        for i, s in enumerate(self.states, 1):
            if s["status"] == "running":
                self.strip.set_state(i, state)
        if self._gen_idx and self.current_idx == self._gen_idx:
            self._dot_n += 1
            dots = "." * (1 + (self._dot_n % 3))
            self.pl_view.setPlaceholderText(f"Generowanie{dots}   (wynik pojawi się po zakończeniu fragmentu)")

    def _flash_pl(self) -> None:
        anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=700)
        anim.valueChanged.connect(self._pl_flash_step)
        anim.finished.connect(lambda: self.pl_view.setStyleSheet(""))
        anim.start()
        self._anim = anim

    def _pl_flash_step(self, v) -> None:
        alpha = max(0, int(110 * (1 - float(v))))
        self.pl_view.setStyleSheet(
            f"QPlainTextEdit#plView {{ border: 2px solid rgba(77, 159, 255, {alpha}); }}")

    # ── statystyki ───────────────────────────────────────────────────────────
    def _done_count(self) -> int:
        return sum(1 for s in self.states if s["status"] in ("ok", "edited", "error"))

    def _refresh_stats(self) -> None:
        if not self.states:
            return
        done = sum(1 for s in self.states if s["status"] in ("ok", "edited"))
        errs = sum(1 for s in self.states if s["status"] == "error")
        processed = done + errs
        lines_en = sum(s["lines_en"] for s in self.states)
        lines_pl = sum(s["lines_pl"] for s in self.states)
        chars = sum(len(s["pl"]) for s in self.states)
        tokens = sum(s["tokens"] for s in self.states)

        self.stats["postep"].setText(f"{_num(processed)} / {_num(self.total)}")
        self.stats["done"].setText(str(done))
        self.stats["err"].setText(str(errs))
        self.stats["len_en"].setText(_num(lines_en))
        self.stats["len_pl"].setText(_num(lines_pl))
        self.stats["chars"].setText(_num(chars))
        self.stats["tokens"].setText("~" + _num(tokens))

        if self.t_start and processed > 0:
            elapsed = time.time() - self.t_start
            avg = elapsed / processed
            self.stats["speed"].setText(f"{_num(int(chars / elapsed))} zn./s")
            self.stats["avg"].setText(f"{avg:.1f} s")
            remaining = self.total - processed
            self.stats["eta"].setText(_fmt_eta(avg * remaining) if remaining > 0 else "—")

        self._refresh_merge_btn()

    def _refresh_merge_btn(self) -> None:
        ready = (bool(self.states) and self.total > 0
                 and self._done_count() == self.total and self.retry_worker is None)
        self.merge_btn.setEnabled(ready)

    def _rebuild_errors(self) -> None:
        while self.errors_box.count():
            item = self.errors_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        failed = [(i, s) for i, s in enumerate(self.states, 1) if s["status"] == "error"]
        self.errors_header.setText(f"BŁĘDY ({len(failed)})")
        if not failed:
            lbl = QLabel("brak błędów")
            lbl.setObjectName("errNone")
            self.errors_box.addWidget(lbl)
            return

        retrying = self.retry_worker is not None
        for idx, s in failed:
            row = QFrame()
            row.setObjectName("errFrame")
            h = QHBoxLayout(row)
            h.setContentsMargins(8, 6, 8, 6)
            h.setSpacing(6)
            txt = QLabel(f"chunk {idx} — {s['reason'] or 'błąd'}")
            txt.setObjectName("errRow")
            txt.setWordWrap(True)
            btn = QPushButton("ponów")
            btn.setFixedWidth(56)
            btn.setEnabled(not retrying)
            btn.clicked.connect(lambda _=False, n=idx: self._start_retry([n]))
            h.addWidget(txt, 1)
            h.addWidget(btn)
            self.errors_box.addWidget(row)

        if len(failed) > 1:
            all_btn = QPushButton("ponów wszystkie")
            all_btn.setEnabled(not retrying)
            all_btn.clicked.connect(lambda _=False: self._start_retry([i for i, _ in failed]))
            self.errors_box.addWidget(all_btn)

    # ── zdarzenia z wątków ───────────────────────────────────────────────────
    def on_worker_event(self, ev: dict) -> None:
        t = ev.get("type")

        if t == "start":
            self.total = ev["total_chunks"]
            self.output_path = ev["output_path"]
            resumed = bool(ev.get("resumed"))
            old_states = self.states if (resumed and len(self.states) == self.total) else []
            self.states = [
                {"status": "pending", "en": en, "pl": "", "reason": "", "took": 0.0,
                 "tokens": 0, "lines_en": tr.count_lines(en), "lines_pl": 0,
                 "edited": False, "dirty": False}
                for en in ev["chunks_en"]
            ]
            # Resume: wczytaj gotowe chunki (nawet z markerem błędu)
            pl_loaded = ev.get("chunks_pl") or []
            for i, s in enumerate(self.states, 1):
                got = pl_loaded[i - 1] if i - 1 < len(pl_loaded) else ""
                if not got:
                    continue
                s["pl"] = got
                s["lines_pl"] = tr.count_lines(_clean_pl(got))
                if tr.CHUNK_ERROR_MARKER in got:
                    s["status"] = "error"
                    s["reason"] = "błąd z poprzedniej sesji"
                else:
                    s["status"] = "ok"
                if i - 1 < len(old_states):
                    prev = old_states[i - 1]
                    if prev.get("edited") and prev.get("pl") == got:
                        s["edited"] = True
            self.t_start = time.time()
            self.finalized = False
            self._stop_requested = False
            self._gen_idx = 0
            self.model_label.setText(f'{ev["backend"]}  ·  {ev["model"] or "?"}')
            self.out_label.setText(_elide(self.output_path, 240))
            self.out_label.setToolTip(self.output_path)
            self.progress.setRange(0, self.total)
            self.progress.setValue(self._done_count())
            self.strip.build(self.total)
            self._show_chunk(1)
            self._set_status("generowanie", "run")
            self._refresh_stats()
            self._rebuild_errors()
            self.pulse_timer.start()
            self.clock_timer.start()
            self.btn_stop.setEnabled(True)
            self.btn_resume.setEnabled(False)

        elif t == "chunk_start":
            idx = ev["idx"]
            self._gen_idx = idx
            s = self.states[idx - 1]
            s["status"] = "running"
            self.strip.set_state(idx, "running_a" if self._pulse_flip else "running_b")
            # podążanie skacze dopiero na chunk_done — tu tylko odświeżamy,
            # gdy użytkownik akurat patrzy na ten chunk (placeholder generowania)
            if self.current_idx == idx:
                self._show_chunk(idx)
            self._set_status(f"generowanie — chunk {idx} / {self.total}", "run")

        elif t == "chunk_done":
            idx = ev["idx"]
            s = self.states[idx - 1]
            s["status"] = "ok" if ev["ok"] else "error"
            s["pl"] = ev.get("pl") or ""
            s["reason"] = ev.get("reason") or ""
            s["took"] = ev.get("took") or 0.0
            s["tokens"] = ev.get("tokens") or 0
            s["lines_en"] = ev.get("lines_en") or s["lines_en"]
            s["lines_pl"] = ev.get("lines_pl") or 0
            s["edited"] = False   # regeneracja nadpisuje ręczne poprawki
            s["dirty"] = False
            if self._gen_idx == idx:
                self._gen_idx = 0
            self.strip.set_state(idx, s["status"])
            self.strip.flash(idx)
            self.progress.setValue(self._done_count())
            if self.follow.isChecked():
                self._goto(idx, user=False)
            elif self.current_idx == idx:
                self._show_chunk(idx)
            self._refresh_stats()
            self._rebuild_errors()
            if ev["ok"]:
                self._flash_pl()

        elif t == "chunk_aborted":
            idx = ev.get("idx")
            if self._gen_idx == idx:
                self._gen_idx = 0
            if idx and 1 <= idx <= len(self.states):
                s = self.states[idx - 1]
                s["status"] = "pending"
                self.strip.set_state(idx, "pending")
                if self.current_idx == idx:
                    self._show_chunk(idx)

        elif t == "stopped":
            reason = ev.get("stop_reason")
            self.pulse_timer.stop()
            self.clock_timer.stop()
            self._gen_idx = 0
            done = self._done_count()
            self.progress.setValue(done)
            when = {"time": " (limit czasu)", "keyboard": " (przerwano)"}.get(reason, "")
            nxt = ev.get("next_chunk", done + 1)
            self._set_status(f"zatrzymano{when} — chunk {min(nxt, self.total)} / {self.total}", "wait")
            self.btn_stop.setEnabled(False)
            self.btn_resume.setEnabled(done < self.total)
            self._refresh_stats()
            self._rebuild_errors()

        elif t == "finished":
            fails = ev.get("failed_chunks") or []
            self.finalized = bool(ev.get("finalized", True))
            self.progress.setValue(self.total)
            self.pulse_timer.stop()
            self.clock_timer.stop()
            self.btn_stop.setEnabled(False)
            self.btn_resume.setEnabled(False)
            # pytanie o scalenie — zanim ustawiemy status, _merge go nadpisze
            merged_now = False
            if not fails and not self.finalized:
                ret = QMessageBox.question(
                    self, "Scalanie",
                    "Wszystkie chunki wygenerowane.\nScalić teraz do pliku wyjściowego?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if ret == QMessageBox.Yes:
                    self._merge()
                    merged_now = True
            if not merged_now:
                if not self.finalized:
                    if fails:
                        self._set_status(f"wygenerowano z błędami ({len(fails)}) — popraw i scal", "err")
                    else:
                        self._set_status("gotowe — scal, gdy skończysz poprawki", "wait")
                elif fails:
                    self._set_status(f"zakończono z błędami ({len(fails)})", "err")
                else:
                    self._set_status("zakończono", "ok")
            self.open_btn.setEnabled(self.finalized)
            self._refresh_stats()
            self._rebuild_errors()

        elif t == "retry_finished":
            self.retry_worker = None
            self.finalized = False   # regeneracja unieważnia ewentualne scalenie
            self.pulse_timer.stop()
            errs = sum(1 for s in self.states if s["status"] == "error")
            if errs:
                self._set_status(f"zakończono z błędami ({errs}) — popraw i scal", "err")
            else:
                self._set_status("gotowe — scal, gdy skończysz poprawki", "wait")
            self.btn_resume.setEnabled(self._done_count() < self.total)
            self._refresh_stats()
            self._rebuild_errors()
            if self.current_idx:
                self._show_chunk(self.current_idx)  # odśwież edytowalność/treść

        elif t == "worker_error":
            self.pulse_timer.stop()
            if self.retry_worker is not None:
                self.retry_worker = None
                self.btn_resume.setEnabled(self._done_count() < self.total)
                if self.current_idx:
                    self._show_chunk(self.current_idx)
            else:
                self.btn_resume.setEnabled(self._done_count() < self.total)
            self._set_status(f"błąd: {ev.get('message', '?')}", "err")
            self._refresh_stats()
            self._rebuild_errors()

    # ── akcje ────────────────────────────────────────────────────────────────
    def _max_time_seconds(self) -> int | None:
        mins = self.stop_after.value()
        return mins * 60 if mins > 0 else None

    def _running_worker(self):
        for w in (self.translate_worker, self.retry_worker, self.resume_worker):
            if w is not None and w.isRunning():
                return w
        return None

    def _release_stale_lock(self) -> None:
        """Po terminate() lock sesji nie zostaje usunięty — clear it."""
        if not self.output_path:
            return
        lock = Path(self.output_path).parent / "chunks" / "lock"
        try:
            if lock.exists():
                lock.unlink()
        except OSError:
            pass

    def _start_worker(self) -> None:
        # Wznowienie z zera zamiast nowej sesji: jeśli istnieje niedokończona
        # sesja dla tego pliku — zapytaj.
        if self.output_path:
            candidates = []
            try:
                meta = tr.load_session(self.output_path)
            except SystemExit:
                meta = None
            if meta and meta.get("state") != "finished":
                candidates = [{
                    "run_dir": str(Path(meta["chunks_dir"]).parent.resolve()),
                    "output_path": str(meta["output_path"]),
                    "next_chunk": int(meta.get("next_chunk", 1) or 1),
                    "total_chunks": int(meta.get("total_chunks", 0) or 0),
                    "stopped_at": None,
                }]
        else:
            candidates = tr.find_resumable_sessions(
                self.input_path, self.cfg.get("output_dir", "output"))

        if candidates:
            s = candidates[0]
            when = f", {s['stopped_at']}" if s.get("stopped_at") else ""
            ret = QMessageBox.question(
                self, "Przerwana sesja",
                f"Znalazłem niedokończoną sesję dla tego pliku:\n"
                f"{s['run_dir']}\n\n"
                f"(chunk {s['next_chunk']} / {s['total_chunks']}{when})\n\n"
                f"Wznowić tłumaczenie od tego miejsca?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret == QMessageBox.Yes:
                self._start_resume(s["output_path"])
                return

        self.translate_worker = TranslateWorker(self.input_path, self.cfg, self.output_path,
                                                max_time=self._max_time_seconds())
        self.translate_worker.signals.event.connect(self.on_worker_event)
        self.translate_worker.start()
        self._set_status("łączenie z modelem…", "run")

    def _stop_translation(self) -> None:
        w = self._running_worker()
        if w is None or not getattr(w, "stop_event", None):
            return
        self._stop_requested = True
        w.stop_event.set()
        self.btn_stop.setEnabled(False)
        self._set_status("zatrzymuję… (natychmiast przerywa bieżący chunk)", "wait")

    def _resume_session(self) -> None:
        """Wznów bieżącą/przerwaną sesję (przycisk „Wznów” po zatrzymaniu)."""
        if self._running_worker() is not None or not self.output_path:
            return
        if not self.states:
            return
        self._save_current_edit()
        self._start_resume(self.output_path)

    def _start_resume(self, output_path: str) -> None:
        if self._running_worker() is not None:
            return
        self._stop_requested = False
        self.output_path = output_path
        self.resume_worker = ResumeWorker(output_path, self.cfg,
                                          max_time=self._max_time_seconds())
        self.resume_worker.signals.event.connect(self.on_worker_event)
        self.resume_worker.start()
        self._set_status("wznawianie…", "run")
        self.pulse_timer.start()
        self.clock_timer.start()

    def _merge(self) -> None:
        """Skleja chunki (z ewentualnymi ręcznymi poprawkami) do pliku wyjściowego."""
        if not self.output_path or not self.states:
            return
        if self._done_count() != self.total:
            return
        self._save_current_edit()
        errs = [i for i, s in enumerate(self.states, 1) if s["status"] == "error"]
        if errs:
            ret = QMessageBox.question(
                self, "Rozjazd linii",
                f"Chunki z niezgodną liczbą linii: {', '.join(map(str, errs))}.\n"
                "Zeskalowany plik może być niespójny w tych miejscach.\n\nScalić mimo to?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        self.merge_btn.setEnabled(False)
        self._set_status("sklejanie chunków…", "run")
        try:
            tr.stitch_session(self.output_path, verbose=False)
        except Exception as e:
            QMessageBox.critical(self, "Scalanie", f"Scalanie nie powiodło się:\n{e}")
            self._refresh_merge_btn()
            return
        self.finalized = True
        self.merge_btn.setText("Scal ponownie")
        if errs:
            self._set_status(f"zscalono (z rozjazdami: {len(errs)}) — {Path(self.output_path).name}", "err")
        else:
            self._set_status(f"zscalono — {Path(self.output_path).name}", "ok")
        self._refresh_merge_btn()

    def _start_retry(self, chunks: list[int]) -> None:
        if self.retry_worker is not None or not self.output_path:
            return
        edited = [n for n in chunks if 1 <= n <= len(self.states) and self.states[n - 1].get("edited")]
        if edited:
            ret = QMessageBox.question(
                self, "Nadpisać edycje?",
                f"Chunki {', '.join(map(str, edited))} były edytowane ręcznie.\n"
                "Ponowne generowanie nadpisze te zmiany. Kontynuować?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        self._save_current_edit()
        self._set_status(f"ponawianie — chunki {', '.join(map(str, chunks))}", "run")
        self._rebuild_errors()  # przerysuj z wyłączonymi przyciskami
        if self.current_idx:
            self._show_chunk(self.current_idx)  # blokada edycji na czas retry
        self.retry_worker = RetryWorker(self.output_path, chunks, self.cfg)
        self.retry_worker.signals.event.connect(self.on_worker_event)
        self.retry_worker.start()
        self.pulse_timer.start()

    def _open_output_dir(self) -> None:
        if self.output_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.output_path).parent)))

    def closeEvent(self, event):  # noqa: N802 (Qt API)
        w = self._running_worker()
        if w is not None and getattr(w, "stop_event", None):
            ret = QMessageBox.question(
                self, "Tłumaczenie w toku",
                "Tłumaczenie jest w toku. Zatrzymać (sesja zostanie zapisana) i zamknąć okno?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                event.ignore()
                return
            # łagodnie: stop → abort chunka → sesja "stopped" dostępna do wznowienia
            w.stop_event.set()
            w.wait(8000)
            if w.isRunning():
                w.terminate()
                w.wait(1500)
            event.accept()
            self._release_stale_lock()
            return
        # zapisz oczekujące poprawki
        self._save_current_edit()
        # niescalone chunki → zaproponuj scalenie przed zamknięciem
        if (self.states and self.output_path and not self.finalized
                and self._done_count() == self.total):
            ret = QMessageBox.question(
                self, "Niescalone chunki",
                "Chunki nie zostały jeszcze scalone do pliku wyjściowego.\n"
                "Scalić przed zamknięciem?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if ret == QMessageBox.Yes:
                try:
                    tr.stitch_session(self.output_path, verbose=False)
                except Exception as e:
                    QMessageBox.critical(self, "Scalanie", f"Scalanie nie powiodło się:\n{e}")
        event.accept()


# ── Edytor prompt.yaml ────────────────────────────────────────────────────────
class PromptEditorDialog(QDialog):
    def __init__(self, config_path: str):
        super().__init__()
        self.path = Path(config_path)
        self.setWindowTitle("prompt.yaml — przegląd i korekta przed tłumaczeniem")
        self.resize(940, 720)

        v = QVBoxLayout(self)
        info = QLabel("Zmiany zostaną zapisane do prompt.yaml i użyte w tym oraz "
                      "wszystkich kolejnych tłumaczeniach.")
        info.setObjectName("dialogInfo")
        info.setWordWrap(True)
        v.addWidget(info)

        self.editor = QPlainTextEdit()
        self.editor.setFont(mono_font(10))
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        try:
            self.editor.setPlainText(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.editor.setPlainText("# nowy plik prompt.yaml\n")
        v.addWidget(self.editor, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Anuluj")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Zapisz i kontynuuj")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        v.addLayout(row)

    def _save(self) -> None:
        text = self.editor.toPlainText()
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as e:
            QMessageBox.critical(self, "Błąd YAML",
                                 f"prompt.yaml nie jest poprawnym YAML-em:\n\n{e}")
            return
        self.path.write_text(text, encoding="utf-8")
        self.accept()


# ── punkty wejścia (wołane z translator.py) ──────────────────────────────────
def run_prompt_editor(config_path: str = "prompt.yaml") -> bool:
    """Otwiera edytor prompt.yaml. True = zapisano i kontynuuj."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_qss())
    dlg = PromptEditorDialog(config_path)
    return dlg.exec() == QDialog.Accepted


def run_gui(args, cfg: dict) -> None:
    """Uruchamia główne okno tłumaczenia (blokuje do zamknięcia)."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(build_qss())
    win = TranslatorWindow(args, cfg)
    win.show()
    app.exec()


if __name__ == "__main__":
    print("Uruchom przez: uv run translator translate <plik> --gui")
    sys.exit(1)
