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

import email.utils
import hashlib
import http.client
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from .. import __app_name__, __version__
from ..config import (
    MICO360_CONNECT_BASE_URL, MICO360_CONNECT_DEFAULT_MODEL, MICO360_CONNECT_HTTPS_CANDIDATES,
    connect_api_key,
)
from . import prompts
from .generation import ProgressCb, run_in_thread, run_minutes_pipeline

log = logging.getLogger("mico360.cloud")

# The guide recommends a client timeout of at least 120 s (cold model load), and
# 180 s when generating long text with a large model (§7).
_TIMEOUT = 180.0

# Status checks (H14): bounded and cached so UI refreshes never freeze for long.
STATUS_TIMEOUT = 3.0           # per socket operation (TLS over the internet)
STATUS_DEADLINE = 4.0          # wall-clock bound incl. DNS
STATUS_OK_TTL = 30.0
STATUS_FAIL_TTL = 5.0

# Retries (M27): transient failures are retried with exponential backoff.
RETRY_STATUSES = {429, 502, 503, 504}
MAX_ATTEMPTS = 4               # 1 try + 3 retries
BACKOFF_BASE = 2.0             # 2 s, 4 s, 8 s …
MAX_SINGLE_WAIT = 30.0         # cap for one wait (incl. a long Retry-After)
MAX_TOTAL_WAIT = 60.0          # cap for all waits of one request


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


# --- transport security (C5) ----------------------------------------------
# The Connect server currently speaks plain HTTP only. As soon as it offers TLS
# on one of MICO360_CONNECT_HTTPS_CANDIDATES, the app switches to it (verified
# certificate) and never falls back to HTTP for the rest of the session.
_secure_base: str | None = None
_probe_started = False
_probe_lock = threading.Lock()
_probe_done = threading.Event()


def _probe_https(timeout: float = 4.0) -> None:
    global _secure_base
    try:
        for cand in MICO360_CONNECT_HTTPS_CANDIDATES:
            try:
                req = urllib.request.Request(cand.rstrip("/") + "/models",
                                             headers={"User-Agent": f"{__app_name__}/{__version__}"})
                urllib.request.urlopen(req, timeout=timeout).close()
                ok = True
            except urllib.error.HTTPError:
                ok = True                  # TLS worked; the server answered (e.g. 401 without key)
            except Exception:
                ok = False                 # no TLS / not reachable / certificate invalid
            if ok:
                _secure_base = cand.rstrip("/")
                log.info("MICO360 Connect: using encrypted endpoint %s", _secure_base)
                invalidate_status_cache()
                return
    finally:
        _probe_done.set()


def start_https_probe() -> None:
    """Look for an encrypted endpoint in the background (once per session)."""
    global _probe_started
    with _probe_lock:
        if _probe_started:
            return
        _probe_started = True
    threading.Thread(target=_probe_https, name="mico360-connect-tls", daemon=True).start()


def is_encrypted() -> bool:
    """True once Cloud traffic goes over a verified HTTPS endpoint."""
    return _base().startswith("https://")


def _base(base_url: str = "") -> str:
    return (base_url or _secure_base or MICO360_CONNECT_BASE_URL).rstrip("/")


_status_cache: dict[tuple, tuple[float, CloudStatus]] = {}
_status_lock = threading.Lock()


def invalidate_status_cache() -> None:
    with _status_lock:
        _status_cache.clear()


def _fetch_models(url: str, key: str) -> CloudStatus:
    req = urllib.request.Request(url, headers=_headers(key))
    with urllib.request.urlopen(req, timeout=STATUS_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    models = [m.get("id") for m in data.get("data", []) if m.get("id")]
    return CloudStatus(True, models=sorted(models))


def check_cloud_status(base_url: str = "", key: str = "", use_cache: bool = True) -> CloudStatus:
    """Reachability + usable model list via GET /v1/models.

    A 401 means the endpoint is reachable but the key is missing/invalid — surfaced
    as not-running with a clear message rather than a raw stack trace. Bounded
    (3 s per network step, 4 s wall clock) and cached: 30 s on success, 5 s on
    failure.
    """
    key = key or connect_api_key()
    if not key:
        return CloudStatus(False, error="No MICO360 Connect API key configured "
                                        "(set MICO360_CONNECT_API_KEY).")
    if not base_url:
        start_https_probe()                 # upgrade to HTTPS as soon as it's offered
    url = _base(base_url) + "/models"
    ck = (url, hashlib.sha256(key.encode("utf-8")).hexdigest())
    if use_cache:
        with _status_lock:
            hit = _status_cache.get(ck)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    try:
        st = run_in_thread(lambda: _fetch_models(url, key), deadline=STATUS_DEADLINE,
                           name="mico360-cloud-status")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            msg = "MICO360 Connect rejected the API key (401)."
        elif exc.code == 403:
            msg = "This key is not permitted to list models (403)."
        else:
            msg = f"MICO360 Connect returned HTTP {exc.code}."
        log.warning("cloud status: %s", msg)
        st = CloudStatus(False, error=msg)
    except Exception as exc:
        log.warning("cloud not reachable at %s: %s", url, exc)
        st = CloudStatus(False, error=f"Could not reach MICO360 Connect: {_network_reason(exc)}")
    ttl = STATUS_OK_TTL if st.running else STATUS_FAIL_TTL
    with _status_lock:
        _status_cache[ck] = (time.monotonic() + ttl, st)
    return st


# --- error classification / messages (M27) ------------------------------------
def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, (TimeoutError, socket.timeout)):
        return True
    return False


def _network_reason(exc: BaseException) -> str:
    """A friendly one-liner for a transport failure (never a bare 'timed out')."""
    if _is_timeout(exc):
        return "the server did not respond in time"
    inner = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(inner, http.client.RemoteDisconnected):
        return "the server closed the connection unexpectedly"
    if isinstance(inner, (ConnectionResetError, ConnectionAbortedError, http.client.IncompleteRead)):
        return "the connection was interrupted"
    if isinstance(inner, ConnectionRefusedError):
        return "the connection was refused"
    if isinstance(inner, socket.gaierror):
        return "the server name could not be resolved (are you offline?)"
    text = str(inner) if not isinstance(inner, str) else inner
    return text or type(inner).__name__


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRY_STATUSES
    if _is_timeout(exc):
        return True
    inner = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(inner, (http.client.RemoteDisconnected, http.client.IncompleteRead,
                              ConnectionResetError, ConnectionAbortedError))


def _retry_after(exc: BaseException) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or an HTTP date)."""
    if not isinstance(exc, urllib.error.HTTPError) or exc.headers is None:
        return None
    raw = (exc.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return max(0.0, dt.timestamp() - time.time())
    except Exception:
        return None


def _sleep(seconds: float, cancel: Callable[[], bool] | None) -> None:
    end = time.monotonic() + seconds
    while True:
        if cancel and cancel():
            raise InterruptedError("Generation cancelled.")
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.1, left))


class CloudGenerator:
    """OpenAI-compatible minutes generator; same interface as OllamaGenerator."""

    def __init__(self, base_url: str = "", key: str = "", model: str = ""):
        self._explicit_base = base_url
        self.key = key or connect_api_key()
        self.model = model or MICO360_CONNECT_DEFAULT_MODEL

    @property
    def base_url(self) -> str:
        # resolved per request: switches to HTTPS as soon as it's discovered (C5)
        return _base(self._explicit_base)

    def _request_once(self, body: bytes) -> dict:
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=body,
            headers=_headers(self.key), method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

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

        waited = 0.0
        attempt = 0
        while True:
            attempt += 1
            try:
                # on a worker thread so Cancel is honoured mid-request
                data = run_in_thread(lambda: self._request_once(body), cancel=cancel,
                                     name="mico360-cloud-chat")
                break
            except InterruptedError:
                raise
            except json.JSONDecodeError as exc:
                raise RuntimeError("MICO360 Connect returned an unexpected response.") from exc
            except Exception as exc:
                if not _is_retryable(exc) or attempt >= MAX_ATTEMPTS:
                    raise RuntimeError(self._error_message(exc, attempt)) from exc
                delay = _retry_after(exc)
                if delay is None:
                    delay = BACKOFF_BASE * (2 ** (attempt - 1))
                delay = min(delay, MAX_SINGLE_WAIT)
                if waited + delay > MAX_TOTAL_WAIT:
                    raise RuntimeError(self._error_message(exc, attempt)) from exc
                log.warning("cloud request failed (%s); retry %d in %.1f s",
                            _describe(exc), attempt, delay)
                _sleep(delay, cancel)
                waited += delay
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("MICO360 Connect returned an unexpected response.") from exc

    @staticmethod
    def _error_message(exc: BaseException, attempts: int) -> str:
        tried = f" (tried {attempts} times)" if attempts > 1 else ""
        if isinstance(exc, urllib.error.HTTPError):
            return _http_error_message(exc) + tried
        if _is_timeout(exc):
            return ("MICO360 Connect did not respond in time" + tried + ". The service "
                    "may be busy or your connection slow — try again shortly.")
        return (f"Could not reach MICO360 Connect ({_network_reason(exc)}){tried}. "
                "Check your network connection.")

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


def _describe(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return _network_reason(exc)


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
        502: "the service is temporarily unavailable — try again shortly",
        503: "no node can serve this model right now — try again shortly",
        504: "the server timed out waiting for a node",
    }
    base = detail or hints.get(exc.code, f"HTTP {exc.code}")
    return f"MICO360 Connect error ({exc.code}): {base}"
