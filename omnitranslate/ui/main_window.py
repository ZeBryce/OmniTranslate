"""
Main application window.

Provides the primary UI for OmniTranslate:
  - Region selection trigger
  - Start / Stop monitor toggle
  - Source / target language dropdowns
  - OCR interval slider
  - OCR result and translation text areas with copy buttons
  - Status bar with performance metrics

OCR runs in a ThreadPoolExecutor and translation in a QThread, keeping
the UI responsive.
"""

import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtCore import (
    Qt, QTimer, QThread, pyqtSlot, pyqtSignal, QObject, QSettings,
)
from PyQt5.QtGui import QFont, QIcon, QTextCursor, QKeySequence
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QLabel, QTextEdit, QSlider,
    QGroupBox, QStatusBar, QApplication, QSplitter,
    QFrame, QSizePolicy, QRadioButton, QButtonGroup,
    QTabWidget, QCheckBox, QLineEdit, QShortcut, QFormLayout,
)

from omnitranslate.capture.selector import RegionSelector
from omnitranslate.capture.grabber import ScreenGrabber
from omnitranslate.ocr.engine import OCREngine
from omnitranslate.translator.engine import (
    TranslationEngine, get_supported_languages, _MAX_CONTEXT_WORDS,
    ENGINES, ENGINE_GROUPS, EngineConfig, config_from_env,
)

_MAX_CONTENT_CHARS = 5000


# ══════════════════════════════════════════════════════════════════════
#  Debug Monitor
# ══════════════════════════════════════════════════════════════════════

class _DebugMonitor(QWidget):
    """Floating window that logs every OCR / translation pipeline event."""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("OmniTranslate — Debug Monitor")
        self.resize(720, 600)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Cascadia Code", 10))
        self._log.setStyleSheet(
            "background-color:#181825; color:#cdd6f4; "
            "border:1px solid #45475a; border-radius:4px; padding:6px;"
        )
        lay.addWidget(self._log)

        btn = QPushButton("Clear")
        btn.clicked.connect(self._log.clear)
        lay.addWidget(btn)

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "_DebugMonitor{background-color:#1e1e2e;} "
            "QPushButton{background:#313244;color:#cdd6f4;border:1px solid #45475a;"
            "border-radius:6px;padding:6px 16px;font-size:13px;}"
        )

    def log(self, category: str, content: str):
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log.append(
            f"[{ts}]  ═══ {category} ═══\n{content}\n{'─' * 52}"
        )
        doc = self._log.document()
        if doc.blockCount() > 3000:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.Start)
            cursor.movePosition(
                QTextCursor.Down, QTextCursor.KeepAnchor,
                doc.blockCount() - 1500,
            )
            cursor.removeSelectedText()
        bar = self._log.verticalScrollBar()
        bar.setValue(bar.maximum())


# ══════════════════════════════════════════════════════════════════════
#  Workers
# ══════════════════════════════════════════════════════════════════════

class _OCRWorker(QObject):
    """Screen capture + OCR in a Python thread pool.

    PaddlePaddle's native inference engine segfaults inside QThread
    (access violation in ``tensor_copy_from_cpu``).  Using a plain
    Python ``ThreadPoolExecutor`` avoids the conflict while still
    keeping the UI responsive.
    """

    result_ready = pyqtSignal(str, float, int)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._grabber = ScreenGrabber()
        self._ocr = None
        self._pool = ThreadPoolExecutor(max_workers=1)

    def set_language(self, lang_code):
        """Schedule a language switch (thread-safe: runs in the pool)."""
        self._pool.submit(self._do_set_language, lang_code)

    def process(self, x, y, w, h, req_id):
        """Schedule an OCR job (thread-safe: runs in the pool)."""
        self._pool.submit(self._do_process, x, y, w, h, req_id)

    def _do_set_language(self, lang_code):
        try:
            if self._ocr is None:
                self._ocr = OCREngine()
            self._ocr.set_language(lang_code)
        except Exception as e:
            self.error.emit(f"OCR lang switch failed: {e}")

    def _do_process(self, x, y, w, h, req_id):
        try:
            if self._ocr is None:
                self._ocr = OCREngine()

            t_start = time.perf_counter()
            image = self._grabber.grab(x, y, w, h)
            text = self._ocr.recognize(image)
            t_ms = (time.perf_counter() - t_start) * 1000
            self.result_ready.emit(text, t_ms, req_id)
        except Exception as e:
            self.error.emit(f"OCR Error: {e}")

    def cleanup(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._grabber.close()


class _TranslationWorker(QObject):
    """Handles translation (streaming via an OpenAI-compatible LLM)."""

    # (trans_text, trans_ms, req_id)
    chunk_ready = pyqtSignal(str, float, int)
    result_ready = pyqtSignal(str, float, int)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._engine = TranslationEngine(source="auto", target="en")
        self._stop_requested = False

    def request_stop(self):
        """Ask an in-progress stream to stop (called from the main thread)."""
        self._stop_requested = True

    @pyqtSlot(str, str, str, int, str, str, object)
    def translate(self, text, source, target, req_id, mode, prev_trans, config):
        try:
            self._engine.configure(config)
            self._engine.set_languages(source, target)
            t_start = time.perf_counter()

            final_text = ""
            for partial_text in self._engine.translate_stream(text, mode, prev_trans):
                if self._stop_requested:
                    return
                final_text = partial_text
                t_ms = (time.perf_counter() - t_start) * 1000
                self.chunk_ready.emit(partial_text, t_ms, req_id)

            t_ms = (time.perf_counter() - t_start) * 1000
            self.result_ready.emit(final_text, t_ms, req_id)
        except Exception as e:
            # Don't fail hard on translation errors (network issues);
            # an empty result releases the backpressure flag.
            self.error.emit(str(e))
            self.result_ready.emit("", 0, req_id)


class MainWindow(QMainWindow):
    """Primary OmniTranslate window."""

    # Trigger Translation (text, src, tgt, req_id, mode, prev_trans, EngineConfig)
    _do_trans = pyqtSignal(str, str, str, int, str, str, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OmniTranslate")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(640, 760)
        # Tall enough for the Engine tab plus both text areas, but never
        # taller than the screen's free area
        avail = QApplication.primaryScreen().availableGeometry()
        self.resize(720, max(760, min(960, avail.height() - 60)))

        # ── State ─────────────────────────────────────────────────────
        self._region = None          # (x, y, w, h)
        self._running = False
        self._processing_ocr = False # Backpressure for OCR only
        self._req_counter = 0        # Monotonic ID for OCR requests
        
        self._last_ocr_content = None # Exact text from last OCR

        self._prev_trans_tgt = ""
        self._trans_head = ""
        self._last_trans_req_time = 0.0

        # Translation throttle: only 1 active translation at a time
        self._is_translating = False
        self._pending_trans_text = None  # Latest text waiting to be translated
        self._trans_gen = 0              # Bumped on reset; stale results are dropped
        self._trans_error = ""           # Last translation error, shown in status bar
        self._selector = None

        # Engine choice + API keys, stored per user (Windows registry),
        # never in the app folder, so sharing the folder can't leak keys.
        self._settings = QSettings()
        self._engine_widgets = {}

        # ── Debug (hidden from users; F12 toggles it) ─────────────────
        self._debug = _DebugMonitor(self)
        QShortcut(QKeySequence("F12"), self, activated=self._toggle_debug)

        # ── Workers ───────────────────────────────────────────────────

        # OCR: uses ThreadPoolExecutor (PaddlePaddle segfaults in QThread)
        self._worker_ocr = _OCRWorker()
        self._worker_ocr.result_ready.connect(self._on_ocr_result)
        self._worker_ocr.error.connect(self._on_ocr_error)

        # Translation: QThread is fine for network-bound LLM calls
        self._thread_trans = QThread()
        self._worker_trans = _TranslationWorker()
        self._worker_trans.moveToThread(self._thread_trans)
        self._do_trans.connect(self._worker_trans.translate)
        self._worker_trans.chunk_ready.connect(self._on_trans_chunk)
        self._worker_trans.result_ready.connect(self._on_trans_result)
        self._worker_trans.error.connect(self._on_trans_error)
        self._thread_trans.start()

        # ── Timer ─────────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        # ── UI ────────────────────────────────────────────────────────
        self._build_ui()
        self._apply_styles()

        # Sync initial language selection
        self._on_language_changed()

    # ══════════════════════════════════════════════════════════════════
    #  UI Construction
    # ══════════════════════════════════════════════════════════════════

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # ── Title bar ─────────────────────────────────────────────────
        title = QLabel("🔍 OmniTranslate")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        # ── Tabs: Controls (everything for translating) | Engine ──────
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_controls_tab(), "Controls")
        self._tabs.addTab(self._build_engine_tab(), "Engine")
        root.addWidget(self._tabs, 1)

        # ── Status bar ────────────────────────────────────────────────
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready — select a region to begin.")

        # Compact group boxes so the window fits 1080p screens at 125%
        for group in central.findChildren(QGroupBox):
            group.layout().setContentsMargins(10, 4, 10, 8)

    def _build_controls_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(10)

        # ── Buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._btn_select = QPushButton("📐 Select Region")
        self._btn_select.setToolTip("Click to select a screen region (or Ctrl+Shift+S)")
        self._btn_select.clicked.connect(self._on_select_region)
        btn_row.addWidget(self._btn_select)

        self._btn_toggle = QPushButton("▶  Start")
        self._btn_toggle.setToolTip("Start / stop real-time OCR & translation")
        self._btn_toggle.setEnabled(False)
        self._btn_toggle.clicked.connect(self._on_toggle)
        btn_row.addWidget(self._btn_toggle)
        lay.addLayout(btn_row)

        # ── Language row ──────────────────────────────────────────────
        lang_box = QGroupBox("Languages")
        lang_lay = QHBoxLayout(lang_box)

        lang_lay.addWidget(QLabel("Source:"))
        self._combo_src = QComboBox()
        self._combo_src.addItem("Auto Detect", "auto")
        self._populate_language_combo(self._combo_src)
        self._combo_src.currentIndexChanged.connect(self._on_language_changed)
        lang_lay.addWidget(self._combo_src, 1)

        lang_lay.addWidget(QLabel("Target:"))
        self._combo_tgt = QComboBox()
        self._populate_language_combo(self._combo_tgt)
        # Default to Chinese (Simplified)
        idx = self._combo_tgt.findData("zh-CN")
        if idx >= 0:
            self._combo_tgt.setCurrentIndex(idx)
        self._combo_tgt.currentIndexChanged.connect(self._on_language_changed)
        lang_lay.addWidget(self._combo_tgt, 1)

        lay.addWidget(lang_box)

        # ── Mode Selection ────────────────────────────────────────────
        mode_box = QGroupBox("Mode")
        mode_lay = QHBoxLayout(mode_box)

        self._mode_group = QButtonGroup(self)
        self._radio_reading = QRadioButton("Reading")
        self._radio_realtime = QRadioButton("Real-time")
        self._radio_realtime.setChecked(True)
        self._radio_realtime.toggled.connect(self._on_mode_changed)
        self._mode_group.addButton(self._radio_reading)
        self._mode_group.addButton(self._radio_realtime)
        mode_lay.addWidget(self._radio_reading)
        mode_lay.addWidget(self._radio_realtime)

        lay.addWidget(mode_box)

        # ── Interval slider ───────────────────────────────────────────
        interval_box = QGroupBox("OCR Interval")
        interval_lay = QHBoxLayout(interval_box)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(500, 3000)
        self._slider.setValue(500)
        self._slider.setTickPosition(QSlider.TicksBelow)
        self._slider.setTickInterval(500)
        self._slider.valueChanged.connect(self._on_interval_changed)
        interval_lay.addWidget(self._slider, 1)

        self._lbl_interval = QLabel("500 ms")
        self._lbl_interval.setMinimumWidth(60)
        interval_lay.addWidget(self._lbl_interval)

        lay.addWidget(interval_box)

        # ── Text areas (splitter) ─────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)

        # OCR result
        ocr_group = QGroupBox("OCR Result")
        ocr_lay = QVBoxLayout(ocr_group)
        self._txt_ocr = QTextEdit()
        self._txt_ocr.setMinimumHeight(40)
        self._txt_ocr.setReadOnly(True)
        self._txt_ocr.setPlaceholderText("Extracted text will appear here…")
        ocr_lay.addWidget(self._txt_ocr)
        btn_row_ocr = QHBoxLayout()
        btn_row_ocr.addStretch()
        self._btn_copy_ocr = QPushButton("📋 Copy OCR Text")
        self._btn_copy_ocr.clicked.connect(
            lambda: self._copy_to_clipboard(self._txt_ocr.toPlainText())
        )
        btn_row_ocr.addWidget(self._btn_copy_ocr)
        ocr_lay.addLayout(btn_row_ocr)
        splitter.addWidget(ocr_group)

        # Translation result
        trans_group = QGroupBox("Translation")
        trans_lay = QVBoxLayout(trans_group)
        self._txt_trans = QTextEdit()
        self._txt_trans.setMinimumHeight(40)
        self._txt_trans.setReadOnly(True)
        self._txt_trans.setPlaceholderText("Translated text will appear here…")
        trans_lay.addWidget(self._txt_trans)
        btn_row_trans = QHBoxLayout()
        btn_row_trans.addStretch()
        self._btn_copy_trans = QPushButton("📋 Copy Translation")
        self._btn_copy_trans.clicked.connect(
            lambda: self._copy_to_clipboard(self._txt_trans.toPlainText())
        )
        btn_row_trans.addWidget(self._btn_copy_trans)
        trans_lay.addLayout(btn_row_trans)
        splitter.addWidget(trans_group)

        lay.addWidget(splitter, 1)

        # ── Region info label ─────────────────────────────────────────
        self._lbl_region = QLabel("No region selected")
        self._lbl_region.setAlignment(Qt.AlignCenter)
        self._lbl_region.setStyleSheet("color: #888;")
        lay.addWidget(self._lbl_region)
        return page

    def _build_engine_tab(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(10, 12, 10, 10)
        lay.setSpacing(12)

        # ── Left: engine list, one exclusive checkbox per row ─────────
        list_box = QGroupBox("Engines")
        list_lay = QVBoxLayout(list_box)
        list_lay.setSpacing(6)
        self._engine_group = QButtonGroup(self)
        self._engine_group.setExclusive(True)
        self._engine_checks = {}
        for group_id, group_title in ENGINE_GROUPS.items():
            header = QLabel(group_title)
            header.setObjectName("engineGroupHeader")
            list_lay.addWidget(header)
            for eid, info in ENGINES.items():
                if info.group != group_id:
                    continue
                cb = QCheckBox(info.label)
                self._engine_group.addButton(cb)
                self._engine_checks[eid] = cb
                list_lay.addWidget(cb)
        list_lay.addStretch()
        lay.addWidget(list_box)

        # ── Right: settings for the ticked engine ─────────────────────
        settings_box = QGroupBox("Settings")
        self._settings_lay = QVBoxLayout(settings_box)
        self._settings_lay.setSpacing(10)

        self._lbl_engine_name = QLabel()
        self._lbl_engine_name.setObjectName("engineName")
        self._settings_lay.addWidget(self._lbl_engine_name)

        self._engine_form = None          # rebuilt per engine
        self._field_edits = {}            # name -> QLineEdit

        self._chk_show_key = QCheckBox("Show keys")
        self._chk_show_key.toggled.connect(self._update_secret_echo)
        self._settings_lay.addWidget(self._chk_show_key)

        self._lbl_engine_hint = QLabel()
        self._lbl_engine_hint.setWordWrap(True)
        self._lbl_engine_hint.setStyleSheet("color: #a6adc8; font-size: 13px;")
        self._settings_lay.addWidget(self._lbl_engine_hint)

        self._lbl_key_note = QLabel(
            "Keys are saved on this PC only (Windows registry), "
            "never in the app folder."
        )
        self._lbl_key_note.setWordWrap(True)
        self._lbl_key_note.setStyleSheet("color: #7f849c; font-size: 12px;")
        self._settings_lay.addWidget(self._lbl_key_note)
        self._settings_lay.addStretch()
        lay.addWidget(settings_box, 1)

        # Restore the saved choice, then wire up change handlers
        current = self._load_engine_choice()
        self._engine_checks[current].setChecked(True)
        self._show_engine_fields(current)
        self._applied_config = self._engine_config()
        self._engine_group.buttonToggled.connect(self._on_engine_toggled)
        return page

    # ── Engine settings ───────────────────────────────────────────────

    def _load_engine_choice(self) -> str:
        """Saved engine id; on first run, import settings from .env if any."""
        s = self._settings
        if not s.contains("engine/current"):
            env = config_from_env()
            for name, value in env.values:
                s.setValue(f"engine/{env.engine}/{name}", value)
            s.setValue("engine/current", env.engine)
        current = s.value("engine/current", "google", type=str)
        return current if current in ENGINES else "google"

    def _current_engine(self) -> str:
        for eid, cb in self._engine_checks.items():
            if cb.isChecked():
                return eid
        return "google"

    def _show_engine_fields(self, eid):
        """Rebuild the settings form for *eid* from its saved values."""
        info = ENGINES[eid]
        s = self._settings

        if self._engine_form is not None:
            # Detach now so the old form never overlaps the new one,
            # even before deleteLater() runs
            self._settings_lay.removeWidget(self._engine_form)
            self._engine_form.setParent(None)
            self._engine_form.deleteLater()

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self._field_edits = {}
        for f in info.fields:
            edit = QLineEdit(s.value(f"engine/{eid}/{f.name}", "", type=str))
            placeholder = f.placeholder or ("optional" if f.optional else "")
            edit.setPlaceholderText(placeholder)
            edit.setProperty("secret", f.secret)
            edit.textChanged.connect(self._save_engine_fields)
            edit.editingFinished.connect(self._apply_engine_config)
            form.addRow(f"{f.label}:", edit)
            self._field_edits[f.name] = edit
        if not info.fields:
            note = QLabel("No setup needed — just tick it and translate.")
            note.setStyleSheet("color: #a6e3a1;")
            form.addRow(note)
        self._settings_lay.insertWidget(1, form_widget)
        self._engine_form = form_widget

        has_secret = any(f.secret for f in info.fields)
        self._chk_show_key.setVisible(has_secret)
        self._lbl_key_note.setVisible(bool(info.fields))
        self._update_secret_echo()
        self._lbl_engine_name.setText(info.label)
        self._lbl_engine_hint.setText(info.hint)

    @pyqtSlot()
    def _update_secret_echo(self):
        mode = QLineEdit.Normal if self._chk_show_key.isChecked() else QLineEdit.Password
        for edit in self._field_edits.values():
            if edit.property("secret"):
                edit.setEchoMode(mode)

    def _engine_config(self) -> EngineConfig:
        return EngineConfig.make(
            self._current_engine(),
            **{name: edit.text().strip() for name, edit in self._field_edits.items()},
        )

    @pyqtSlot()
    def _save_engine_fields(self):
        eid = self._current_engine()
        for name, edit in self._field_edits.items():
            self._settings.setValue(f"engine/{eid}/{name}", edit.text().strip())

    def _on_engine_toggled(self, button, checked):
        if not checked:
            return
        eid = self._current_engine()
        self._settings.setValue("engine/current", eid)
        self._show_engine_fields(eid)
        self._apply_engine_config()

    @pyqtSlot()
    def _apply_engine_config(self):
        """Re-translate with the new engine settings, if they changed."""
        config = self._engine_config()
        if config == self._applied_config:
            return
        self._applied_config = config
        self._trans_error = ""
        self._retranslate_from_scratch()

    def _populate_language_combo(self, combo: QComboBox):
        """Fill a QComboBox with supported languages."""
        langs = get_supported_languages()
        for name in sorted(langs.keys()):
            code = langs[name]
            combo.addItem(name.title(), code)

    # ══════════════════════════════════════════════════════════════════
    #  Helpers
    # ══════════════════════════════════════════════════════════════════

    def _current_mode(self) -> str:
        """Return ``'realtime'`` or ``'reading'`` based on the radio selection."""
        return "realtime" if self._radio_realtime.isChecked() else "reading"

    def _reset_translation_state(self):
        """Forget translation context and drop any in-flight result."""
        self._trans_gen += 1
        self._txt_trans.clear()
        self._pending_trans_text = None
        self._prev_trans_tgt = ""
        self._trans_head = ""
        self._last_trans_req_time = 0.0

    # ══════════════════════════════════════════════════════════════════
    #  Styles
    # ══════════════════════════════════════════════════════════════════

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e2e;
            }
            QLabel {
                color: #cdd6f4;
                font-family: "Segoe UI";
                font-size: 15px;
            }
            QGroupBox {
                color: #89b4fa;
                font-family: "Segoe UI";
                font-weight: bold;
                font-size: 15px;
                border: 1px solid #45475a;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
            }
            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 9px 20px;
                font-family: "Segoe UI";
                font-size: 15px;
            }
            QPushButton:hover {
                background-color: #45475a;
                border-color: #89b4fa;
            }
            QPushButton:pressed {
                background-color: #585b70;
            }
            QPushButton:disabled {
                background-color: #1e1e2e;
                color: #585b70;
                border-color: #313244;
            }
            QComboBox {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 6px 12px;
                font-family: "Segoe UI";
                font-size: 15px;
            }
            QComboBox QAbstractItemView {
                background-color: #313244;
                color: #cdd6f4;
                selection-background-color: #45475a;
                font-size: 14px;
            }
            QTextEdit {
                background-color: #181825;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 10px;
                font-family: "Cascadia Code", "Consolas", monospace;
                font-size: 15px;
                line-height: 1.5;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #45475a;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #89b4fa;
                width: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider::sub-page:horizontal {
                background: #89b4fa;
                border-radius: 3px;
            }
            QStatusBar {
                background-color: #181825;
                color: #a6adc8;
                font-family: "Segoe UI";
                font-size: 13px;
                padding: 4px;
            }
            QRadioButton {
                color: #cdd6f4;
                font-family: "Segoe UI";
                font-size: 15px;
                spacing: 8px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
                border: 2px solid #45475a;
                border-radius: 10px;
                background-color: #181825;
            }
            QRadioButton::indicator:checked {
                border-color: #89b4fa;
                background-color: #89b4fa;
            }
            QRadioButton::indicator:hover {
                border-color: #89b4fa;
            }
            QSplitter::handle {
                background-color: transparent;
            }
            QTabWidget::pane {
                border: 1px solid #45475a;
                border-radius: 8px;
                top: -1px;
            }
            QTabBar {
                font-family: "Segoe UI";
                font-weight: bold;
                font-size: 15px;
            }
            QTabBar::tab {
                background-color: #181825;
                color: #a6adc8;
                border: 1px solid #45475a;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 7px 18px;
                margin-right: 4px;
                min-width: 80px;
            }
            QTabBar::tab:selected {
                background-color: #313244;
                color: #89b4fa;
            }
            QTabBar::tab:hover:!selected {
                color: #cdd6f4;
            }
            QCheckBox {
                color: #cdd6f4;
                font-family: "Segoe UI";
                font-size: 15px;
                spacing: 8px;
            }
            QLineEdit {
                background-color: #181825;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 3px 8px;
                font-family: "Segoe UI";
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #89b4fa;
            }
            QLabel#engineGroupHeader {
                color: #89b4fa;
                font-size: 13px;
                font-weight: bold;
                padding-top: 6px;
            }
            QLabel#engineName {
                font-size: 17px;
                font-weight: bold;
            }
        """)

    # ══════════════════════════════════════════════════════════════════
    #  Slots
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot()
    def _on_select_region(self):
        """Open the overlay to let the user select a region."""
        # Ignore repeat triggers (e.g. hotkey) while an overlay is open
        if self._selector is not None and self._selector.isVisible():
            return

        # Stop any running pipeline first
        self._stop()

        # Temporarily hide main window so it's not captured
        self.hide()
        QApplication.processEvents()

        self._selector = RegionSelector()
        self._selector.region_selected.connect(self._on_region_selected)
        self._selector.selection_done.connect(self._on_selector_closed)
        self._selector.showFullScreen()
        # Qt.Tool windows don't take focus by themselves; needed for Esc
        self._selector.activateWindow()
        self._selector.setFocus()

    @pyqtSlot(int, int, int, int)
    def _on_region_selected(self, x, y, w, h):
        """Handle region selection completion."""
        self._region = (x, y, w, h)
        self._lbl_region.setText(f"Region: ({x}, {y}) — {w}×{h} px")
        self._lbl_region.setStyleSheet("color: #a6e3a1;")
        self._btn_toggle.setEnabled(True)
        self._status.showMessage(f"Region set: ({x}, {y}, {w}×{h}). Press Start.")

        # Clear previous results and reset state
        self._txt_ocr.clear()
        self._last_ocr_content = None
        self._reset_translation_state()

    @pyqtSlot()
    def _on_selector_closed(self):
        """Re-show the main window after the selector closes."""
        self.show()
        self.activateWindow()

    @pyqtSlot()
    def _on_toggle(self):
        """Start or stop the capture-OCR-translate loop."""
        if self._running:
            self._stop()
        else:
            self._start()

    def _start(self):
        self._running = True
        self._processing_ocr = False
        self._btn_toggle.setText("⏹  Stop")
        self._btn_select.setEnabled(False)
        self._timer.start(self._slider.value())
        self._status.showMessage("Running…")

    def _stop(self):
        self._running = False
        self._processing_ocr = False
        self._timer.stop()
        self._btn_toggle.setText("▶  Start")
        self._btn_select.setEnabled(True)
        self._status.showMessage("Stopped.")

    @pyqtSlot()
    def _on_language_changed(self):
        """Update OCR + translation engine when language selection changes."""
        src = self._combo_src.currentData() or "auto"
        self._worker_ocr.set_language(src)
        self._retranslate_from_scratch()

    @pyqtSlot(bool)
    def _on_mode_changed(self, _checked):
        """Switching modes invalidates the realtime context."""
        self._retranslate_from_scratch()

    def _retranslate_from_scratch(self):
        """Reset translation state and re-translate the current OCR text."""
        self._reset_translation_state()
        text = self._last_ocr_content or ""
        if text.strip():
            self._request_translation(text)

    @pyqtSlot(int)
    def _on_interval_changed(self, value):
        """Update the timer interval."""
        self._lbl_interval.setText(f"{value} ms")
        if self._timer.isActive():
            self._timer.setInterval(value)

    # ══════════════════════════════════════════════════════════════════
    #  Pipeline (runs worker in background thread)
    # ══════════════════════════════════════════════════════════════════

    def _tick(self):
        """Submit an OCR job to the worker's thread pool."""
        if not self._region or not self._running:
            return

        if self._processing_ocr:
            return

        self._processing_ocr = True
        self._req_counter += 1
        x, y, w, h = self._region
        self._worker_ocr.process(x, y, w, h, self._req_counter)

    @pyqtSlot(str, float, int)
    def _on_ocr_result(self, text, ms, req_id):
        # Drop results that finish after Stop / region change
        if req_id != self._req_counter:
            return
        self._processing_ocr = False
        if not self._running:
            return

        msg = f"OCR: {ms:.0f}ms"
        if self._trans_error:
            msg += f" | {self._trans_error}"
        self._status.showMessage(msg)

        if text == self._last_ocr_content:
            return
        self._last_ocr_content = text

        self._debug.log("OCR RESULT", f"{ms:.0f}ms\n{text}")

        self._txt_ocr.setPlainText(text.replace("\n", " "))
        bar = self._txt_ocr.verticalScrollBar()
        bar.setValue(bar.maximum())

        if not text.strip():
            # Realtime mode keeps its history across gaps between subtitles
            if self._current_mode() == "reading":
                self._txt_trans.clear()
            return

        self._request_translation(text)

    def _request_translation(self, text):
        """Send text to translation worker. Backpressure + 0.5s throttle."""
        now = time.perf_counter()
        mode = self._current_mode()

        if self._is_translating:
            self._pending_trans_text = text
            return

        if mode == "realtime":
            if self._last_trans_req_time > 0:
                elapsed = now - self._last_trans_req_time
                if elapsed < 0.5:
                    self._pending_trans_text = text
                    if not hasattr(self, "_throttle_timer"):
                        self._throttle_timer = QTimer(self)
                        self._throttle_timer.setSingleShot(True)
                        self._throttle_timer.timeout.connect(self._flush_pending_translation)
                    if not self._throttle_timer.isActive():
                        self._throttle_timer.start(int((0.5 - elapsed) * 1000))
                    return

        self._last_trans_req_time = time.perf_counter()
        self._is_translating = True
        self._pending_trans_text = None
        src = self._combo_src.currentData() or "auto"
        tgt = self._combo_tgt.currentData() or "en"

        # Context + local head only apply to realtime mode on LLM engines;
        # Google / DeepL always translate the full text.
        config = self._engine_config()
        use_context = mode == "realtime" and config.info.is_llm
        prev_tgt = self._prev_trans_tgt if use_context else ""
        context = TranslationEngine._tail_words(prev_tgt, _MAX_CONTEXT_WORDS)
        if prev_tgt and len(prev_tgt) > len(context):
            self._trans_head = prev_tgt[:-len(context)]
        else:
            self._trans_head = ""

        self._debug.log(
            "TRANSLATION REQUEST",
            f"OCR text ({len(text)} chars):\n{text}\n\n"
            f"prev_trans context ({len(context)} chars, "
            f"~{len(context.split())} words):\n{context}\n\n"
            f"head ({len(self._trans_head)} chars, kept locally):\n"
            f"{self._trans_head[:200]}{'…' if len(self._trans_head) > 200 else ''}",
        )

        self._do_trans.emit(text, src, tgt, self._trans_gen, mode, context, config)

    @pyqtSlot()
    def _flush_pending_translation(self):
        if self._pending_trans_text:
            text = self._pending_trans_text
            self._pending_trans_text = None
            self._request_translation(text)

    @pyqtSlot(str, float, int)
    def _on_trans_chunk(self, text, ms, req_id):
        if req_id != self._trans_gen:
            return
        full = self._trans_head + text if self._trans_head else text

        if self._current_mode() == "realtime":
            current = self._txt_trans.toPlainText()
            if not full.startswith(current) or len(full) <= len(current):
                return
            cursor = self._txt_trans.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(full[len(current):])
            self._txt_trans.setTextCursor(cursor)
            self._trim_translation_display()
        else:
            self._update_trans_text_smoothly(full)

        bar = self._txt_trans.verticalScrollBar()
        bar.setValue(bar.maximum())

        current_msg = self._status.currentMessage()
        if "OCR:" in current_msg:
            self._status.showMessage(f"{current_msg.split(' | ')[0]} | Stream: {ms:.0f}ms")

    @pyqtSlot(str, float, int)
    def _on_trans_result(self, text, ms, req_id):
        self._is_translating = False
        # Stale (context was reset) or failed request: don't touch the display
        if req_id != self._trans_gen or not text:
            self._trans_head = ""
            self._flush_pending_translation()
            return

        self._trans_error = ""
        full = self._trans_head + text if self._trans_head else text
        self._trans_head = ""

        self._debug.log(
            "LLM OUTPUT (final)",
            f"Raw LLM ({len(text)} chars):\n{text}\n\n"
            f"After head prepend ({len(full)} chars):\n{full}",
        )

        if self._current_mode() == "realtime":
            current = self._txt_trans.toPlainText()

            prefix_len = 0
            for c1, c2 in zip(current, full):
                if c1 == c2:
                    prefix_len += 1
                else:
                    break

            if prefix_len >= len(current):
                # full extends current perfectly — append the new tail
                if prefix_len < len(full):
                    cursor = self._txt_trans.textCursor()
                    cursor.movePosition(QTextCursor.End)
                    cursor.insertText(full[prefix_len:])
                    self._txt_trans.setTextCursor(cursor)
            else:
                # Prefix diverged — search for overlap between current
                # tail and full's divergent portion
                overlap = self._find_tail_overlap(
                    current, full, prefix_len,
                )
                if overlap is not None:
                    cur_pos, full_pos, match_len = overlap
                    # Delete from the first mismatch AFTER the overlap
                    end_cur = cur_pos + match_len
                    end_full = full_pos + match_len
                    cursor = self._txt_trans.textCursor()
                    if end_cur < len(current):
                        cursor.setPosition(end_cur)
                        cursor.setPosition(
                            len(current), QTextCursor.KeepAnchor,
                        )
                        cursor.removeSelectedText()
                    if end_full < len(full):
                        cursor = self._txt_trans.textCursor()
                        cursor.movePosition(QTextCursor.End)
                        cursor.insertText(full[end_full:])
                    self._txt_trans.setTextCursor(cursor)
                else:
                    # No overlap at all — append as new paragraph
                    new_text = full[prefix_len:] if prefix_len else full
                    cursor = self._txt_trans.textCursor()
                    cursor.movePosition(QTextCursor.End)
                    if current:
                        cursor.insertText("\n" + new_text)
                    else:
                        cursor.insertText(new_text)
                    self._txt_trans.setTextCursor(cursor)

            self._trim_translation_display()
            self._prev_trans_tgt = self._txt_trans.toPlainText()
        else:
            self._update_trans_text_smoothly(full)

        self._debug.log(
            "DISPLAY STATE",
            f"Translation box ({len(self._txt_trans.toPlainText())} chars):\n"
            f"{self._txt_trans.toPlainText()}",
        )

        bar = self._txt_trans.verticalScrollBar()
        bar.setValue(bar.maximum())

        current_msg = self._status.currentMessage()
        if "OCR:" in current_msg:
            self._status.showMessage(f"{current_msg.split(' | ')[0]} | Trans: {ms:.0f}ms")

        if self._pending_trans_text:
            self._request_translation(self._pending_trans_text)

    def _update_trans_text_smoothly(self, new_text: str):
        """Diff-based text update to eliminate flickering."""
        if not new_text:
            self._txt_trans.setPlainText("")
            return
            
        current_text = self._txt_trans.toPlainText()
        if current_text == new_text:
            return

        # Find the length of the common prefix
        prefix_len = 0
        for c1, c2 in zip(current_text, new_text):
            if c1 == c2:
                prefix_len += 1
            else:
                break
                
        cursor = self._txt_trans.textCursor()
        
        # Remove diverging tail if any (from prefix_len to end of current_text)
        if prefix_len < len(current_text):
            cursor.setPosition(prefix_len, QTextCursor.MoveAnchor)
            cursor.setPosition(len(current_text), QTextCursor.KeepAnchor)
            cursor.removeSelectedText()
            
        # Insert new tail (what's left in new_text after prefix_len)
        if prefix_len < len(new_text):
            cursor.setPosition(prefix_len, QTextCursor.MoveAnchor)
            cursor.insertText(new_text[prefix_len:])
            
        # Unselect and move to end
        cursor.clearSelection()
        self._txt_trans.setTextCursor(cursor)

    @pyqtSlot(str)
    def _on_trans_error(self, msg):
        """Handle errors from translation worker."""
        # Kept until the next successful translation; the OCR status
        # message would otherwise overwrite it within one tick.
        self._trans_error = f"Translation Error: {msg}"
        self._debug.log("TRANSLATION ERROR", msg)
        self._status.showMessage(self._trans_error)

    @pyqtSlot(str)
    def _on_ocr_error(self, msg):
        self._processing_ocr = False
        self._status.showMessage(msg)

    # ══════════════════════════════════════════════════════════════════
    #  Helpers
    # ══════════════════════════════════════════════════════════════════

    @staticmethod
    def _find_tail_overlap(current, full, prefix_len, min_anchor=15):
        """Find where full[prefix_len:] overlaps with current[prefix_len:].

        Slides an anchor (first *min_anchor* chars of each candidate
        window in *full*) through *current* to locate a confirmed
        overlap.  Returns ``(cur_pos, full_pos, match_len)`` or
        ``None``.
        """
        tail = full[prefix_len:]
        if len(tail) < min_anchor:
            return None

        search_area = current[prefix_len:]
        if not search_area:
            return None

        # Try anchors starting at different offsets inside `tail`
        # so a correction near the beginning doesn't block detection.
        for anchor_off in range(0, min(len(tail) - min_anchor + 1, 300), 3):
            anchor = tail[anchor_off : anchor_off + min_anchor]
            rel = search_area.find(anchor)
            if rel < 0:
                continue

            cur_pos = prefix_len + rel
            full_pos = prefix_len + anchor_off

            # Extend the match forward as far as possible
            match_len = 0
            while (
                cur_pos + match_len < len(current)
                and full_pos + match_len < len(full)
                and current[cur_pos + match_len] == full[full_pos + match_len]
            ):
                match_len += 1

            if match_len >= min_anchor:
                return (cur_pos, full_pos, match_len)

        return None

    def _trim_translation_display(self):
        """Remove old content from the top to bound memory usage."""
        text = self._txt_trans.toPlainText()
        if len(text) <= _MAX_CONTENT_CHARS:
            return
        remove_len = len(text) - _MAX_CONTENT_CHARS
        cursor = self._txt_trans.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(remove_len, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

    def _toggle_debug(self):
        self._debug.setVisible(not self._debug.isVisible())

    def _copy_to_clipboard(self, text: str):
        """Copy text to system clipboard."""
        if text:
            QApplication.clipboard().setText(text)
            self._status.showMessage("Copied to clipboard!", 2000)

    @pyqtSlot()
    def open_region_selector(self):
        """Public slot to open region selector (invoked by the global hotkey)."""
        self._on_select_region()

    # ── cleanup ───────────────────────────────────────────────────────
    def closeEvent(self, event):
        self._stop()

        self._worker_ocr.cleanup()

        # Let an in-progress LLM stream end so the QThread isn't
        # destroyed while still running.
        self._worker_trans.request_stop()
        self._thread_trans.quit()
        self._thread_trans.wait(3000)
        self._debug.close()

        super().closeEvent(event)
