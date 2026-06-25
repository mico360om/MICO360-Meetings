"""Transcript cleaning and chunking utilities.

- Remove common filler words and stutters.
- Collapse immediate repeated lines/words from STT artifacts.
- Split long transcripts into character-bounded chunks on sentence
  boundaries so each chunk fits comfortably in the LLM context.
"""
from __future__ import annotations

import re

FILLERS = [
    "um", "uh", "erm", "ah", "uhh", "umm", "hmm", "mm-hmm", "mmhmm",
    "you know", "i mean", "sort of", "kind of", "like,", "basically",
    "actually,", "literally", "right?", "okay so", "so yeah",
]

_FILLER_RE = re.compile(
    r"\b(" + "|".join(re.escape(f) for f in FILLERS) + r")\b",
    flags=re.IGNORECASE,
)
_WS_RE = re.compile(r"[ \t]{2,}")
_REPEAT_WORD_RE = re.compile(r"\b(\w+)(\s+\1\b){1,}", flags=re.IGNORECASE)


def remove_fillers(text: str) -> str:
    text = _FILLER_RE.sub("", text)
    text = _REPEAT_WORD_RE.sub(r"\1", text)          # "the the the" -> "the"
    text = re.sub(r"\s+([,.!?])", r"\1", text)         # space before punctuation
    text = _WS_RE.sub(" ", text)
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


def _split_sentences(text: str) -> list[str]:
    # split keeping delimiters; good enough for chunk packing
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p for p in parts if p.strip()]


def chunk_text(text: str, max_chars: int = 6000, overlap_chars: int = 400) -> list[str]:
    """Pack text into chunks <= max_chars on sentence boundaries.

    Consecutive chunks share ~overlap_chars of trailing context from the
    previous chunk so points spanning a boundary aren't lost during summarization.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []

    sentences = _split_sentences(text)
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for sent in sentences:
        s_len = len(sent) + 1
        if size + s_len > max_chars and cur:
            chunks.append(" ".join(cur).strip())
            # seed next chunk with a tail of the current one for continuity
            tail, tlen = [], 0
            for s in reversed(cur):
                if tlen + len(s) > overlap_chars:
                    break
                tail.insert(0, s); tlen += len(s) + 1
            cur, size = list(tail), tlen
        if s_len > max_chars:  # a single huge sentence: hard-split
            for i in range(0, len(sent), max_chars):
                chunks.append(sent[i:i + max_chars])
            cur, size = [], 0
            continue
        cur.append(sent)
        size += s_len
    if cur:
        chunks.append(" ".join(cur).strip())
    return [c for c in chunks if c]
