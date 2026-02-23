"""
Main application window.

Provides the primary UI for ScreenLens:
  - Region selection trigger
  - Start / Stop monitor toggle
  - Source / target language dropdowns
  - OCR interval slider
  - OCR result and translation text areas with copy buttons
  - Status bar with performance metrics

OCR and translation run in a background QThread to keep the UI responsive.
"""

import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSlot, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QIcon, QTextCursor
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QLabel, QTextEdit, QSlider,
    QGroupBox, QStatusBar, QApplication, QSplitter,
    QFrame, QSizePolicy, QRadioButton, QButtonGroup,
)

from omnitranslate.capture.selector import RegionSelector
from omnitranslate.capture.grabber import ScreenGrabber
from omnitranslate.ocr.engine import OCREngine
from omnitranslate.translator.engine import (
    TranslationEngine, get_supported_languages, _MAX_CONTEXT_WORDS,
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

        self.setStyleSheet(
            "background-color:#1e1e2e; "
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
        self._pool.shutdown(wait=False)
        self._grabber.close()


class _TranslationWorker(QObject):
    """Handles Translation (Streaming via Gemini)."""

    # (trans_text, trans_ms, req_id)
    chunk_ready = pyqtSignal(str, float, int)
    result_ready = pyqtSignal(str, float, int)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._engine = TranslationEngine(source="auto", target="en")

    @pyqtSlot(str, str, str, int, str, str)
    def translate(self, text, source, target, req_id, mode, prev_trans):
        try:
            self._engine.set_languages(source, target)
            t_start = time.perf_counter()
            
            final_text = ""
            for partial_text in self._engine.translate_stream(text, mode, prev_trans):
                final_text = partial_text
                t_ms = (time.perf_counter() - t_start) * 1000
                self.chunk_ready.emit(partial_text, t_ms, req_id)
                
            t_ms = (time.perf_counter() - t_start) * 1000
            self.result_ready.emit(final_text, t_ms, req_id)
        except Exception as e:
            # Don't fail hard on translation errors (network issues)
            self.result_ready.emit(text, 0, req_id)


class MainWindow(QMainWindow):
    """Primary ScreenLens window."""

    # Trigger Translation (text, src, tgt, req_id, mode, prev_trans)
    _do_trans = pyqtSignal(str, str, str, int, str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OmniTranslate — OCR & Translate")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setMinimumSize(640, 760)
        self.resize(720, 860)

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

        # ── Debug ─────────────────────────────────────────────────────
        self._debug = _DebugMonitor(self)

        # ── Workers ───────────────────────────────────────────────────

        # OCR: uses ThreadPoolExecutor (PaddlePaddle segfaults in QThread)
        self._worker_ocr = _OCRWorker()
        self._worker_ocr.result_ready.connect(self._on_ocr_result)
        self._worker_ocr.error.connect(self._on_ocr_error)

        # Translation: QThread is fine for argostranslate
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

        # ── Control row ───────────────────────────────────────────────
        ctrl_box = QGroupBox("Controls")
        ctrl_lay = QHBoxLayout(ctrl_box)

        self._btn_select = QPushButton("📐 Select Region")
        self._btn_select.setToolTip("Click to select a screen region (or Ctrl+Shift+S)")
        self._btn_select.clicked.connect(self._on_select_region)
        ctrl_lay.addWidget(self._btn_select)

        self._btn_toggle = QPushButton("▶  Start")
        self._btn_toggle.setToolTip("Start / stop real-time OCR & translation")
        self._btn_toggle.setEnabled(False)
        self._btn_toggle.clicked.connect(self._on_toggle)
        ctrl_lay.addWidget(self._btn_toggle)

        self._btn_debug = QPushButton("🔍 Debug")
        self._btn_debug.setToolTip("Toggle debug monitor window")
        self._btn_debug.clicked.connect(self._toggle_debug)
        ctrl_lay.addWidget(self._btn_debug)

        root.addWidget(ctrl_box)

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

        root.addWidget(lang_box)

        # ── Mode Selection ────────────────────────────────────────────
        mode_box = QGroupBox("Mode")
        mode_lay = QHBoxLayout(mode_box)

        self._mode_group = QButtonGroup(self)
        self._radio_reading = QRadioButton("Reading")
        self._radio_realtime = QRadioButton("Real-time")
        self._radio_realtime.setChecked(True)
        self._mode_group.addButton(self._radio_reading)
        self._mode_group.addButton(self._radio_realtime)
        mode_lay.addWidget(self._radio_reading)
        mode_lay.addWidget(self._radio_realtime)

        root.addWidget(mode_box)

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

        root.addWidget(interval_box)

        # ── Text areas (splitter) ─────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)

        # OCR result
        ocr_group = QGroupBox("OCR Result")
        ocr_lay = QVBoxLayout(ocr_group)
        self._txt_ocr = QTextEdit()
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

        root.addWidget(splitter, 1)

        # ── Region info label ─────────────────────────────────────────
        self._lbl_region = QLabel("No region selected")
        self._lbl_region.setAlignment(Qt.AlignCenter)
        self._lbl_region.setStyleSheet("color: #888;")
        root.addWidget(self._lbl_region)

        # ── Status bar ────────────────────────────────────────────────
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready — select a region to begin.")

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
                padding-top: 16px;
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
        """)

    # ══════════════════════════════════════════════════════════════════
    #  Slots
    # ══════════════════════════════════════════════════════════════════

    @pyqtSlot()
    def _on_select_region(self):
        """Open the overlay to let the user select a region."""
        # Stop any running pipeline first
        self._stop()

        # Temporarily hide main window so it's not captured
        self.hide()
        QApplication.processEvents()

        self._selector = RegionSelector()
        self._selector.region_selected.connect(self._on_region_selected)
        self._selector.selection_done.connect(self._on_selector_closed)
        self._selector.showFullScreen()

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
        self._txt_trans.clear()
        self._last_ocr_content = None
        self._pending_trans_text = None
        self._prev_trans_tgt = ""
        self._trans_head = ""
        self._last_trans_req_time = 0.0

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
        self._prev_trans_tgt = ""
        self._trans_head = ""
        self._last_trans_req_time = 0.0

        # Re-translate current text if any
        text = self._txt_ocr.toPlainText()
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
        self._processing_ocr = False
        self._status.showMessage(f"OCR: {ms:.0f}ms")

        if text == self._last_ocr_content:
            return
        self._last_ocr_content = text

        self._debug.log("OCR RESULT", f"{ms:.0f}ms\n{text}")

        self._txt_ocr.setPlainText(text.replace("\n", " "))
        bar = self._txt_ocr.verticalScrollBar()
        bar.setValue(bar.maximum())

        if not text.strip():
            self._txt_trans.setPlainText("")
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

        prev_tgt = self._prev_trans_tgt
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

        self._do_trans.emit(text, src, tgt, 0, mode, context)

    @pyqtSlot()
    def _flush_pending_translation(self):
        if self._pending_trans_text:
            text = self._pending_trans_text
            self._pending_trans_text = None
            self._request_translation(text)

    @pyqtSlot(str, float, int)
    def _on_trans_chunk(self, text, ms, req_id):
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
        # Check if we still have a status bar/widget (in case of closing)
        if hasattr(self, '_status'):
            self._status.showMessage(f"Translation Error: {msg}")

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

    def open_region_selector(self):
        """Public method to open region selector (called by hotkey)."""
        self._on_select_region()

    # ── cleanup ───────────────────────────────────────────────────────
    def closeEvent(self, event):
        self._stop()

        self._worker_ocr.cleanup()

        self._thread_trans.quit()
        self._thread_trans.wait(1000)

        super().closeEvent(event)
