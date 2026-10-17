import faulthandler
import multiprocessing
import os
import sys

from omnitranslate.paths import app_dir

_app_dir = app_dir()

# A windowed (no-console) exe has no stdout/stderr, and libraries that print
# would crash; send their output to a log file next to the exe instead.
if sys.stdout is None or sys.stderr is None:
    try:
        _log = open(os.path.join(_app_dir, "omnitranslate.log"), "w",
                    encoding="utf-8", buffering=1)
    except OSError:
        _log = open(os.devnull, "w")
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log

faulthandler.enable(file=sys.stderr)

os.environ["PADDLE_PDX_CACHE_HOME"] = os.path.join(_app_dir, ".paddleocr")
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

def _selftest() -> int:
    """Run OCR once on a generated image (``--selftest``), without the GUI.

    Useful to check a packaged exe: results go to stdout or omnitranslate.log.
    """
    import cv2
    import numpy as np
    from omnitranslate.ocr.engine import OCREngine

    img = np.full((120, 640, 3), 255, np.uint8)
    cv2.putText(img, "OmniTranslate self test", (20, 75),
                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    engine = OCREngine()
    # Call predict directly first so a failure shows its traceback
    list(engine._get_ocr(engine._current_lang).predict(img))
    text = engine.recognize(img)
    ok = "test" in text.lower()
    print(f"[selftest] OCR result: {text!r} -> {'OK' if ok else 'FAILED'}")

    # Free engines need network, so they're reported but don't fail the test
    from omnitranslate.translator.engine import TranslationEngine, EngineConfig
    for eid in ("google", "bing"):
        try:
            tr = TranslationEngine("en", "zh-CN", EngineConfig(eid))
            result = list(tr.translate_stream("Hello world"))[-1]
            print(f"[selftest] {eid}: OK {result!r}")
        except Exception as e:
            print(f"[selftest] {eid}: ERROR {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--selftest" in sys.argv:
        sys.exit(_selftest())

    from omnitranslate.app import main
    main()
