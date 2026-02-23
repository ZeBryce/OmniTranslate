"""
Region selector overlay.

Provides a full-screen transparent overlay that lets the user draw a rectangle
with the mouse to select a screen region. Emits ``region_selected`` with
``(x, y, width, height)`` in screen coordinates when the selection finishes.
Emits ``selection_done`` when the overlay closes for any reason.
"""

from PyQt5.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QCursor, QScreen
from PyQt5.QtWidgets import QWidget, QApplication


class RegionSelector(QWidget):
    """Full-screen transparent overlay for rubber-band region selection."""

    # Emitted with (x, y, w, h) in *screen* pixels when selection completes.
    region_selected = pyqtSignal(int, int, int, int)

    # Emitted when the overlay closes (whether selection succeeded or not).
    selection_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Frameless, stays on top, translucent background
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)  # smooth tracking even without button held
        self.setCursor(QCursor(Qt.CrossCursor))

        # Span the virtual desktop (all screens)
        geo = QRect()
        for screen in QApplication.screens():
            geo = geo.united(screen.geometry())
        self.setGeometry(geo)

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
                    screen = QApplication.primaryScreen()
                dpr = screen.devicePixelRatio()

                self.region_selected.emit(
                    int(top_left.x() * dpr),
                    int(top_left.y() * dpr),
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
