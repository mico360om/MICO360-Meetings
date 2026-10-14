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
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .. import __app_name__, __version__
from ..config import (
    MICO360_CONNECT_BASE_URL, MICO360_CONNECT_DEFAULT_MODEL, MICO360_CONNECT_HTTPS_CANDIDATES,
    connect_api_key,
)
from . import prompts
from .generation import REDUCE_NOTES_BUDGET, ProgressCb, run_in_thread, run_minutes_pipeline

log = logging.getLogger("mico360.cloud")

# API guide §5/§7: the OpenAI surface waits 300 s for an answer, then answers
# 504 naming the job. Waiting a little longer than the server means that 504
# (and the job id to collect the answer with) arrives, instead of the client
# giving up first and sending the same work again.
_TIMEOUT = 330.0

# API guide §5/§11: a conversation is limited to 32,000 characters (413 above).
MAX_PROMPT_CHARS = 32_000
# Room kept for the pipeline's own instructions around the template + text.
_PROMPT_OVERHEAD = 1_500

# API guide §5/§8: after a 504 the job is collected with GET /api/v1/jobs/{id}
# ("poll every few seconds until the state is final") — never resubmitted.
JOB_POLL_INTERVAL = 3.0
JOB_POLL_DEADLINE = 600.0
_FINAL_JOB_STATES = {"Completed", "Failed", "TimedOut", "Cancelled"}
_JOB_ID_RE = re.compile(r"\bjob\s+(JOB-[A-Za-z0-9_\-:.]+?)[,)\s]")

# Status checks (H14): bounded and cached so UI refreshes never freeze for long.
STATUS_TIMEOUT = 3.0           # per socket operation (TLS over the internet)
STATUS_DEADLINE = 4.0          # wall-clock bound incl. DNS
STATUS_OK_TTL = 30.0
STATUS_FAIL_TTL = 5.0

# Retries (M27): transient failures are retried with exponential backoff.
# API guide §8: 429 and 503 are retried only after their Retry-After (a 503
# without one — the model is unavailable with no alternative — is final); a 504
# is collected, not retried (see JOB_POLL_*).
RETRY_STATUSES = {429, 502, 503}
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


def _headers(key: str, idempotency_key: str = "", correlation_id: str = "") -> dict:
    """OpenAI-surface headers (API guide §1/§5): Bearer key, JSON in and out.
    `Idempotency-Key` makes a retry return the first job's answer instead of
    running it twice (§11); `X-Mico-Correlation-Id` traces one request through
    the platform and is quoted in error messages (§8)."""
    h = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"{__app_name__}/{__version__}",
    }
    if idempotency_key:
        h["Idempotency-Key"] = idempotency_key
    if correlation_id:
        h["X-Mico-Correlation-Id"] = correlation_id
    return h


def _new_request_ids() -> tuple[str, str]:
    """(Idempotency-Key ≤128, correlation id ≤64) — letters, digits and '-' only."""
    u = uuid.uuid4().hex
    return f"mico360-meetings-{u}", f"MM-{u[:24]}"


def _origin(base: str) -> str:
    """Server root of an OpenAI base URL: '…:5310/v1' -> '…:5310' (for /api/v1)."""
    b = base.rstrip("/")
    for suffix in ("/openai/v1", "/v1"):
        if b.endswith(suffix):
            return b[: -len(suffix)]
    return b


# --- transport security (C5) ----------------------------------------------
# The Connect server currently speaks plain HTTP only. As soon as it offers TLS
# on one of MICO360_CONNECT_HTTPS_CANDIDATES, the app switches to it (verified
# certificate) and never falls back to HTTP for the rest of the session.
_secure_base: str | None = None
_probe_started = False
_probe_lock = threading.Lock()
_probe_done = threading.Event()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Cloud API calls never follow redirects: urllib would carry the
    Authorization header (the API key) to the new location — even from https to
    plain http. A redirect surfaces as an HTTPError instead."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _open(req, timeout: float):
    """Every Cloud request goes through here (no redirects followed)."""
    return _opener.open(req, timeout=timeout)


def _looks_like_connect(status: int, body: bytes) -> bool:
    """Is this reply really the MICO360 Connect (OpenAI-style) API? A 200 lists
    models under "data"; without a key it answers 401/403 with an "error" object.
    Anything else (an HTML site, a 404, a proxy page) is not the API."""
    try:
        data = json.loads((body or b"").decode("utf-8", "replace"))
    except Exception:
        return False
    if not isinstance(data, dict):
        return False
    if status == 200:
        return isinstance(data.get("data"), list)
    if status in (401, 403):
        return isinstance(data.get("error"), (dict, str))
    return False


def _probe_https(timeout: float = 4.0) -> None:
    global _secure_base
    try:
        for cand in MICO360_CONNECT_HTTPS_CANDIDATES:
            if not cand.lower().startswith("https://"):
                continue
            req = urllib.request.Request(cand.rstrip("/") + "/models",
                                         headers={"User-Agent": f"{__app_name__}/{__version__}",
                                                  "Accept": "application/json"})
            try:
                with _open(req, timeout) as resp:
                    status, body = resp.status, resp.read(65536)
            except urllib.error.HTTPError as exc:     # 401/403 = the API without a key
                status = exc.code
                try:
                    body = exc.read(65536)
                except Exception:
                    body = b""
            except Exception:
                continue                   # no TLS / not reachable / certificate invalid
            if _looks_like_connect(status, body):
                _secure_base = cand.rstrip("/")
                log.info("MICO360 Connect: using encrypted endpoint %s", _secure_base)
                invalidate_status_cache()
                return
            log.info("MICO360 Connect: %s answered but isn't the API (HTTP %s) — ignored",
                     cand, status)
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
    with _open(req, STATUS_TIMEOUT) as resp:
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


def _wait_words(seconds: float) -> str:
    s = int(round(seconds))
    if s < 90:
        return f"{s} seconds"
    if s < 90 * 60:
        return f"{round(s / 60)} minutes"
    return f"{round(s / 3600)} hours"


def _header(exc_or_resp, name: str) -> str:
    h = getattr(exc_or_resp, "headers", None)
    try:
        return (h.get(name) or "").strip() if h is not None else ""
    except Exception:
        return ""


class CloudGenerator:
    """OpenAI-compatible minutes generator; same interface as OllamaGenerator."""

    def __init__(self, base_url: str = "", key: str = "", model: str = ""):
        self._explicit_base = base_url
        self.key = key or connect_api_key()
        self.model = model or MICO360_CONNECT_DEFAULT_MODEL
        # API guide §6/§8: another model answered, or part of the request was
        # not honoured — shown to the user after generation (deduplicated).
        self.warnings: list[str] = []

    @property
    def base_url(self) -> str:
        # resolved per request: switches to HTTPS as soon as it's discovered (C5)
        return _base(self._explicit_base)

    def _request_once(self, body: bytes, idem: str, corr: str) -> tuple[dict, object]:
        req = urllib.request.Request(
            self.base_url + "/chat/completions", data=body,
            headers=_headers(self.key, idem, corr), method="POST")
        with _open(req, _TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), resp

    def _note_warnings(self, data: dict, resp) -> None:
        for w in (_header(resp, "X-Mico-Model-Warning"), (data or {}).get("warning") or "",
                  _header(resp, "X-Mico-Warning")):
            w = str(w).strip()
            if w and w not in self.warnings:
                self.warnings.append(w)
                log.warning("MICO360 Connect: %s", w)

    def _follow_tls_redirect(self, exc: urllib.error.HTTPError) -> bool:
        """API guide §9: once TLS is on, plain HTTP is redirected (307) to HTTPS.
        Switch to it only when it is HTTPS on the SAME host; never follow a
        redirect elsewhere (the Authorization header would go with it)."""
        global _secure_base
        loc = _header(exc, "Location")
        cur = urllib.parse.urlsplit(self.base_url)
        new = urllib.parse.urlsplit(loc)
        if (exc.code not in (307, 308) or new.scheme != "https"
                or (new.hostname or "").lower() != (cur.hostname or "").lower()
                or not new.path.endswith("/chat/completions")):
            return False
        base = f"https://{new.netloc}{new.path[: -len('/chat/completions')]}".rstrip("/")
        if self._explicit_base:
            self._explicit_base = base
        else:
            _secure_base = base
            invalidate_status_cache()
        log.info("MICO360 Connect: server redirected to its encrypted endpoint %s", base)
        return True

    def _collect_job(self, job_id: str, corr: str, cancel) -> tuple[str, str]:
        """Poll GET /api/v1/jobs/{id} (native surface: X-Mico-Api-Key only) until
        the job is final. Returns (state, output-or-error); ("Unknown", reason)
        when it cannot be read (server older than 2.6.4, network)."""
        url = _origin(self.base_url) + f"/api/v1/jobs/{urllib.parse.quote(job_id)}"
        headers = {"X-Mico-Api-Key": self.key, "Accept": "application/json",
                   "User-Agent": f"{__app_name__}/{__version__}", "X-Mico-Correlation-Id": corr}
        deadline = time.monotonic() + JOB_POLL_DEADLINE
        last = "the job could not be read"
        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request(url, headers=headers)
                job = run_in_thread(
                    lambda: json.loads(_open(req, 30.0).read().decode("utf-8", "replace")),
                    cancel=cancel, name="mico360-cloud-job")
                state = str(job.get("state") or "")
                if state == "Completed":
                    return state, job.get("output") or ""
                if state in _FINAL_JOB_STATES:
                    return state, job.get("error") or state
            except InterruptedError:
                raise
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403, 404):
                    return "Unknown", f"the job could not be read (HTTP {exc.code})"
                last = f"HTTP {exc.code}"
            except Exception as exc:                      # transient: keep polling
                last = _network_reason(exc)
            _sleep(JOB_POLL_INTERVAL, cancel)
        return "Unknown", f"no answer within {_wait_words(JOB_POLL_DEADLINE)} ({last})"

    def _chat(self, prompt: str, cancel: Callable[[], bool] | None = None) -> str:
        if cancel and cancel():
            raise InterruptedError("Generation cancelled.")
        if not self.key:
            raise ValueError("No MICO360 Connect API key configured "
                             "(set MICO360_CONNECT_API_KEY).")
        if len(prompt) > MAX_PROMPT_CHARS:
            raise RuntimeError(
                f"This request is {len(prompt):,} characters; MICO360 Cloud accepts at most "
                f"{MAX_PROMPT_CHARS:,}. Shorten the prompt template, or lower 'Chunk size' in "
                "Settings so long meetings are split into smaller parts.")
        # API guide §5: model, messages, stream. temperature & co. are ignored
        # by the platform, so they are not sent.
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode("utf-8")
        idem, corr = _new_request_ids()          # the same for every retry of this call

        waited = 0.0
        attempt = 0
        redirects = 0
        while True:
            attempt += 1
            try:
                # on a worker thread so Cancel is honoured mid-request
                data, resp = run_in_thread(lambda: self._request_once(body, idem, corr),
                                           cancel=cancel, name="mico360-cloud-chat")
                self._note_warnings(data, resp)
                break
            except InterruptedError:
                raise
            except json.JSONDecodeError as exc:
                raise RuntimeError("MICO360 Connect returned an unexpected response.") from exc
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 303, 307, 308):
                    redirects += 1
                    if redirects <= 2 and self._follow_tls_redirect(exc):
                        attempt -= 1
                        continue
                    raise RuntimeError(
                        "MICO360 Connect redirected the request to another address, which is not "
                        "followed (the API key would go with it).") from exc
                if exc.code == 504:
                    text = self._error_detail(exc)
                    m = _JOB_ID_RE.search(text + " ")
                    if m:
                        log.warning("cloud request timed out on the server; collecting %s", m.group(1))
                        state, out = self._collect_job(m.group(1), corr, cancel)
                        if state == "Completed":
                            data = {"choices": [{"message": {"content": out}}]}
                            break
                        if state == "Cancelled" and attempt < MAX_ATTEMPTS:
                            continue                 # never started: safe to send again
                        raise RuntimeError(
                            f"MICO360 Connect could not finish this part ({m.group(1)}: {out})."
                            + self._quote(exc, corr)) from exc
                    raise RuntimeError(self._error_message(exc, attempt, corr)) from exc
                delay = self._retry_delay(exc, attempt, waited, corr)
                log.warning("cloud request failed (HTTP %s); retry %d in %.1f s",
                            exc.code, attempt, delay)
                _sleep(delay, cancel)
                waited += delay
            except Exception as exc:
                if not _is_retryable(exc) or attempt >= MAX_ATTEMPTS:
                    raise RuntimeError(self._error_message(exc, attempt, corr)) from exc
                delay = min(BACKOFF_BASE * (2 ** (attempt - 1)), MAX_SINGLE_WAIT)
                if waited + delay > MAX_TOTAL_WAIT:
                    raise RuntimeError(self._error_message(exc, attempt, corr)) from exc
                log.warning("cloud request failed (%s); retry %d in %.1f s",
                            _describe(exc), attempt, delay)
                _sleep(delay, cancel)
                waited += delay
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("MICO360 Connect returned an unexpected response.") from exc

    def _retry_delay(self, exc: urllib.error.HTTPError, attempt: int, waited: float,
                     corr: str) -> float:
        """Seconds to wait before retrying an HTTP error, or raise when the API
        guide says not to retry (§8): anything but 429/502/503; a 503 without
        Retry-After (model unavailable, no alternative); a Retry-After longer
        than we are prepared to wait (a quota period, a fleet that is down) —
        never retried early."""
        if exc.code not in RETRY_STATUSES or attempt >= MAX_ATTEMPTS:
            raise RuntimeError(self._error_message(exc, attempt, corr)) from exc
        ra = _retry_after(exc)
        if exc.code == 503 and ra is None:
            raise RuntimeError(self._error_message(exc, attempt, corr)) from exc
        if ra is not None and (ra > MAX_SINGLE_WAIT or waited + ra > MAX_TOTAL_WAIT):
            raise RuntimeError(self._error_message(exc, attempt, corr)
                               + f" Try again in about {_wait_words(ra)}.") from exc
        delay = ra if ra is not None else min(BACKOFF_BASE * (2 ** (attempt - 1)), MAX_SINGLE_WAIT)
        if waited + delay > MAX_TOTAL_WAIT:
            raise RuntimeError(self._error_message(exc, attempt, corr)) from exc
        return delay

    @staticmethod
    def _error_detail(exc: urllib.error.HTTPError) -> str:
        """The server's message from either error envelope (§8), read once."""
        cached = getattr(exc, "_mico_detail", None)
        if cached is not None:
            return cached
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
            err = payload.get("error")
            detail = (err.get("message") if isinstance(err, dict) else err) or ""
        except Exception:
            detail = ""
        try:
            exc._mico_detail = detail
        except Exception:
            pass
        return detail

    @staticmethod
    def _quote(exc, corr: str) -> str:
        cid = _header(exc, "X-Mico-Correlation-Id") or corr
        return f" Reference for support: {cid}." if cid else ""

    @classmethod
    def _error_message(cls, exc: BaseException, attempts: int, corr: str = "") -> str:
        tried = f" (tried {attempts} times)" if attempts > 1 else ""
        if isinstance(exc, urllib.error.HTTPError):
            return _http_error_message(exc, cls._error_detail(exc)) + tried + cls._quote(exc, corr)
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
        self.warnings = []
        # API guide §5/§11: every request must fit 32,000 characters, the template
        # included — size the parts and the notes merged at the end to fit.
        room = MAX_PROMPT_CHARS - len(template or "") - _PROMPT_OVERHEAD
        if room < 1_500:
            raise RuntimeError(
                f"The prompt template is {len(template or ''):,} characters — too long for MICO360 "
                f"Cloud, which accepts {MAX_PROMPT_CHARS:,} characters per request including the "
                "meeting text. Shorten the template.")
        chunk = max(1_500, min(int(chunk_chars or 6000), room))
        budget = min(max(REDUCE_NOTES_BUDGET, 2 * chunk), room)
        return run_minutes_pipeline(
            self._chat, self.model, transcript, template, style,
            remove_fillers=remove_fillers, chunk_chars=chunk,
            progress=progress, cancel=cancel, reduce_budget=budget)


def _describe(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return _network_reason(exc)


def _http_error_message(exc: urllib.error.HTTPError, detail: str = "") -> str:
    """Turn a MICO360 Connect error (API guide §8) into a readable message.
    Some statuses have no body at all; then a hint stands in for the message."""
    hints = {
        400: "the request was not accepted",
        401: "the API key was rejected (missing, disabled, expired or deleted)",
        403: "this key is not permitted to use that capability or model",
        404: "the address is not the MICO360 AI API — check the base URL ends in /v1",
        405: "the request used the wrong method",
        413: "the request is too large — lower 'Chunk size' in Settings or shorten the template",
        415: "the request was not sent as JSON",
        429: "rate limit or quota exceeded — try again shortly",
        500: "the server had an internal error",
        502: "the AI node could not complete the request — try again shortly",
        503: "no AI node can serve this request right now",
        504: "the server timed out waiting for an AI node",
    }
    base = detail or hints.get(exc.code, f"HTTP {exc.code}")
    return f"MICO360 Connect error ({exc.code}): {base}"
