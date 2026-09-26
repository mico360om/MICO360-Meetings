"""Transport-agnostic minutes-generation pipeline.

Both the local (Ollama) and cloud (MICO360 Connect) providers share this
map-reduce strategy: long transcripts are condensed chunk-by-chunk into factual
notes, then a final pass merges the notes into the chosen minutes style. Short
transcripts go through a single pass. The only thing that differs between
providers is HOW a single prompt is sent to a model — passed in as ``chat``.

When a meeting is so long that even the combined notes would not fit a model's
context comfortably, the notes are first merged in groups (hierarchically)
until they fit the reduce budget, so the final prompt never silently overflows
and loses the start of the meeting (H15).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, TypeVar

from . import cleaning, prompts

log = logging.getLogger("mico360.generation")

ProgressCb = Callable[[float, str], None]
CancelCb = Callable[[], bool]
# chat(prompt, cancel) -> completion text
ChatFn = Callable[[str, "CancelCb | None"], str]

T = TypeVar("T")

# Upper bound (characters) for the combined part-notes handed to the final
# reduce step. Above it the notes are merged in groups first. ~12k characters
# is ~3.5k tokens — with the template and the answer it fits an 8k context.
REDUCE_NOTES_BUDGET = 12000
_MAX_MERGE_ROUNDS = 4


# Finished part-notes, kept in memory so a Retry after a failure (or a Cancel)
# part-way through a long meeting reuses every part that already completed
# instead of starting over (M27). Keyed by model + the exact part prompt, so a
# changed transcript, template or model never reuses stale notes. Bounded LRU.
_PART_CACHE_MAX = 256
_part_cache: dict[str, str] = {}                     # insertion-ordered (LRU)
_part_lock = threading.Lock()


def _part_key(model: str, prompt: str) -> str:
    import hashlib
    return hashlib.sha256(f"{model}\x00{prompt}".encode("utf-8")).hexdigest()


def _cache_get(key: str) -> str | None:
    with _part_lock:
        val = _part_cache.pop(key, None)
        if val is not None:
            _part_cache[key] = val                   # mark most recently used
        return val


def _cache_put(key: str, value: str) -> None:
    with _part_lock:
        _part_cache.pop(key, None)
        _part_cache[key] = value
        while len(_part_cache) > _PART_CACHE_MAX:
            _part_cache.pop(next(iter(_part_cache)))


def clear_part_cache() -> None:
    with _part_lock:
        _part_cache.clear()


class DeadlineExceeded(TimeoutError):
    """A bounded background call did not finish within its deadline."""


def run_in_thread(fn: Callable[[], T], cancel: CancelCb | None = None,
                  deadline: float | None = None, poll: float = 0.1,
                  name: str = "mico360-call") -> T:
    """Run ``fn`` on a daemon thread while the caller stays responsive.

    * ``cancel`` is polled every ``poll`` seconds; when it returns True an
      :class:`InterruptedError` is raised at once, even if ``fn`` is stuck in a
      blocking socket read (e.g. a stalled model load). The abandoned call ends
      on its own at its transport timeout.
    * ``deadline`` (seconds) bounds the total wall time, covering steps that
      socket timeouts don't (DNS resolution, TLS stalls): on expiry
      :class:`DeadlineExceeded` is raised.

    Exceptions raised by ``fn`` are re-raised in the caller.
    """
    import time
    box: dict = {}
    done = threading.Event()

    def _target():
        try:
            box["value"] = fn()
        except BaseException as exc:          # re-raised in the caller
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_target, name=name, daemon=True).start()
    end = (time.monotonic() + deadline) if deadline is not None else None
    while not done.wait(poll):
        if cancel is not None and cancel():
            raise InterruptedError("Generation cancelled.")
        if end is not None and time.monotonic() >= end:
            raise DeadlineExceeded(f"no response within {deadline:g} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _check_cancel(cancel: CancelCb | None) -> None:
    if cancel and cancel():
        raise InterruptedError("Generation cancelled.")


def _group_notes(notes: list[str], budget: int) -> list[list[str]]:
    """Pack consecutive notes into groups whose joined size stays <= budget.
    Every group that can hold two notes does; a note larger than the budget
    travels alone (merging it still condenses it)."""
    groups: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for n in notes:
        add = len(n) + 2
        if cur and size + add > budget:
            groups.append(cur)
            cur, size = [], 0
        cur.append(n)
        size += add
    if cur:
        groups.append(cur)
    if len(groups) == len(notes) and len(notes) > 1:
        # nothing could be paired within the budget: merge neighbours pairwise
        groups = [notes[i:i + 2] for i in range(0, len(notes), 2)]
    return groups


def merge_notes_hierarchically(chat: ChatFn, notes: list[str], total_parts: int,
                               budget: int = REDUCE_NOTES_BUDGET,
                               progress: ProgressCb | None = None,
                               cancel: CancelCb | None = None,
                               span: tuple[float, float] = (0.75, 0.85)) -> list[str]:
    """Merge part-notes in groups until their combined size fits ``budget``.

    Order is preserved and each merged block is labelled with the parts it
    covers. Returns the (possibly unchanged) list of note blocks.
    """
    # labels: which original parts each block covers
    level = list(notes)
    ranges = [(i, i) for i in range(1, len(notes) + 1)]
    rounds = 0
    while (sum(len(n) + 2 for n in level) > budget and len(level) > 1
           and rounds < _MAX_MERGE_ROUNDS):
        rounds += 1
        groups = _group_notes(level, budget)
        new_level: list[str] = []
        new_ranges: list[tuple[int, int]] = []
        idx = 0
        for gi, g in enumerate(groups, start=1):
            first, last = ranges[idx][0], ranges[idx + len(g) - 1][1]
            idx += len(g)
            if len(g) == 1:
                new_level.append(g[0])
                new_ranges.append((first, last))
                continue
            _check_cancel(cancel)
            if progress:
                lo, hi = span
                progress(lo + (hi - lo) * (gi - 1) / max(1, len(groups)),
                         f"Combining notes for parts {first}–{last} of {total_parts}…")
            p = prompts.build_merge_notes_prompt("\n\n".join(g), first, last, total_parts)
            merged = chat(p, cancel)
            new_level.append(f"### Parts {first}–{last}\n{merged}")
            new_ranges.append((first, last))
        log.info("merge round %d: %d -> %d note blocks", rounds, len(level), len(new_level))
        if len(new_level) >= len(level):          # no progress possible
            level, ranges = new_level, new_ranges
            break
        level, ranges = new_level, new_ranges
    return level


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
    reduce_budget: int | None = None,
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
        _check_cancel(cancel)
        if progress:
            note = " (the first request can take up to a minute)" if i == 1 else ""
            progress(0.1 + 0.65 * (i - 1) / n, f"Analyzing part {i} of {n}…{note}")
        p = prompts.CHUNK_SUMMARY_PROMPT.format(idx=i, total=n).replace(
            prompts.TRANSCRIPT_TOKEN, ch
        )
        key = _part_key(model, p)
        part = _cache_get(key)
        if part is None:
            part = chat(p, cancel)
            _cache_put(key, part)                     # kept even if a later part fails
        else:
            log.info("reusing finished notes for part %d of %d", i, n)
        notes.append(f"### Part {i}\n" + part)

    # --- Hierarchical merge when the notes are too long for one reduce ---
    budget = reduce_budget or max(REDUCE_NOTES_BUDGET, 2 * int(chunk_chars or 0))
    notes = merge_notes_hierarchically(chat, notes, n, budget=budget,
                                       progress=progress, cancel=cancel)

    # --- Reduce: merge into final minutes ---
    _check_cancel(cancel)
    if progress:
        progress(0.85, f"Composing {style}…")
    reduce_prompt = prompts.build_reduce_prompt(template, style, "\n\n".join(notes))
    result = chat(reduce_prompt, cancel)
    if progress:
        progress(1.0, "Minutes generated.")
    return result
