"""
OCR engine — PaddleOCR PP-OCRv4.

Uses PaddleOCR 3.4+ (backed by PaddleX) for state-of-the-art text
recognition.  Supports Chinese, English, Japanese and 80+ other languages.
Models are stored locally in the project directory (controlled by
``PADDLE_PDX_CACHE_HOME`` environment variable set in ``run.py``).
"""

import numpy as np

try:
    from paddleocr import PaddleOCR
    HAS_PADDLEOCR = True
except ImportError:
    PaddleOCR = None
    HAS_PADDLEOCR = False


_LANG_MAP = {
    "auto": "ch",
    "zh": "ch",
    "zh-CN": "ch",
    "zh-TW": "chinese_cht",
    "en": "en",
    "ja": "japan",
    "ko": "korean",
    "fr": "fr",
    "de": "german",
    "es": "es",
    "pt": "pt",
    "ru": "ru",
    "ar": "ar",
}

# PP-OCRv4 only ships models for these languages; everything else uses v3.
_V4_LANGUAGES = {"ch", "en"}


class OCREngine:
    """Performs optical character recognition using PaddleOCR.

    Uses PP-OCRv4 for Chinese/English (highest accuracy) and falls back
    to PP-OCRv3 for all other languages (Japanese, Korean, French, etc.).
    One PaddleOCR instance is cached per language for instant switching.
    """

    def __init__(self, lang: str = "ch"):
        if not HAS_PADDLEOCR:
            raise ImportError(
                "paddleocr is not installed. "
                "Run: pip install paddlepaddle paddleocr"
            )
        self._current_lang = lang
        self._ocr_instances: dict = {}
        self._get_ocr(lang)

    def _get_ocr(self, lang: str) -> "PaddleOCR":
        """Get or create a PaddleOCR instance for *lang*."""
        if lang not in self._ocr_instances:
            version = "PP-OCRv4" if lang in _V4_LANGUAGES else "PP-OCRv3"
            self._ocr_instances[lang] = PaddleOCR(
                lang=lang,
                ocr_version=version,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        return self._ocr_instances[lang]

    def set_language(self, source_lang: str):
        """Switch OCR language model based on a source-language code."""
        paddle_lang = _LANG_MAP.get(source_lang, "ch")
        if paddle_lang != self._current_lang:
            self._current_lang = paddle_lang
            self._get_ocr(paddle_lang)

    def recognize(self, image: np.ndarray) -> str:
        """Extract text from an image.

        Parameters
        ----------
        image : np.ndarray
            RGB image as a (H, W, 3) uint8 array.

        Returns
        -------
        str
            Extracted text with lines joined by newlines.
        """
        if image is None or image.size == 0:
            return ""

        try:
            ocr = self._get_ocr(self._current_lang)
            results = list(ocr.predict(image))
        except Exception:
            return ""

        if not results:
            return ""

        result = results[0]
        res = result.json.get("res", {}) if hasattr(result, "json") else {}

        texts = res.get("rec_texts", [])
        scores = res.get("rec_scores", [])
        boxes = res.get("rec_boxes", [])

        if not texts:
            return ""

        # Build (text, center_y, height) tuples for line grouping
        items = []
        for i, text in enumerate(texts):
            text = str(text).strip()
            if not text:
                continue
            if i < len(boxes):
                x1, y1, x2, y2 = boxes[i]
                center_y = (y1 + y2) / 2.0
                height = max(y2 - y1, 1)
            else:
                center_y = i * 40
                height = 20
            items.append((text, center_y, height))

        if not items:
            return ""

        # Group text fragments into lines by vertical proximity
        lines: list[str] = []
        current_parts: list[str] = [items[0][0]]
        last_y = items[0][1]
        last_h = items[0][2]

        for text, cy, h in items[1:]:
            threshold = max(h, last_h) * 0.6
            if abs(cy - last_y) < threshold:
                current_parts.append(text)
            else:
                lines.append(" ".join(current_parts))
                current_parts = [text]
                last_y = cy
                last_h = h

        if current_parts:
            lines.append(" ".join(current_parts))

        return "\n".join(lines)
