"""
OmniTranslate application entry point.

Creates the QApplication, sets up the main window, and registers
a global hotkey (Ctrl+Shift+S) for quick region selection.
"""

import os
import sys

# Enable High DPI scaling BEFORE QApplication is created
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QMetaObject, Qt

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

        CTRL = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
        SHIFT = {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r}
        VK_S = 0x53
        held = set()
        fired = False

        def is_s(key):
            # With Ctrl held, Windows reports the char as '\x13', not 's',
            # so match on the virtual-key code instead.
            return getattr(key, "vk", None) == VK_S

        def on_press(key):
            nonlocal fired
            if key in CTRL:
                held.add("ctrl")
            elif key in SHIFT:
                held.add("shift")
            elif is_s(key) and held >= {"ctrl", "shift"} and not fired:
                fired = True  # ignore key auto-repeat while held
                # Schedule on the Qt event loop
                QMetaObject.invokeMethod(
                    window, "open_region_selector", Qt.QueuedConnection
                )

        def on_release(key):
            nonlocal fired
            if key in CTRL:
                held.discard("ctrl")
            elif key in SHIFT:
                held.discard("shift")
            elif is_s(key):
                fired = False

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
