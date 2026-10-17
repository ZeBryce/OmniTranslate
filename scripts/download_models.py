"""
Pre-download PaddleOCR models into the project-local ``.paddleocr`` cache.

The app downloads models on first use anyway; run this to fetch them ahead
of time (e.g. before going offline).

Usage (from the project folder):
    venv\\Scripts\\python.exe scripts\\download_models.py            # zh + en
    venv\\Scripts\\python.exe scripts\\download_models.py ja ko fr   # extra languages
"""

import os
import sys

_project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Same cache location as run.py — must be set BEFORE importing paddleocr
os.environ["PADDLE_PDX_CACHE_HOME"] = os.path.join(_project_dir, ".paddleocr")
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
sys.path.insert(0, _project_dir)

from omnitranslate.ocr.engine import OCREngine, _LANG_MAP


def download_models(langs):
    engine = OCREngine()  # loads the default Chinese/English model
    for code in langs:
        if code not in _LANG_MAP:
            print(f"Skipping unknown language code: {code}")
            continue
        print(f"Loading OCR model for {code} ({_LANG_MAP[code]})...")
        try:
            engine.set_language(code)
            print("  OK")
        except Exception as e:
            print(f"  Failed: {e}")


if __name__ == "__main__":
    download_models(sys.argv[1:] or ["zh", "en"])
