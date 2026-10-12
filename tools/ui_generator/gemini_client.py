"""Minimal Gemini REST client (standard library only).

The API key is read from GEMINI_API_KEY (environment, or the git-ignored .env in
the project root) and is never logged. Override the model with GEMINI_MODEL.

Temporary Google-side trouble is handled here so the user rarely sees it:
- overloaded / network errors (HTTP 5xx): retried a few times with short waits;
- quota used up (HTTP 429) or retries exhausted: the fallback model is tried
  (GEMINI_FALLBACK_MODEL, default gemini-flash-latest);
- bad request / bad key (HTTP 4xx): reported immediately.
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Reads KEY=VALUE lines from the project's .env. Variables already set in the
    real environment win, so a terminal override still works."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value:
            os.environ.setdefault(key, value)


load_dotenv()

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_FALLBACK_MODEL = "gemini-flash-latest"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_DELAYS = (4, 10, 20)  # seconds to wait before each retry on a temporary error


class GeminiError(Exception):
    """detail is for the developer log; user_message is safe to show to the end user.

    retryable: worth retrying the same model shortly (overload, network).
    switch_model: this model will not work now, so another model may (quota)."""

    def __init__(self, detail, user_message="I couldn't reach the assistant. Please try again.",
                 retryable=False, switch_model=False):
        super().__init__(detail)
        self.user_message = user_message
        self.retryable = retryable
        self.switch_model = switch_model


def _request(model: str, body: dict, key: str, timeout: int) -> dict:
    req = urllib.request.Request(
        ENDPOINT.format(model=model),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        text = f"Gemini API ({model}) returned HTTP {e.code}: {detail}"
        if e.code in (400, 401, 403):
            raise GeminiError(text, "The assistant rejected the request. The API key may be wrong.") from None
        if e.code == 429:
            if "free_tier" in detail or "PerDay" in detail:
                message = ("Today's free limit for the assistant is used up. It resets at midnight "
                           "Pacific time; or switch to a paid API key.")
            else:
                message = "The assistant is busy right now. Please wait a minute and try again."
            raise GeminiError(text, message, switch_model=True) from None
        if e.code in (500, 502, 503, 504):
            raise GeminiError(text, "The assistant is overloaded right now. Please try again in a minute.",
                              retryable=True) from None
        raise GeminiError(text) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        raise GeminiError(f"Could not reach the Gemini API ({model}): {reason}",
                          "I couldn't reach the assistant. Check the internet connection and try again.",
                          retryable=True) from None


def _reply_text(data: dict) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        reason = data.get("promptFeedback") or data.get("candidates")
        raise GeminiError(f"Gemini returned no content: {reason}") from None
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _log(text: str) -> None:
    sys.stderr.write("[gemini] " + text + "\n")


def image_part(jpeg_bytes: bytes) -> dict:
    """A message part carrying a JPEG photo (for the vision judge)."""
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpeg_bytes).decode("ascii")}}


def _parts(content):
    return [{"text": content}] if isinstance(content, str) else list(content)


def generate(system_prompt: str, messages, model: str = None, timeout: int = 120,
             sleep=time.sleep, temperature: float = 0.2, json_output: bool = False,
             key_var: str = "GEMINI_API_KEY", fallback_model: str = None) -> str:
    """messages: list of (role, content) with role 'user' or 'model'. content is the
    text, or a list of parts (text and image_part()). Returns the reply text.
    json_output asks Gemini for a JSON reply. key_var names the environment
    variable holding the API key; fallback_model ("" for none) overrides
    GEMINI_FALLBACK_MODEL."""
    key = os.environ.get(key_var)
    if not key:
        raise GeminiError(
            f"{key_var} is not set. Put it in .env or set it in the terminal.",
            "The assistant isn't set up yet (the API key is missing).",
        )
    primary = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
    if fallback_model is None:
        fallback_model = os.environ.get("GEMINI_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)
    models = [primary] + ([fallback_model] if fallback_model and fallback_model != primary else [])

    config = {"temperature": temperature}
    if json_output:
        config["responseMimeType"] = "application/json"
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": role, "parts": _parts(content)} for role, content in messages],
        "generationConfig": config,
    }

    last_error = None
    for name in models:
        for attempt, delay in enumerate((0,) + RETRY_DELAYS):
            if delay:
                _log(f"{name} unavailable, retrying in {delay}s (retry {attempt}/{len(RETRY_DELAYS)})")
                sleep(delay)
            try:
                return _reply_text(_request(name, body, key, timeout))
            except GeminiError as e:
                last_error = e
                if e.retryable:
                    continue
                if e.switch_model:
                    break
                raise
        _log(f"giving up on {name}" + (f", trying {models[-1]}" if name != models[-1] else ""))
    raise last_error
