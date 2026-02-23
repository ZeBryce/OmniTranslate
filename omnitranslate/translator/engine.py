"""
Real-time streaming translation engine.

Uses an OpenAI-compatible API to provide fast, context-aware
streaming translation with minimal output churn.

In *realtime* mode the previous translation is fed back to the LLM
with strict instructions to preserve it as a stable prefix.  The
caller then does a cheap character-level prefix-match diff so that
only genuinely new or changed text is redrawn in the UI — eliminating
the flicker that plagued earlier approaches.
"""

import os
from typing import Dict, List, Optional, Generator

from openai import OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_SUPPORTED_LANGUAGES: Optional[Dict[str, str]] = None

_MAX_CONTEXT_WORDS = 30


def get_supported_languages() -> Dict[str, str]:
    """Return ``{name: code}`` dict of supported languages."""
    global _SUPPORTED_LANGUAGES
    if _SUPPORTED_LANGUAGES is None:
        _SUPPORTED_LANGUAGES = {
            "English": "en",
            "Chinese (Simplified)": "zh-CN",
            "Chinese (Traditional)": "zh-TW",
            "Japanese": "ja",
            "Korean": "ko",
            "Spanish": "es",
            "French": "fr",
            "German": "de",
            "Russian": "ru",
            "Arabic": "ar",
            "Portuguese": "pt",
            "Italian": "it",
            "Vietnamese": "vi",
            "Thai": "th",
            "Indonesian": "id",
            "Hindi": "hi",
        }
    return _SUPPORTED_LANGUAGES


def get_language_list() -> List[str]:
    """Return a sorted list of language display names."""
    return sorted(get_supported_languages().keys())


class TranslationEngine:
    """Translates text via an OpenAI-compatible streaming API.

    In *realtime* mode the prompt feeds the previous translation back to
    the LLM and instructs it to preserve that text as a stable prefix,
    only appending newly translated content.  This lets the caller do a
    simple prefix-match diff to avoid flickery full-screen redraws.
    """

    def __init__(self, source: str = "auto", target: str = "en"):
        self.source = source
        self.target = target
        self._last_text = ""
        self._last_result = ""

        self.client = None
        api_key = os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if api_key:
            self.client = OpenAI(api_key=api_key, base_url=base_url)

        self.set_languages(source, target)

    def set_languages(self, source: str = "auto", target: str = "en"):
        self.source = source
        self.target = target
        self._last_text = ""
        self._last_result = ""

    @staticmethod
    def _tail_chars(text: str, n: int) -> str:
        """Return the last *n* characters of *text*."""
        if len(text) <= n:
            return text
        return text[-n:]

    @staticmethod
    def _tail_words(text: str, n: int) -> str:
        """Return the last *n* whitespace-separated tokens of *text*."""
        words = text.split()
        if len(words) <= n:
            return text
        return " ".join(words[-n:])

    def translate_stream(
        self,
        text: str,
        mode: str = "reading",
        prev_trans: str = "",
    ) -> Generator[str, None, None]:
        """Yield progressively longer translation strings.

        In *realtime* mode the prompt instructs the LLM to preserve
        *prev_trans* as a prefix so the caller can do a cheap
        prefix-match diff to minimise UI redraws.
        """
        if not text or not text.strip():
            yield ""
            return

        text = text.strip()

        if text == self._last_text and self._last_result:
            yield self._last_result
            return

        if not self.client:
            yield "Error: OPENAI_API_KEY not set. Create a .env file with OPENAI_API_KEY=your_key"
            return

        if self.source == self.target:
            yield text
            return

        src_lbl = self.source if self.source != "auto" else "Auto-detect"

        if mode == "realtime" and prev_trans:
            context = self._tail_words(prev_trans, _MAX_CONTEXT_WORDS)
            sys_instruction = (
                f"You are a real-time subtitle translation engine.\n"
                f"Source language: {src_lbl}. Target language: {self.target}.\n\n"
                f"You will receive the FULL on-screen OCR text (which grows over "
                f"time) and the last ~{_MAX_CONTEXT_WORDS} words of your own "
                f"previous translation output.\n\n"
                f"RULES (follow strictly):\n"
                f"1. Output ONLY the translation. No explanation, no commentary.\n"
                f"2. The previous translation snippet is shown below. You MUST "
                f"start your output by reproducing these words EXACTLY, then "
                f"APPEND the translation of any new content after them.\n"
                f"3. Only modify words in the previous snippet when NOT doing "
                f"so would make the sentence grammatically broken or semantically "
                f"wrong. Even then, change as FEW words as possible (preferably "
                f"only the last one or two words at the very end).\n"
                f"4. NEVER rephrase, restructure, or reword the previous "
                f"snippet when it is already correct and coherent.\n"
                f"5. If the OCR text is COMPLETELY unrelated to the previous "
                f"translation (e.g. a scene change), output a fresh translation "
                f"WITHOUT the previous text.\n\n"
                f"Previous translation (last ~{_MAX_CONTEXT_WORDS} words):\n"
                f"{context}"
            )
        else:
            sys_instruction = (
                f"You are a translation engine. "
                f"Translate from {src_lbl} to {self.target}. "
                f"Output ONLY the translation. Be concise, preserve formatting."
            )

        try:
            model_name = os.environ.get("OPENAI_MODEL", "deepseek-chat")

            response = self.client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": sys_instruction},
                    {"role": "user", "content": text},
                ],
                stream=True,
                temperature=0.1,
            )

            full = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    full += chunk.choices[0].delta.content
                    yield full

            self._last_text = text
            self._last_result = full

        except Exception as e:
            yield f"Translation Error: {str(e)}"
