"""Transport-agnostic minutes-generation pipeline.

Both the local (Ollama) and cloud (MICO360 Connect) providers share this
map-reduce strategy: long transcripts are condensed chunk-by-chunk into factual
notes, then a final pass merges the notes into the chosen minutes style. Short
transcripts go through a single pass. The only thing that differs between
providers is HOW a single prompt is sent to a model — passed in as ``chat``.
"""
from __future__ import annotations

import logging
from typing import Callable

from . import cleaning, prompts

log = logging.getLogger("mico360.generation")

ProgressCb = Callable[[float, str], None]
CancelCb = Callable[[], bool]
# chat(prompt, cancel) -> completion text
ChatFn = Callable[[str, "CancelCb | None"], str]


def run_minutes_pipeline(
    chat: ChatFn,
    model: str,
    transcript: str,
    template: str,
    style: str = prompts.DEFAULT_STYLE,
    remove_fillers: bool = True,
    chunk_chars: int = 6000,
    progress: ProgressCb | None = None,
    cancel: CancelCb | None = None,
) -> str:
    """Generate minutes from a transcript using ``chat`` as the model transport."""
    if not model:
        raise ValueError("No AI model selected.")

    if progress:
        progress(0.03, "Cleaning transcript…")
    cleaned = cleaning.clean_transcript(transcript, remove_filler_words=remove_fillers)
    if not cleaned.strip():
        raise ValueError("Transcript is empty after cleaning.")

    chunks = cleaning.chunk_text(cleaned, max_chars=chunk_chars)
    log.info("generating minutes: style=%s chunks=%d model=%s", style, len(chunks), model)

    # --- Single-pass for short transcripts ---
    if len(chunks) <= 1:
        if progress:
            progress(0.15, f"Generating {style} with {model} — the first request can take up to "
                           "a minute while the model loads…")
        prompt = prompts.build_generation_prompt(template, style, cleaned)
        result = chat(prompt, cancel)
        if progress:
            progress(1.0, "Minutes generated.")
        return result

    # --- Map: condense each chunk ---
    notes: list[str] = []
    n = len(chunks)
    for i, ch in enumerate(chunks, start=1):
        if progress:
            note = " (the first request can take up to a minute)" if i == 1 else ""
            progress(0.1 + 0.7 * (i - 1) / n, f"Analyzing part {i} of {n}…{note}")
        p = prompts.CHUNK_SUMMARY_PROMPT.format(idx=i, total=n).replace(
            prompts.TRANSCRIPT_TOKEN, ch
        )
        notes.append(f"### Part {i}\n" + chat(p, cancel))

    # --- Reduce: merge into final minutes ---
    if progress:
        progress(0.85, f"Composing {style}…")
    reduce_prompt = prompts.build_reduce_prompt(template, style, "\n\n".join(notes))
    result = chat(reduce_prompt, cancel)
    if progress:
        progress(1.0, "Minutes generated.")
    return result
