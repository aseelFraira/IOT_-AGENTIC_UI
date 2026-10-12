"""Minimal OpenAI Chat Completions client (standard library only), with the same
generate() signature as gemini_client so the pipeline can use either.

The key is read from OPENAI_API_KEY (environment, or the git-ignored .env) and is
never logged. The model comes from OPENAI_MODEL.

Errors are raised as gemini_client.GeminiError (the tool's common "assistant
error" type), so the server shows the same friendly messages for both providers.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

from gemini_client import GeminiError  # importing gemini_client also loads .env

ENDPOINT = "https://api.openai.com/v1/chat/completions"
RETRY_DELAYS = (4, 10, 20)


def _log(text: str) -> None:
    sys.stderr.write("[openai] " + text + "\n")


def _text(content) -> str:
    """Our messages may carry Gemini-style part lists; the generator only sends text."""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content)


def _request(body: dict, key: str, timeout: int) -> dict:
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        text = f"OpenAI API ({body['model']}) returned HTTP {e.code}: {detail}"
        if e.code in (400, 401, 403, 404):
            raise GeminiError(text, "The assistant rejected the request. The API key or model may be wrong.") from None
        if e.code == 429:
            if "insufficient_quota" in detail:
                raise GeminiError(text, "The OpenAI account has no credit left. Add credit and try again.") from None
            raise GeminiError(text, "The assistant is busy right now. Please wait a minute and try again.",
                              retryable=True) from None
        if e.code >= 500:
            raise GeminiError(text, "The assistant is overloaded right now. Please try again in a minute.",
                              retryable=True) from None
        raise GeminiError(text) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        reason = getattr(e, "reason", e)
        raise GeminiError(f"Could not reach the OpenAI API: {reason}",
                          "I couldn't reach the assistant. Check the internet connection and try again.",
                          retryable=True) from None


def generate(system_prompt: str, messages, model: str = None, timeout: int = 300,
             sleep=time.sleep, **_ignored) -> str:
    """messages: list of (role, content) with role 'user' or 'model'. Returns the reply text.
    Temperature is left at the model default: some OpenAI models reject other values."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise GeminiError("OPENAI_API_KEY is not set. Put it in .env or set it in the terminal.",
                          "The assistant isn't set up yet (the API key is missing).")
    model = model or os.environ.get("OPENAI_MODEL")
    if not model:
        raise GeminiError("OPENAI_MODEL is not set. Put it in .env.",
                          "The assistant isn't set up yet (no model chosen).")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + [
            {"role": "assistant" if role == "model" else "user", "content": _text(content)}
            for role, content in messages
        ],
    }

    last_error = None
    for attempt, delay in enumerate((0,) + RETRY_DELAYS):
        if delay:
            _log(f"{model} unavailable, retrying in {delay}s (retry {attempt}/{len(RETRY_DELAYS)})")
            sleep(delay)
        try:
            data = _request(body, key, timeout)
        except GeminiError as e:
            last_error = e
            if e.retryable:
                continue
            raise
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise GeminiError(f"OpenAI returned no content: {str(data)[:300]}") from None
    raise last_error
