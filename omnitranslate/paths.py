"""
Application folder lookup.

Everything user-facing (``.env``, the ``.paddleocr`` model cache, the log
file) lives in one folder, resolved relative to the program itself:

- running from source: the project root (the folder containing ``run.py``)
- running as a PyInstaller build: the folder containing ``OmniTranslate.exe``
"""

import os
import sys


def app_dir() -> str:
    """Return the folder that holds ``.env`` and ``.paddleocr``."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
