"""
Screen grabber.

Uses ``mss`` to capture a specific screen region as a NumPy array.
Designed to be called repeatedly from a QTimer loop.
"""

import numpy as np
import mss
import mss.tools


class ScreenGrabber:
    """Captures a screen region using mss."""

    def __init__(self):
        pass

    def grab(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """Capture the given screen region and return an RGB numpy array.

        Parameters
        ----------
        x, y : int
            Top-left corner of the region in screen coordinates.
        w, h : int
            Width and height of the region in pixels.

        Returns
        -------
        np.ndarray
            RGB image as a (H, W, 3) uint8 array.
        """
        # Use context manager for thread safety and to avoid stale handles
        with mss.mss() as sct:
            monitor = {"left": x, "top": y, "width": w, "height": h}
            shot = sct.grab(monitor)
            # mss returns BGRA; convert to RGB numpy array
            img = np.array(shot)
            # Drop alpha, convert BGR → RGB
            img = img[:, :, :3][:, :, ::-1].copy()
            return img

    def close(self):
        """No-op (handled by context manager)."""
        pass
