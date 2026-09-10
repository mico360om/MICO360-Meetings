"""MICO360 Connect AI platform — OpenAI-compatible client.

Talks to the hard-coded MICO360 Connect endpoint (see config.MICO360_CONNECT_BASE_URL)
using the OpenAI-compatible surface documented in the API guide:

    GET  /v1/models            -> list usable models
    POST /v1/chat/completions  -> a completion   (Authorization: Bearer <key>)

Only MINUTES generation goes here; transcription stays local (Whisper). The key
is read from the environment via config.connect_api_key() — never from source.
Stdlib-only (urllib), so no extra dependency and it works in the frozen build.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from .. import __app_name__, __version__
from ..config import (
    MICO360_CONNECT_BASE_URL, MICO360_CONNECT_DEFAULT_MODEL, connect_api_key,
)
from . import prompts
from .generation import ProgressCb, run_minutes_pipeline

log = logging.getLogger("mico360.cloud")

# The guide recommends a client timeout of at least 120 s (cold model load), and
# 180 s when generating long text with a large model (§7).
_TIMEOUT = 180.0


@dataclass
class CloudStatus:
    """Mirror of OllamaStatus so the UI can treat both providers uniformly."""
    running: bool
    models: list[str] = field(default_factory=list)
    error: str = ""


def _headers(key: str) -> dict:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"{__app_name__}/{__version__}",
    }


def _base(base_url: str = "") -> str:
    return (base_url or MICO360_CONNECT_BASE_URL).rstrip("/")


def check_cloud_status(base_url: str = "", key: str = "") -> CloudStatus:
    """Reachability + usable model list via GET /v1/models.

    A 401 means the endpoint is reachable but the key is missing/invalid — surfaced
    as not-running with a clear message rather than a raw stack trace.
    """
    key = key or connect_api_key()
    if not key:
        return CloudStatus(False, error="No MICO360 Connect API key configured "
                                        "(set MICO360_CONNECT_API_KEY).")
    url = _base(base_url) + "/models"
    try:
        req = urllib.request.Request(url, headers=_headers(key))
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        models = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return CloudStatus(True, models=sorted(models))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            msg = "MICO360 Connect rejected the API key (401)."
        elif exc.code == 403:
            msg = "This key is not permitted to list models (403)."
        else:
            msg = f"MICO360 Connect returned HTTP {exc.code}."
        log.warning("cloud status: %s", msg)
        return CloudStatus(False, error=msg)
    except Exception as exc:
        log.warning("cloud not reachable at %s: %s", url, exc)
        return CloudStatus(False, error=f"Could not reach MICO360 Connect: {exc}")


class CloudGenerator:
    """OpenAI-compatible minutes generator; same interface as OllamaGenerator."""

    def __init__(self, base_url: str = "", key: str = "", model: str = ""):
        self.base_url = _base(base_url)
        self.key = key or connect_api_key()
        self.model = model or MICO360_CONNECT_DEFAULT_MODEL

    def _chat(self, prompt: str, cancel: Callable[[], bool] | None = None) -> str:
        if cancel and cancel():
            raise InterruptedError("Generation cancelled.")
        if not self.key:
            raise ValueError("No MICO360 Connect API key configured "
                             "(set MICO360_CONNECT_API_KEY).")
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0.2,
        }).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=body,
            headers=_headers(self.key), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_http_error_message(exc)) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not reach MICO360 Connect ({exc.reason}). Check your "
                "network connection.") from exc
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("MICO360 Connect returned an unexpected response.") from exc

    def generate_minutes(
        self,
        transcript: str,
        template: str,
        style: str = prompts.DEFAULT_STYLE,
        remove_fillers: bool = True,
        chunk_chars: int = 6000,
        progress: ProgressCb | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> str:
        return run_minutes_pipeline(
            self._chat, self.model, transcript, template, style,
            remove_fillers=remove_fillers, chunk_chars=chunk_chars,
            progress=progress, cancel=cancel)


def _http_error_message(exc: urllib.error.HTTPError) -> str:
    """Turn a MICO360 Connect error envelope into a readable message."""
    detail = ""
    try:
        payload = json.loads(exc.read().decode("utf-8", "replace"))
        err = payload.get("error")
        detail = (err.get("message") if isinstance(err, dict) else err) or ""
    except Exception:
        detail = ""
    hints = {
        401: "the API key was rejected",
        403: "this key is not permitted to use that model",
        413: "the prompt is too large — split the meeting",
        429: "rate limit or quota exceeded — try again shortly",
        503: "no node can serve this model right now — try again shortly",
        504: "the server timed out waiting for a node",
    }
    base = detail or hints.get(exc.code, f"HTTP {exc.code}")
    return f"MICO360 Connect error ({exc.code}): {base}"
