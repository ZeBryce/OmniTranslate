"""
Region selector overlay.

Provides a transparent overlay on the active screen that lets the user draw a
rectangle with the mouse to select a screen region. Emits ``region_selected``
with ``(x, y, width, height)`` in screen coordinates when the selection finishes.
Emits ``selection_done`` when the overlay closes for any reason.
"""

from typing import Optional

from PyQt5.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QCursor, QScreen
from PyQt5.QtWidgets import QWidget, QApplication


class RegionSelector(QWidget):
    """Transparent overlay for selecting a region on one physical screen."""

    # Emitted with (x, y, w, h) in *screen* pixels when selection completes.
    region_selected = pyqtSignal(int, int, int, int)

    # Emitted when the overlay closes (whether selection succeeded or not).
    selection_done = pyqtSignal()

    def __init__(self, screen: Optional[QScreen] = None, parent=None):
        super().__init__(parent)
        self._screen = screen or QApplication.screenAt(QCursor.pos())
        if self._screen is None:
            self._screen = QApplication.primaryScreen()

        # Frameless, stays on top, translucent background
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)  # smooth tracking even without button held
        self.setCursor(QCursor(Qt.CrossCursor))

        # Keep the overlay on a single screen. On Windows, one translucent
        # top-level window spanning the virtual desktop can receive input only
        # on a small part of another monitor when DPI/scaling differs.
        if self._screen is not None:
            self.winId()
            handle = self.windowHandle()
            if handle is not None:
                handle.setScreen(self._screen)
            self.setGeometry(self._screen.geometry())

        self._origin = QPoint()
        self._current = QPoint()
        self._selecting = False

    # ── paint ─────────────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Dim the entire screen
        painter.fillRect(self.rect(), QColor(0, 0, 0, 80))

        if self._selecting:
            rect = QRect(self._origin, self._current).normalized()
            # Clear the selected region (make it transparent)
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            # Draw a border around the selection
            pen = QPen(QColor(0, 174, 255), 2, Qt.SolidLine)
            painter.setPen(pen)
            painter.drawRect(rect)

            # Draw dimension label
            dim_text = f"{rect.width()} × {rect.height()}"
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                rect.right() - 90, rect.bottom() + 18, dim_text
            )

    # ── mouse events ──────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._origin = event.pos()
            self._current = event.pos()
            self._selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self._selecting:
            self._current = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._selecting:
            self._selecting = False
            rect = QRect(self._origin, self._current).normalized()
            if rect.width() > 5 and rect.height() > 5:
                # Map widget coords to global screen coords
                top_left = self.mapToGlobal(rect.topLeft())

                # Account for display scaling (e.g. 150% on 4K)
                # Qt reports logical pixels; mss needs physical pixels
                screen = QApplication.screenAt(top_left)
                if screen is None:
                    screen = self._screen or QApplication.primaryScreen()
                dpr = screen.devicePixelRatio()
                screen_geo = screen.geometry()
                physical_x = screen_geo.x() + (top_left.x() - screen_geo.x()) * dpr
                physical_y = screen_geo.y() + (top_left.y() - screen_geo.y()) * dpr

                self.region_selected.emit(
                    int(physical_x),
                    int(physical_y),
                    int(rect.width() * dpr),
                    int(rect.height() * dpr),
                )
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._selecting = False
            self.close()

    def closeEvent(self, event):
        """Emit selection_done when the overlay closes."""
        self.selection_done.emit()
        super().closeEvent(event)
