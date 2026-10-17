"""
Real-time streaming translation engine.

Supports many backends, picked in the UI's Engine tab (see ``ENGINES``):

- **Free, no key** — Google Translate and Microsoft Bing (public web
  endpoints).
- **Translation APIs** — DeepL, Microsoft Azure, Baidu, Youdao (NetEase),
  Tencent and Caiyun, each with its own credentials.
- **AI models** — DeepSeek, OpenAI, Gemini, Qwen, Kimi, Zhipu GLM or any
  OpenAI-compatible chat API, with streaming output.

In *realtime* mode the LLM backends get the previous translation fed back
with strict instructions to preserve it as a stable prefix.  The caller
then does a cheap character-level prefix-match diff so that only genuinely
new or changed text is redrawn in the UI.  The other backends have no such
context: they always translate the full text.
"""

import hashlib
import hmac
import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Generator, Tuple

import requests
from openai import OpenAI

from omnitranslate.paths import app_dir

try:
    from dotenv import load_dotenv
    # .env lives in the project root (or next to OmniTranslate.exe)
    load_dotenv(os.path.join(app_dir(), ".env"))
except ImportError:
    pass

_SUPPORTED_LANGUAGES: Optional[Dict[str, str]] = None

_MAX_CONTEXT_WORDS = 30

_HTTP_TIMEOUT = 15
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


# ══════════════════════════════════════════════════════════════════════
#  Engine catalogue
# ══════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Field:
    """One credential / option an engine needs, shown as a text box."""

    name: str
    label: str
    secret: bool = False
    optional: bool = False
    placeholder: str = ""


@dataclass(frozen=True)
class EngineInfo:
    label: str
    group: str                      # "free" | "api" | "llm"
    fields: Tuple[Field, ...] = ()
    hint: str = ""
    base_url: str = ""              # LLM default endpoint
    model: str = ""                 # LLM default model

    @property
    def is_llm(self) -> bool:
        return self.group == "llm"


ENGINE_GROUPS = {
    "free": "Free (no key)",
    "api": "Translation APIs",
    "llm": "AI models",
}

_KEY = Field("api_key", "API Key", secret=True)


def _llm(label, base_url, model, hint):
    return EngineInfo(
        label, "llm",
        (_KEY, Field("model", "Model", optional=True, placeholder=model)),
        hint, base_url, model,
    )


# Order here is the order shown in the UI.
ENGINES: Dict[str, EngineInfo] = {
    "google": EngineInfo(
        "Google Translate", "free",
        hint="Free public endpoint. May be rate-limited on some networks."),
    "bing": EngineInfo(
        "Microsoft Bing", "free",
        hint="Free Bing web translator. Max ~1000 characters per request."),
    "deepl": EngineInfo(
        "DeepL", "api", (_KEY,),
        hint="Key from deepl.com/pro-api (free keys end with :fx)."),
    "azure": EngineInfo(
        "Microsoft Azure", "api",
        (_KEY, Field("region", "Region", optional=True,
                     placeholder="e.g. eastasia (empty for global)")),
        hint="Azure Translator resource key; free tier: 2M chars/month."),
    "baidu": EngineInfo(
        "Baidu Translate", "api",
        (Field("app_id", "APP ID"), Field("secret", "Secret Key", secret=True)),
        hint="From fanyi-api.baidu.com (standard edition is free)."),
    "youdao": EngineInfo(
        "Youdao (NetEase)", "api",
        (Field("app_key", "App Key"), Field("secret", "App Secret", secret=True)),
        hint="From ai.youdao.com (text translation application)."),
    "tencent": EngineInfo(
        "Tencent TMT", "api",
        (Field("secret_id", "SecretId"), Field("secret", "SecretKey", secret=True),
         Field("region", "Region", optional=True, placeholder="ap-guangzhou")),
        hint="From console.cloud.tencent.com/cam/capi (5M chars/month free)."),
    "caiyun": EngineInfo(
        "Caiyun", "api", (Field("api_key", "Token", secret=True),),
        hint="Token from fanyi.caiyunapp.com. Supports zh / en / ja best."),
    "deepseek": _llm("DeepSeek", "https://api.deepseek.com", "deepseek-chat",
                     "Key from platform.deepseek.com."),
    "openai": _llm("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini",
                   "Key from platform.openai.com."),
    "gemini": _llm("Google Gemini",
                   "https://generativelanguage.googleapis.com/v1beta/openai/",
                   "gemini-2.5-flash", "Key from aistudio.google.com."),
    "qwen": _llm("Qwen (Alibaba)",
                 "https://dashscope.aliyuncs.com/compatible-mode/v1",
                 "qwen-plus", "Key from dashscope.console.aliyun.com."),
    "kimi": _llm("Kimi (Moonshot)", "https://api.moonshot.cn/v1",
                 "moonshot-v1-8k", "Key from platform.moonshot.cn."),
    "zhipu": _llm("Zhipu GLM", "https://open.bigmodel.cn/api/paas/v4",
                  "glm-4-flash", "Key from open.bigmodel.cn (glm-4-flash is free)."),
    "custom": EngineInfo(
        "Custom (OpenAI-compatible)", "llm",
        (_KEY, Field("base_url", "Base URL", placeholder="https://your-endpoint/v1"),
         Field("model", "Model", placeholder="model name")),
        hint="Any OpenAI-compatible API (local Ollama, OpenRouter, …)."),
}


@dataclass(frozen=True)
class EngineConfig:
    """Which backend to use and its field values (hashable, comparable)."""

    engine: str = "google"
    values: Tuple[Tuple[str, str], ...] = ()

    @classmethod
    def make(cls, engine: str, **values: str) -> "EngineConfig":
        return cls(engine, tuple(sorted((k, v) for k, v in values.items() if v)))

    @property
    def info(self) -> EngineInfo:
        return ENGINES[self.engine]

    def get(self, name: str) -> str:
        return dict(self.values).get(name, "")


def config_from_env() -> EngineConfig:
    """Build a config from the legacy ``.env`` variables, if present."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return EngineConfig("google")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("OPENAI_MODEL", "")
    for eid, info in ENGINES.items():
        if info.is_llm and info.base_url and base_url.rstrip("/") == info.base_url.rstrip("/"):
            return EngineConfig.make(eid, api_key=api_key, model=model)
    if not base_url:
        return EngineConfig.make("deepseek", api_key=api_key, model=model)
    return EngineConfig.make("custom", api_key=api_key, base_url=base_url, model=model)


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


# Per-engine language codes where they differ from ours
_DEEPL_TARGET = {"zh-CN": "ZH-HANS", "zh-TW": "ZH-HANT", "en": "EN-US", "pt": "PT-PT"}
_DEEPL_SOURCE = {"zh-CN": "ZH", "zh-TW": "ZH"}
_MS_CODES = {"zh-CN": "zh-Hans", "zh-TW": "zh-Hant"}
_BAIDU_CODES = {"zh-CN": "zh", "zh-TW": "cht", "ja": "jp", "ko": "kor", "es": "spa",
                "fr": "fra", "ar": "ara", "vi": "vie"}
_YOUDAO_CODES = {"zh-CN": "zh-CHS", "zh-TW": "zh-CHT"}
_TENCENT_CODES = {"zh-CN": "zh"}
_CAIYUN_CODES = {"zh-CN": "zh", "zh-TW": "zh-Hant"}


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


# ══════════════════════════════════════════════════════════════════════
#  Engine
# ══════════════════════════════════════════════════════════════════════

class TranslationEngine:
    """Translates text with the backend selected by an ``EngineConfig``.

    In *realtime* mode the LLM prompt feeds the previous translation back
    and instructs the model to preserve that text as a stable prefix,
    only appending newly translated content.  This lets the caller do a
    simple prefix-match diff to avoid flickery full-screen redraws.
    """

    def __init__(self, source: str = "auto", target: str = "en",
                 config: Optional[EngineConfig] = None):
        self.source = source
        self.target = target
        self.config = config or EngineConfig()
        self.client = None
        self._http = requests.Session()
        self._http.headers["User-Agent"] = _USER_AGENT
        self._bing = None                   # cached Bing page tokens
        self._last_key = None
        self._last_result = ""
        self._build_client()

    def configure(self, config: EngineConfig):
        """Switch backend / credentials (no-op if unchanged)."""
        if config == self.config:
            return
        self.config = config
        self._last_key = None
        self._last_result = ""
        self._build_client()

    def _build_client(self):
        cfg = self.config
        self.client = None
        if cfg.info.is_llm and cfg.get("api_key"):
            base_url = cfg.get("base_url") or cfg.info.base_url
            if base_url:
                self.client = OpenAI(api_key=cfg.get("api_key"), base_url=base_url)

    @property
    def supports_context(self) -> bool:
        """Whether realtime mode can feed previous output back as context."""
        return self.config.info.is_llm

    def set_languages(self, source: str = "auto", target: str = "en"):
        if (source, target) == (self.source, self.target):
            return
        self.source = source
        self.target = target
        self._last_key = None
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

        In *realtime* mode the LLM prompt instructs the model to preserve
        *prev_trans* as a prefix so the caller can do a cheap prefix-match
        diff to minimise UI redraws.  Non-LLM engines ignore *prev_trans*
        and yield one complete result.

        Raises ``RuntimeError`` on missing credentials or API failure, so
        error messages never end up in the translation text.
        """
        if not text or not text.strip():
            yield ""
            return

        text = text.strip()

        key = (text, mode, prev_trans)
        if key == self._last_key and self._last_result:
            yield self._last_result
            return

        info = self.config.info
        for f in info.fields:
            if not f.optional and not self.config.get(f.name):
                raise RuntimeError(
                    f"{info.label} needs its {f.label}. Enter it on the Engine tab."
                )

        if self.source == self.target:
            yield text
            return

        if info.is_llm:
            yield from self._translate_llm(text, mode, prev_trans, key)
            return

        result = getattr(self, f"_translate_{self.config.engine}")(text)
        self._last_key = key
        self._last_result = result
        yield result

    # ── HTTP helper ───────────────────────────────────────────────────
    def _request(self, name: str, method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", _HTTP_TIMEOUT)
        try:
            return self._http.request(method, url, **kwargs)
        except requests.RequestException as e:
            raise RuntimeError(f"{name} network error: {e}") from e

    # ── Google (free web endpoint) ────────────────────────────────────
    def _translate_google(self, text: str) -> str:
        resp = self._request(
            "Google", "POST", "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": self.source, "tl": self.target, "dt": "t"},
            data={"q": text},
        )
        if resp.status_code == 429:
            raise RuntimeError(
                "Google refused the request (HTTP 429: rate-limited on this "
                "network). Try again later or pick another engine."
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Google error: HTTP {resp.status_code}")
        try:
            segments = resp.json()[0] or []
            return "".join(seg[0] for seg in segments if seg and seg[0])
        except (ValueError, IndexError, TypeError) as e:
            raise RuntimeError(f"Google returned an unexpected response: {e}") from e

    # ── Microsoft Bing (free web translator) ──────────────────────────
    def _bing_tokens(self, refresh=False):
        """Fetch (and cache for 30 min) the tokens the Bing page embeds."""
        if self._bing and not refresh and time.time() - self._bing["at"] < 1800:
            return self._bing
        page = self._request("Bing", "GET", "https://www.bing.com/translator")
        ig = re.search(r'IG:"([^"]+)"', page.text)
        iid = re.search(r'data-iid="([^"]+)"', page.text)
        abuse = re.search(
            r'params_AbusePreventionHelper\s*=\s*\[([^,]+),"([^"]+)",', page.text)
        if not (ig and iid and abuse):
            raise RuntimeError("Bing page format changed; pick another engine.")
        host = re.match(r"https://[^/]+", page.url).group(0)
        self._bing = {"at": time.time(), "host": host, "ig": ig.group(1),
                      "iid": iid.group(1), "key": abuse.group(1),
                      "token": abuse.group(2)}
        return self._bing

    def _translate_bing(self, text: str) -> str:
        if len(text) > 1000:
            text = text[-1000:]  # Bing's free limit; keep the newest text
        src = "auto-detect" if self.source == "auto" else _MS_CODES.get(self.source, self.source)
        tgt = _MS_CODES.get(self.target, self.target)
        for attempt in range(2):
            tok = self._bing_tokens(refresh=attempt > 0)
            resp = self._request(
                "Bing", "POST", f"{tok['host']}/ttranslatev3",
                params={"isVertical": "1", "IG": tok["ig"], "IID": tok["iid"] + ".1"},
                data={"fromLang": src, "to": tgt, "text": text,
                      "token": tok["token"], "key": tok["key"]},
            )
            try:
                data = resp.json()
            except ValueError:
                data = None
            if isinstance(data, list) and data and "translations" in data[0]:
                return data[0]["translations"][0]["text"]
            if isinstance(data, dict) and data.get("ShowCaptcha"):
                raise RuntimeError(
                    "Bing is rate-limiting this network (captcha). "
                    "Try again later or pick another engine."
                )
        raise RuntimeError(f"Bing error: HTTP {resp.status_code} {resp.text[:120]}")

    # ── DeepL API ─────────────────────────────────────────────────────
    def _translate_deepl(self, text: str) -> str:
        key = self.config.get("api_key")
        host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
        body = {
            "text": [text],
            "target_lang": _DEEPL_TARGET.get(self.target, self.target.upper()),
        }
        if self.source != "auto":
            body["source_lang"] = _DEEPL_SOURCE.get(self.source, self.source.upper())
        resp = self._request(
            "DeepL", "POST", f"https://{host}/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {key}"}, json=body,
        )
        if resp.status_code == 403:
            raise RuntimeError("DeepL rejected the API key (HTTP 403).")
        if resp.status_code == 456:
            raise RuntimeError("DeepL quota exceeded (HTTP 456).")
        if resp.status_code != 200:
            raise RuntimeError(f"DeepL error: HTTP {resp.status_code} {resp.text[:200]}")
        return "".join(t["text"] for t in resp.json()["translations"])

    # ── Microsoft Azure Translator ────────────────────────────────────
    def _translate_azure(self, text: str) -> str:
        params = {"api-version": "3.0", "to": _MS_CODES.get(self.target, self.target)}
        if self.source != "auto":
            params["from"] = _MS_CODES.get(self.source, self.source)
        headers = {"Ocp-Apim-Subscription-Key": self.config.get("api_key")}
        if self.config.get("region"):
            headers["Ocp-Apim-Subscription-Region"] = self.config.get("region")
        resp = self._request(
            "Azure", "POST", "https://api.cognitive.microsofttranslator.com/translate",
            params=params, headers=headers, json=[{"Text": text}],
        )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            msg = data.get("error", {}).get("message", resp.text[:200]) if isinstance(data, dict) else resp.text[:200]
            raise RuntimeError(f"Azure error (HTTP {resp.status_code}): {msg}")
        return data[0]["translations"][0]["text"]

    # ── Baidu Translate API ───────────────────────────────────────────
    def _translate_baidu(self, text: str) -> str:
        app_id, secret = self.config.get("app_id"), self.config.get("secret")
        salt = str(random.randint(32768, 65536))
        sign = hashlib.md5((app_id + text + salt + secret).encode("utf-8")).hexdigest()
        resp = self._request(
            "Baidu", "POST", "https://fanyi-api.baidu.com/api/trans/vip/translate",
            data={"q": text, "from": _BAIDU_CODES.get(self.source, self.source),
                  "to": _BAIDU_CODES.get(self.target, self.target),
                  "appid": app_id, "salt": salt, "sign": sign},
        )
        data = resp.json()
        code = str(data.get("error_code", "0"))
        if code not in ("0", "52000"):
            raise RuntimeError(f"Baidu error {code}: {data.get('error_msg', '')}")
        return "\n".join(item["dst"] for item in data.get("trans_result", []))

    # ── Youdao (NetEase) API ──────────────────────────────────────────
    def _translate_youdao(self, text: str) -> str:
        app_key, secret = self.config.get("app_key"), self.config.get("secret")
        salt, curtime = str(uuid.uuid4()), str(int(time.time()))
        short = text if len(text) <= 20 else text[:10] + str(len(text)) + text[-10:]
        sign = _sha256(app_key + short + salt + curtime + secret)
        resp = self._request(
            "Youdao", "POST", "https://openapi.youdao.com/api",
            data={"q": text, "from": _YOUDAO_CODES.get(self.source, self.source),
                  "to": _YOUDAO_CODES.get(self.target, self.target),
                  "appKey": app_key, "salt": salt, "sign": sign,
                  "signType": "v3", "curtime": curtime},
        )
        data = resp.json()
        if data.get("errorCode") != "0":
            raise RuntimeError(f"Youdao error code {data.get('errorCode')}")
        return "\n".join(data.get("translation", []))

    # ── Tencent Machine Translation (TC3-HMAC-SHA256) ─────────────────
    def _translate_tencent(self, text: str) -> str:
        secret_id, secret = self.config.get("secret_id"), self.config.get("secret")
        region = self.config.get("region") or "ap-guangzhou"
        host, action, version = "tmt.tencentcloudapi.com", "TextTranslate", "2018-03-21"
        payload = json.dumps({
            "SourceText": text,
            "Source": _TENCENT_CODES.get(self.source, self.source),
            "Target": _TENCENT_CODES.get(self.target, self.target),
            "ProjectId": 0,
        })
        timestamp = int(time.time())
        date = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
        ctype = "application/json; charset=utf-8"
        canonical = (f"POST\n/\n\ncontent-type:{ctype}\nhost:{host}\n"
                     f"x-tc-action:{action.lower()}\n\n"
                     f"content-type;host;x-tc-action\n{_sha256(payload)}")
        scope = f"{date}/tmt/tc3_request"
        to_sign = f"TC3-HMAC-SHA256\n{timestamp}\n{scope}\n{_sha256(canonical)}"
        signing_key = _hmac(_hmac(_hmac(("TC3" + secret).encode(), date), "tmt"), "tc3_request")
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
        resp = self._request(
            "Tencent", "POST", f"https://{host}",
            data=payload.encode("utf-8"),
            headers={
                "Authorization": (f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
                                  f"SignedHeaders=content-type;host;x-tc-action, "
                                  f"Signature={signature}"),
                "Content-Type": ctype, "Host": host, "X-TC-Action": action,
                "X-TC-Timestamp": str(timestamp), "X-TC-Version": version,
                "X-TC-Region": region,
            },
        )
        data = resp.json().get("Response", {})
        if "Error" in data:
            raise RuntimeError(f"Tencent error {data['Error'].get('Code')}: "
                               f"{data['Error'].get('Message')}")
        return data.get("TargetText", "")

    # ── Caiyun (彩云小译) API ─────────────────────────────────────────
    def _translate_caiyun(self, text: str) -> str:
        src = _CAIYUN_CODES.get(self.source, self.source)
        tgt = _CAIYUN_CODES.get(self.target, self.target)
        resp = self._request(
            "Caiyun", "POST", "https://api.interpreter.caiyunai.com/v1/translator",
            headers={"x-authorization": f"token {self.config.get('api_key')}"},
            json={"source": [text], "trans_type": f"{src}2{tgt}",
                  "request_id": "omnitranslate", "detect": True},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Caiyun error: HTTP {resp.status_code} {resp.text[:200]}")
        return "\n".join(resp.json().get("target", []))

    # ── OpenAI-compatible LLMs (streaming) ────────────────────────────
    def _translate_llm(self, text, mode, prev_trans, key):
        if not self.client:
            raise RuntimeError(
                f"{self.config.info.label} needs a Base URL. Enter it on the Engine tab."
            )
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

        model_name = self.config.get("model") or self.config.info.model
        if not model_name:
            raise RuntimeError(
                f"{self.config.info.label} needs a model name. "
                f"Enter it on the Engine tab."
            )

        try:
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

            self._last_key = key
            self._last_result = full

        except Exception as e:
            raise RuntimeError(f"Translation API error: {e}") from e
