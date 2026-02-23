"""
OmniTranslate application entry point.

Creates the QApplication, sets up the main window, and registers
a global hotkey (Ctrl+Shift+S) for quick region selection.
"""

import os
import sys
import threading

# Enable High DPI scaling BEFORE QApplication is created
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QMetaObject, Qt, Q_ARG
from PyQt5.QtGui import QFont

# Must be set before QApplication instantiation
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

from omnitranslate.ui.main_window import MainWindow


def _register_hotkey(window: MainWindow):
    """Register a global Ctrl+Shift+S hotkey in a background thread.

    Uses ``pynput`` to listen for the key combination. When triggered,
    it schedules ``window.open_region_selector()`` on the Qt main thread.
    """
    try:
        from pynput import keyboard

        COMBO = {keyboard.Key.ctrl_l, keyboard.Key.shift, keyboard.KeyCode.from_char('s')}
        current_keys = set()

        def on_press(key):
            current_keys.add(key)
            if COMBO.issubset(current_keys):
                # Schedule on the Qt event loop
                QMetaObject.invokeMethod(
                    window, "open_region_selector", Qt.QueuedConnection
                )

        def on_release(key):
            current_keys.discard(key)

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
    except ImportError:
        print("[OmniTranslate] pynput not installed — global hotkey disabled.")
    except Exception as e:
        print(f"[OmniTranslate] Hotkey registration failed: {e}")


def main():
    """Launch OmniTranslate."""
    app = QApplication(sys.argv)
    app.setApplicationName("OmniTranslate")
    app.setOrganizationName("OmniTranslate")

    window = MainWindow()
    window.show()

    # Register global hotkey in background thread
    _register_hotkey(window)

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
