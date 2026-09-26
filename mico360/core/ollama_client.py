"""Local Ollama integration: model discovery + minutes generation.

For long transcripts we use a map-reduce strategy: each chunk is condensed
into factual notes, then a final pass merges the notes into the chosen minutes
style. Short transcripts go through a single pass. All processing is local.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import prompts
from .generation import ProgressCb, run_in_thread, run_minutes_pipeline

log = logging.getLogger("mico360.ollama")


@dataclass
class OllamaStatus:
    running: bool
    models: list[str]
    error: str = ""


# --- timeouts (H14) ----------------------------------------------------------
# Generation: a short connect timeout (an unreachable host fails fast) but long
# reads, because a cold model load or a long answer can legitimately take
# minutes. Nothing waits forever any more (the ollama client default is None).
GEN_CONNECT_TIMEOUT = 3.0
GEN_READ_TIMEOUT = 600.0
# Status / model list: cheap and bounded so a UI refresh never freezes for long.
STATUS_TIMEOUT = 2.0
STATUS_DEADLINE = 3.0          # wall-clock bound incl. DNS (see run_in_thread)
STATUS_OK_TTL = 30.0           # cache a successful status this long
STATUS_FAIL_TTL = 5.0          # ...and a failure only briefly

# --- context window sizing (H15) ----------------------------------------------
CHARS_PER_TOKEN = 3.5
RESPONSE_TOKENS = 2048         # room reserved for the model's answer
MIN_NUM_CTX = 4096
MAX_NUM_CTX = 32768


def _timeout(connect: float, read: float):
    try:
        import httpx
        return httpx.Timeout(read, connect=connect)
    except Exception:              # httpx always ships with ollama; be defensive
        return max(connect, read)


def _client(host: str, timeout=None):
    """An ollama Client with real timeouts (the library default is none)."""
    from ollama import Client
    if timeout is None:
        timeout = _timeout(GEN_CONNECT_TIMEOUT, GEN_READ_TIMEOUT)
    return Client(host=host, timeout=timeout)


def num_ctx_for(prompt: str, response_tokens: int = RESPONSE_TOKENS) -> int:
    """Context window (tokens) large enough for ``prompt`` plus the answer.

    Estimated at ~3.5 characters per token, rounded UP to a power of two (so
    successive calls reuse the same loaded model instead of forcing a reload
    for every small size change) and clamped to [4096, 32768].
    """
    need = math.ceil(len(prompt or "") / CHARS_PER_TOKEN) + response_tokens
    ctx = MIN_NUM_CTX
    while ctx < need and ctx < MAX_NUM_CTX:
        ctx *= 2
    if need > MAX_NUM_CTX:
        log.warning("prompt (~%d tokens) exceeds the largest context used (%d)",
                    need, MAX_NUM_CTX)
    return min(max(ctx, MIN_NUM_CTX), MAX_NUM_CTX)


# recommended models offered in Settings → Install Required Model
RECOMMENDED_MODELS = [
    ("Llama 3.1 8B  (~4.7 GB, recommended)", "llama3.1"),
    ("Qwen 2.5 3B  (~1.9 GB, light)", "qwen2.5:3b"),
    ("Gemma 2 2B  (~1.6 GB, fastest)", "gemma2:2b"),
    ("Mistral 7B  (~4.1 GB)", "mistral"),
    ("Llama 3.2 3B  (~2.0 GB)", "llama3.2"),
]


def pull_model(host: str, model: str, progress=None, cancel=None) -> None:
    """Download/install an Ollama model, reporting progress(frac, status_text).

    Raises on failure so the caller can surface a clear message.
    """
    client = _client(host)
    try:
        for part in client.pull(model, stream=True):
            if cancel and cancel():
                raise InterruptedError("Model download cancelled.")
            status = part.get("status", "")
            total = part.get("total") or 0
            completed = part.get("completed") or 0
            frac = (completed / total) if total else None
            if progress:
                progress(frac, status, completed, total)
    finally:
        invalidate_status_cache()          # the model list has (probably) changed


# Model families/names that cannot write text minutes, so we hide them from the
# picker: vision/multimodal (e.g. llama3.2-vision → 'mllama'/'clip') and
# embedding models. Offering these leads to Ollama load errors like
# "unknown model architecture: 'mllama'".
_VISION_FAMILIES = {"clip", "mllama", "llava", "qwen2vl", "qwen2.5vl", "mllama4"}
_EMBED_FAMILIES = {"bert", "nomic-bert", "gte", "stella", "jina-bert"}
_VISION_NAME_HINTS = ("vision", "llava", "-vl", "minicpm-v", "moondream", "bakllava")
_EMBED_NAME_HINTS = ("embed", "nomic-embed", "mxbai", "bge-", "all-minilm", "snowflake-arctic-embed")


def usable_for_text(name: str, details: dict | None) -> bool:
    """True if an Ollama model can generate text (not vision-only / embedding)."""
    try:
        fams = set()
        d = details or {}
        fam = d.get("family") if hasattr(d, "get") else None
        if fam:
            fams.add(str(fam).lower())
        for f in (d.get("families") if hasattr(d, "get") else None) or []:
            if f:
                fams.add(str(f).lower())
        low = (name or "").lower()
        if fams & _VISION_FAMILIES or any(h in low for h in _VISION_NAME_HINTS):
            return False
        if fams & _EMBED_FAMILIES or any(h in low for h in _EMBED_NAME_HINTS):
            return False
        return True
    except Exception:                       # never hide a model on a shape surprise
        return True


_status_cache: dict[str, tuple[float, OllamaStatus]] = {}
_status_lock = threading.Lock()


def invalidate_status_cache() -> None:
    """Forget cached status results (after installing a model, changing host…)."""
    with _status_lock:
        _status_cache.clear()


def _list_models(host: str) -> OllamaStatus:
    resp = _client(host, timeout=_timeout(STATUS_TIMEOUT, STATUS_TIMEOUT)).list()
    models = []
    for m in resp.get("models", []):
        name = m.get("model") or m.get("name")
        if not name:
            continue
        if usable_for_text(name, m.get("details")):
            models.append(name)
    return OllamaStatus(running=True, models=sorted(models))


def check_status(host: str = "http://127.0.0.1:11434", use_cache: bool = True) -> OllamaStatus:
    """Return whether the Ollama server is reachable and which usable text
    models exist. Vision-only and embedding models are filtered out because
    they cannot produce minutes.

    Bounded (2 s per network step, 3 s wall clock) and cached — a success for
    30 s, a failure for 5 s — so pages that ask repeatedly stay responsive.
    """
    host = host or "http://127.0.0.1:11434"
    if use_cache:
        with _status_lock:
            hit = _status_cache.get(host)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    try:
        st = run_in_thread(lambda: _list_models(host), deadline=STATUS_DEADLINE,
                           name="mico360-ollama-status")
    except Exception as exc:
        log.warning("ollama not reachable at %s: %s", host, exc)
        msg = (f"Ollama did not respond at {host} in time" if _is_timeout(exc)
               else (str(exc) or type(exc).__name__))
        st = OllamaStatus(running=False, models=[], error=msg)
    ttl = STATUS_OK_TTL if st.running else STATUS_FAIL_TTL
    with _status_lock:
        _status_cache[host] = (time.monotonic() + ttl, st)
    return st


def _is_timeout(exc: BaseException) -> bool:
    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return True
    except Exception:
        pass
    return isinstance(exc, TimeoutError)


class OllamaGenerator:
    def __init__(self, host: str = "http://127.0.0.1:11434", model: str = ""):
        self.host = host
        self.model = model

    def _chat(self, prompt: str, cancel: Callable[[], bool] | None = None) -> str:
        if cancel and cancel():
            raise InterruptedError("Generation cancelled.")
        client = _client(self.host)
        # H15: without num_ctx Ollama uses its small default window and silently
        # drops the START of a long prompt (the instructions and early notes).
        options = {"temperature": 0.2, "num_ctx": num_ctx_for(prompt)}

        def _stream() -> str:
            out: list[str] = []
            stream = client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                options=options,
            )
            try:
                for part in stream:
                    if cancel and cancel():
                        raise InterruptedError("Generation cancelled.")
                    out.append(part.get("message", {}).get("content", ""))
            finally:
                close = getattr(stream, "close", None)
                if close:
                    try:
                        close()             # stops reading -> server stops generating
                    except Exception:
                        pass
            return "".join(out).strip()

        # The stream runs on a worker thread so Cancel works at once, even while
        # Ollama is still loading the model and has sent nothing yet.
        try:
            return run_in_thread(_stream, cancel=cancel, name="mico360-ollama-chat")
        except InterruptedError:
            raise
        except Exception as exc:
            if _is_timeout(exc):
                raise RuntimeError(
                    f"Ollama did not respond at {self.host} in time. Check that "
                    "Ollama is running and reachable, or try a smaller model.") from exc
            raise

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
