"""Transcript cleaning and chunking utilities.

- Remove true disfluencies ("um", "uh", "erm", "hmm" …) — never phrases that
  can carry meaning ("you know", "I mean", "kind of", "like", "actually").
- Collapse obvious stutters of function words ("the the" -> "the") but keep
  legitimate repetitions ("that that plan", "had had").
- Collapse immediate repeated lines from STT artifacts.
- Split long transcripts into character-bounded chunks on sentence / line
  boundaries (Latin, Arabic, Urdu and CJK punctuation), never mid-word, with a
  trailing overlap so each chunk fits comfortably in the LLM context.
"""
from __future__ import annotations

import re

# Only pure hesitation sounds. They never change what was said. Phrases such as
# "you know" ("Do you know if…"), "I mean", "kind of" ("What kind of
# contract"), "sort of", "like", "basically", "actually", "literally", "right?"
# and "mm-hmm" (= yes) are deliberately NOT removed (M25).
FILLERS = [
    "um", "umm", "ummm", "uhm", "uh", "uhh", "uhhh", "erm",
    "hmm", "hmmm", "hm", "mmm",
]
# ("er" is not listed: it is a real word in German — "er" = "he".)

_F = r"(?<![\w'-])(?:" + "|".join(re.escape(f) for f in FILLERS) + r")(?![\w'-])"
# a filler wrapped in commas ("we, uh, should") -> drop both commas
_COMMA_WRAPPED_RE = re.compile(r",[ \t]*" + _F + r"[ \t]*,", flags=re.IGNORECASE)
# a filler with the punctuation glued to it and the space after it
_FILLER_RE = re.compile(
    _F + r"(?P<p>[ \t]*(?:\.\.\.|…|[,.!?]))?(?P<ws>[ \t]*)", flags=re.IGNORECASE)
_WS_RE = re.compile(r"[ \t]{2,}")
_CAP = "\x00"                                   # "capitalise what follows" marker

# Words that are (practically) never repeated on purpose in English speech, so
# "the the" / "I I" / "and and" are stutters. Words that CAN legitimately
# repeat are left alone: "that that plan", "had had", "what it is is",
# "log in in", "so-so", "no no", "very very", "told you you should" …
_STUTTER_WORDS = [
    "the", "a", "an", "i", "and", "to", "of", "we", "but", "or", "our", "your",
    "their", "for", "with", "this", "they", "he", "she", "if",
]
_REPEAT_WORD_RE = re.compile(
    r"\b(" + "|".join(_STUTTER_WORDS) + r")(?:[ \t]+\1\b)+",
    flags=re.IGNORECASE,
)


def _drop_filler(m: re.Match) -> str:
    before = m.string[:m.start()].rstrip(" \t")
    if not before or before[-1] in "\n.!?…":
        return _CAP                             # sentence-initial: drop it + its punctuation
    p = (m.group("p") or "").strip()
    keep = p if p in (".", "!", "?") else ""    # a sentence end belongs to the sentence
    return keep + m.group("ws")


def remove_fillers(text: str) -> str:
    """Remove hesitation sounds only (M25) and collapse function-word stutters."""
    text = _COMMA_WRAPPED_RE.sub(" ", text)
    text = _FILLER_RE.sub(_drop_filler, text)
    text = re.sub(_CAP + r"[ \t]*([a-z])", lambda m: m.group(1).upper(), text)
    text = text.replace(_CAP, "")
    text = _REPEAT_WORD_RE.sub(r"\1", text)            # "the the the" -> "the"
    text = re.sub(r"[ \t]+([,.!?;:])", r"\1", text)    # space before punctuation
    text = re.sub(r",([.!?])", r"\1", text)            # "Yes, um." -> "Yes."
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"(?m)^[ \t]+(?=\S)", "", text)
    return text


def dedup_lines(text: str) -> str:
    out: list[str] = []
    prev = None
    for line in text.splitlines():
        norm = line.strip().lower()
        if norm and norm == prev:
            continue
        out.append(line)
        prev = norm
    return "\n".join(out)


def clean_transcript(text: str, remove_filler_words: bool = True) -> str:
    text = text.replace("\r\n", "\n").strip()
    text = dedup_lines(text)
    if remove_filler_words:
        text = remove_fillers(text)
    # tidy blank runs
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Sentence ends: Latin . ! ? …, Arabic question mark ؟, Urdu full stop ۔,
# Devanagari danda । and full-width/CJK 。！？ (which need no following space).
# Newlines always end a segment (speaker turns, timestamps).
_BOUNDARY_RE = re.compile(
    r"(?<=[.!?…؟۔।。！？])[ \t]+"
    r"|[ \t]*\n\s*"
    r"|(?<=[。！？])(?=\S)"
)


def _split_sentences(text: str) -> list[str]:
    """Split into sentence/line segments. Each segment keeps its trailing
    separator, so ``"".join(segments) == text`` (line breaks survive)."""
    segs: list[str] = []
    start = 0
    for m in _BOUNDARY_RE.finditer(text):
        end = m.end()
        if end <= start:
            continue
        segs.append(text[start:end])
        start = end
    if start < len(text):
        segs.append(text[start:])
    return [s for s in segs if s.strip()]


def _split_long(seg: str, step: int, limit: int) -> list[str]:
    """Break an over-long segment at whitespace into pieces of about ``step``
    characters (never more than ``limit``). A word is only cut when a single
    run without whitespace is longer than ``limit`` (e.g. unspaced CJK)."""
    pieces: list[str] = []
    pos, n = 0, len(seg)
    while pos < n:
        if n - pos <= step:
            pieces.append(seg[pos:])
            break
        window = seg[pos:pos + step + 1]
        cut = max(window.rfind(" "), window.rfind("\t"), window.rfind("\n"))
        if cut > 0:
            end = pos + cut                          # the space starts the next piece
        else:                                        # one word longer than step
            m = re.search(r"\s", seg[pos + step:pos + limit + 1])
            end = (pos + step + m.start()) if m else pos + limit
        pieces.append(seg[pos:end])
        pos = end
    return pieces


def chunk_text(text: str, max_chars: int = 6000, overlap_chars: int = 400) -> list[str]:
    """Pack text into chunks <= max_chars on sentence boundaries.

    Consecutive chunks share ~overlap_chars of trailing context from the
    previous chunk so points spanning a boundary aren't lost during
    summarization. Text without punctuation is split at whitespace (never
    mid-word) and still overlaps.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    max_chars = max(1, int(max_chars))
    overlap_chars = max(0, min(int(overlap_chars), max_chars // 2))

    # pieces small enough that a tail of them can seed the next chunk
    step = max(1, min(max_chars, overlap_chars or max_chars))
    segments: list[str] = []
    for seg in _split_sentences(text):
        if len(seg) > max_chars:
            segments.extend(_split_long(seg, step, max_chars))
        else:
            segments.append(seg)

    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    fresh = 0                                      # segments added since the last flush
    for seg in segments:
        s_len = len(seg)
        if size + s_len > max_chars and cur:
            if fresh:
                chunks.append("".join(cur).strip())
                # seed the next chunk with a tail of this one for continuity
                tail, tlen = [], 0
                for s in reversed(cur):
                    if tlen + len(s) > overlap_chars:
                        break
                    tail.insert(0, s)
                    tlen += len(s)
                cur, size = tail, tlen
            if size + s_len > max_chars:           # tail + segment still too big
                cur, size = [], 0
            fresh = 0
        cur.append(seg)
        size += s_len
        fresh += 1
    if cur and fresh:
        chunks.append("".join(cur).strip())
    return [c for c in chunks if c]
