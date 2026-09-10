"""Local Ollama integration: model discovery + minutes generation.

For long transcripts we use a map-reduce strategy: each chunk is condensed
into factual notes, then a final pass merges the notes into the chosen minutes
style. Short transcripts go through a single pass. All processing is local.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from . import prompts
from .generation import ProgressCb, run_minutes_pipeline

log = logging.getLogger("mico360.ollama")


@dataclass
class OllamaStatus:
    running: bool
    models: list[str]
    error: str = ""


def _client(host: str):
    from ollama import Client
    return Client(host=host)


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
    for part in client.pull(model, stream=True):
        if cancel and cancel():
            raise InterruptedError("Model download cancelled.")
        status = part.get("status", "")
        total = part.get("total") or 0
        completed = part.get("completed") or 0
        frac = (completed / total) if total else None
        if progress:
            progress(frac, status, completed, total)


def check_status(host: str = "http://127.0.0.1:11434") -> OllamaStatus:
    """Return whether the Ollama server is reachable and which models exist."""
    try:
        resp = _client(host).list()
        models = []
        for m in resp.get("models", []):
            name = m.get("model") or m.get("name")
            if name:
                models.append(name)
        return OllamaStatus(running=True, models=sorted(models))
    except Exception as exc:
        log.warning("ollama not reachable at %s: %s", host, exc)
        return OllamaStatus(running=False, models=[], error=str(exc))


class OllamaGenerator:
    def __init__(self, host: str = "http://127.0.0.1:11434", model: str = ""):
        self.host = host
        self.model = model

    def _chat(self, prompt: str, cancel: Callable[[], bool] | None = None) -> str:
        client = _client(self.host)
        out: list[str] = []
        stream = client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            options={"temperature": 0.2},
        )
        for part in stream:
            if cancel and cancel():
                raise InterruptedError("Generation cancelled.")
            out.append(part.get("message", {}).get("content", ""))
        return "".join(out).strip()

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
