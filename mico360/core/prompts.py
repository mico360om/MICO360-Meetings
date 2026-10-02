"""Prompt templates, output styles, and the user-editable Prompt Library.

The base instruction is deliberately conservative: never invent names, dates,
decisions or action items; use 'Not specified' when information is missing.
Users can edit the prompt per-upload and save reusable prompts to the library.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from ..config import PROMPTS_DIR, atomic_write_text

log = logging.getLogger("mico360.prompts")

TRANSCRIPT_TOKEN = "[TRANSCRIPT_HERE]"

# The structure every style must fill in.
MINUTES_STRUCTURE = """\
Use EXACTLY these Markdown headings (omit none; write 'Not specified' when unknown):

# Meeting Minutes
**Meeting Title:** ...
**Date and Time:** ...
**Attendees:** ...

## Purpose of Meeting
...

## Meeting Summary
...

## Key Discussion Points
- ...

## Decisions Made
- ...

## Action Items
| Task | Responsible Person | Deadline | Status |
| --- | --- | --- | --- |
| ... | ... | ... | Pending |

## Pending Issues
- ...

## Risks or Concerns
- ...

## Next Meeting Notes
...

## Closing Summary
...
"""

BASE_TEMPLATE = (
    "You are a professional meeting secretary. Convert the following meeting "
    "transcript into accurate and formal minutes of meeting. Do not invent names, "
    "dates, decisions, or action items. If information is missing, write "
    "'Not specified'. Clearly mark unclear speakers or unclear audio as "
    "'[unclear]'. Organize the output clearly using headings and bullet points.\n\n"
    f"{MINUTES_STRUCTURE}\n"
    "Transcript:\n" + TRANSCRIPT_TOKEN
)

# Per-style guidance appended to the base instruction.
OUTPUT_STYLES: dict[str, str] = {
    "Formal Minutes":
        "Write complete, formal minutes covering every section in full sentences.",
    "Short Summary":
        "Write a concise summary. Keep Meeting Summary to 3-4 sentences and use "
        "brief bullets. Still include all headings.",
    "Detailed Minutes":
        "Write thorough, detailed minutes. Expand discussion points and capture "
        "nuance, context, and rationale for each decision.",
    "Action Item Report":
        "Focus on actionable outcomes. Keep narrative sections brief but make the "
        "Action Items table comprehensive with clear owners, deadlines and status.",
    "Executive Summary":
        "Write for executives: a tight summary, the key decisions, top risks, and "
        "the most important action items. Keep it scannable.",
}

DEFAULT_STYLE = "Formal Minutes"

# Map-reduce: per-chunk condensation prompt (used only for long transcripts).
CHUNK_SUMMARY_PROMPT = (
    "You are summarizing PART of a longer meeting transcript. Extract only what is "
    "actually stated: key points, decisions, action items (with owner/deadline if "
    "given), risks, and open issues. Do not invent anything. Be concise and factual.\n\n"
    "Transcript part {idx} of {total}:\n" + TRANSCRIPT_TOKEN
)


def build_generation_prompt(template: str, style: str, transcript: str) -> str:
    style_hint = OUTPUT_STYLES.get(style, "")
    base = template if TRANSCRIPT_TOKEN in template else template + "\n\nTranscript:\n" + TRANSCRIPT_TOKEN
    base = base.replace(TRANSCRIPT_TOKEN, transcript)
    if style_hint:
        base = f"{base}\n\nStyle instruction: {style_hint}"
    return base


REDUCE_NOTES_PREFIX = (
    "[This meeting was long, so it was processed in parts. Below are factual notes "
    "extracted from each consecutive part, in order. Treat them as the transcript: "
    "merge them into one coherent result and do not add anything that is not in "
    "the notes.]\n\n"
)


def build_reduce_prompt(template: str, style: str, partial_notes: str) -> str:
    """Final step for long meetings: the SAME template the user chose, with the
    merged part-notes in place of the transcript.

    The whole template is kept — the text before AND after the transcript token,
    and no extra minutes headings are forced in — so non-minutes templates
    (TL;DR, follow-up email) and instructions such as "write the output in
    Arabic" still apply to long meetings, exactly as they do to short ones.
    """
    return build_generation_prompt(template, style, REDUCE_NOTES_PREFIX + partial_notes)


# Hierarchical reduce (very long meetings): consecutive part-notes are first
# merged in groups so the final reduce prompt fits the model's context.
MERGE_NOTES_PROMPT = (
    "You are condensing notes from a long meeting that was processed in parts. "
    "Below are factual notes for parts {first} to {last} of {total}, in order. "
    "Merge them into ONE set of concise notes in the same order. Keep every key "
    "point, decision, action item (with owner and deadline if given), risk and "
    "open issue; remove only exact repetition. Do not invent anything and do not "
    "write minutes yet — output notes only.\n\n"
    "Notes:\n" + TRANSCRIPT_TOKEN
)


def build_merge_notes_prompt(notes: str, first: int, last: int, total: int) -> str:
    return MERGE_NOTES_PROMPT.format(first=first, last=last, total=total).replace(
        TRANSCRIPT_TOKEN, notes)


# ---------------------------------------------------------------------------
# Prompt Library  (add / edit / view / delete)
# ---------------------------------------------------------------------------
@dataclass
class SavedPrompt:
    id: str
    name: str
    text: str
    builtin: bool = False
    category: str = "General"
    favorite: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "prompt"


# Categories the library is organised under.
PROMPT_CATEGORIES = [
    "Formal Meeting", "Project Meeting", "Client Meeting",
    "Internal Meeting", "Action Items", "Summary",
]


def _struct() -> str:
    return MINUTES_STRUCTURE


# 14 ready-to-use prompts (covers the common meeting types). Each is seeded as a
# built-in on first run; existing installs get any that are missing, by name.
BUILTIN_PROMPTS: list[tuple[str, str, str]] = [
    ("Formal Minutes", "Formal Meeting", BASE_TEMPLATE),
    ("Board Meeting Minutes", "Formal Meeting",
     "You are the company secretary recording formal board minutes. Be precise and "
     "neutral. Record attendees, quorum, motions proposed, who moved/seconded, the "
     "vote outcome, and resolutions passed. Do not invent anything; use 'Not specified' "
     "if missing.\n\n" + MINUTES_STRUCTURE + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Project Kickoff Minutes", "Project Meeting",
     "You are documenting a project kickoff. Capture project goals, scope, milestones, "
     "owners, dependencies, risks and the agreed next steps. Do not invent details.\n\n"
     + MINUTES_STRUCTURE + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Project Status / Standup", "Project Meeting",
     "Summarise this status/standup meeting. For each person/workstream capture: what "
     "was done, what's next, and any blockers. Then list overall decisions, risks and "
     "action items with owners and deadlines. Be concise; do not invent.\n\n"
     + MINUTES_STRUCTURE + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Sprint Retrospective", "Project Meeting",
     "Summarise this retrospective under three headings — 'What went well', 'What didn't "
     "go well', and 'Improvements / Actions' (with owners). Keep the standard minutes "
     "sections too. Only use what is stated.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Client Meeting Recap", "Client Meeting",
     "You are writing a professional client meeting recap. Capture the client's needs, "
     "questions raised, commitments made by each side, decisions, and clear next steps "
     "with owners and dates. Polite, professional tone. Do not invent.\n\n"
     + MINUTES_STRUCTURE + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Sales Discovery Call Notes", "Client Meeting",
     "Summarise this sales discovery call. Capture: prospect/company, pain points, "
     "budget/authority/need/timeline if mentioned, objections, and agreed next steps. "
     "Do not fabricate figures or names.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Internal Team Meeting Notes", "Internal Meeting",
     "Summarise this internal team meeting clearly and informally but professionally. "
     "Capture discussion points, decisions, and action items with owners and deadlines. "
     "Use 'Not specified' where unknown.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("One-on-One Notes", "Internal Meeting",
     "Summarise this one-on-one meeting. Capture topics discussed, feedback given, "
     "agreements, and follow-up actions with dates. Keep it concise and respectful.\n\n"
     + MINUTES_STRUCTURE + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Interview Notes Summary", "Internal Meeting",
     "Summarise this interview. Capture the candidate/role, key questions and answers, "
     "strengths, concerns, and a recommendation if one was stated. Be objective; do not "
     "infer beyond what was said.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Brainstorming Session Summary", "Internal Meeting",
     "Summarise this brainstorming session. Group the ideas by theme, note which were "
     "favoured, and list any agreed next steps or owners. Capture every distinct idea "
     "mentioned without inventing new ones.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Action Item Report", "Action Items",
     "Focus only on outcomes. Produce a thorough Action Items table (Task, Responsible "
     "Person, Deadline, Status) plus a short list of Decisions Made. Keep narrative "
     "sections brief. Do not invent owners or dates; use 'Not specified'.\n\n"
     + MINUTES_STRUCTURE + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Decision Log", "Action Items",
     "Extract only the DECISIONS made in this meeting. For each decision give: the "
     "decision, who made/approved it, the rationale if stated, and any follow-up action. "
     "Then add a brief Action Items table. Do not invent decisions.\n\n"
     + MINUTES_STRUCTURE + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Executive Summary", "Summary",
     "Write a tight executive summary for leadership: the purpose, the 3-5 most important "
     "points, key decisions, top risks, and the most important action items. Scannable, "
     "no fluff. Do not invent.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Short Summary", "Summary",
     "Write a concise summary of the meeting: 3-4 sentence overview, brief bullet key "
     "points, decisions, and action items. Keep every heading but stay brief.\n\n"
     + MINUTES_STRUCTURE + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    # --- 10 more commonly-used prompts ---
    ("Stakeholder Update", "Project Meeting",
     "Summarise this meeting as a stakeholder update: progress since last time, current "
     "status (on track / at risk), key decisions, blockers needing help, and next "
     "milestones with dates. Concise and executive-friendly. Do not invent.\n\n"
     + MINUTES_STRUCTURE + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Daily Standup Bullets", "Project Meeting",
     "Produce brief standup notes. For each person/workstream: Yesterday, Today, "
     "Blockers. Then a short combined list of blockers and action items with owners. "
     "Keep it to tight bullets; do not invent.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Vendor / Supplier Meeting Notes", "Client Meeting",
     "Summarise this vendor/supplier meeting. Capture: vendor, products/services "
     "discussed, pricing or terms mentioned, commitments by each side, SLAs, risks, and "
     "next steps with owners and dates. Do not invent figures.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Customer Feedback Session", "Client Meeting",
     "Summarise this customer feedback session. Group feedback into themes (likes, "
     "pain points, feature requests), note severity/frequency if stated, and list "
     "follow-up actions with owners. Only use what was said.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Performance Review Notes", "Internal Meeting",
     "Summarise this performance/review discussion professionally and objectively. "
     "Capture: achievements, strengths, areas to improve, goals agreed, and follow-up "
     "actions with dates. Do not infer beyond what was discussed.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("All-Hands / Town Hall Summary", "Internal Meeting",
     "Summarise this all-hands/town-hall for staff who missed it. Capture announcements, "
     "key updates by area, decisions, Q&A highlights, and any actions for the team. "
     "Clear and upbeat but factual.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Training / Workshop Notes", "Internal Meeting",
     "Summarise this training/workshop. Capture topics covered, key learnings, exercises "
     "or demos, questions raised, resources mentioned, and any follow-up actions for "
     "attendees. Do not invent content.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Risk & Issues Review", "Action Items",
     "Focus on risks and issues. For each: description, impact, likelihood/severity if "
     "stated, owner, and mitigation/next step. Then a short Action Items table. Only use "
     "what is stated; mark unknowns 'Not specified'.\n\n" + MINUTES_STRUCTURE
     + "\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Follow-up Email Draft", "Summary",
     "Write a short, professional follow-up email that could be sent after this meeting. "
     "Use this structure: Subject line; a one-line thank-you; 3-5 bullet recap of what "
     "was discussed/decided; a clear list of next steps with owners and dates; a polite "
     "closing. Do not invent names, dates or commitments.\n\nTranscript:\n" + TRANSCRIPT_TOKEN),
    ("Key Takeaways (TL;DR)", "Summary",
     "Give the meeting's key takeaways as a tight bulleted TL;DR (5-8 bullets max) "
     "covering the most important points, decisions and next steps. No headings, no "
     "fluff, only what was actually said.\n\nTranscript:\n" + TRANSCRIPT_TOKEN),
]


class PromptLibrary:
    """JSON-file-per-prompt library under the prompts data directory."""

    def __init__(self, directory: Path = PROMPTS_DIR):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ensure_default()

    def _ensure_default(self) -> None:
        # Seed any built-in prompt that isn't already present (by name), so new
        # AND existing installs end up with the full library.
        existing = {p.name for p in self.list()}
        for name, category, text in BUILTIN_PROMPTS:
            if name not in existing:
                self.add(name, text, builtin=True, category=category)

    def _from_dict(self, d: dict) -> SavedPrompt:
        valid = {f.name for f in fields(SavedPrompt)}
        p = SavedPrompt(**{k: v for k, v in d.items() if k in valid})
        # a hand-edited / imported file can hold anything: keep the types sane
        p.name = str(p.name or "").strip() or "Untitled"
        p.text = "" if p.text is None else str(p.text)
        p.category = str(p.category or "").strip() or "General"
        p.builtin, p.favorite = bool(p.builtin), bool(p.favorite)
        return p

    def list(self) -> list[SavedPrompt]:
        items: list[SavedPrompt] = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                items.append(self._from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except Exception:
                log.warning("skipping bad prompt file %s", f, exc_info=True)
        items.sort(key=lambda p: (p.category.lower(), p.name.lower()))
        return items

    def get(self, prompt_id: str) -> SavedPrompt | None:
        f = self.dir / f"{prompt_id}.json"
        if f.exists():
            return self._from_dict(json.loads(f.read_text(encoding="utf-8")))
        return None

    def add(self, name: str, text: str, builtin: bool = False,
            category: str = "General") -> SavedPrompt:
        pid = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
        p = SavedPrompt(id=pid, name=name.strip() or "Untitled", text=text,
                        builtin=builtin, category=category)
        self._write(p)
        return p

    def update(self, prompt_id: str, name: str, text: str,
               category: str | None = None) -> SavedPrompt | None:
        p = self.get(prompt_id)
        if not p:
            return None
        p.name = name.strip() or p.name
        p.text = text
        if category is not None:
            p.category = category
        self._write(p)
        return p

    def delete(self, prompt_id: str) -> bool:
        f = self.dir / f"{prompt_id}.json"
        if f.exists():
            f.unlink()
            return True
        return False

    def set_favorite(self, prompt_id: str, favorite: bool) -> SavedPrompt | None:
        p = self.get(prompt_id)
        if not p:
            return None
        p.favorite = favorite
        self._write(p)
        return p

    def unique_name(self, name: str) -> str:
        """`name`, or "name (2)", "name (3)" ... if a prompt already has it."""
        taken = {p.name.strip().lower() for p in self.list()}
        base = (name or "").strip() or "Untitled"
        if base.lower() not in taken:
            return base
        n = 2
        while f"{base} ({n})".lower() in taken:
            n += 1
        return f"{base} ({n})"

    def duplicate(self, prompt_id: str) -> SavedPrompt | None:
        """Copy a prompt into a new editable Custom prompt (never a built-in).
        The text and category are copied exactly; the name is made unique."""
        src = self.get(prompt_id)
        if not src:
            return None
        return self.add(self.copy_name(src.name), src.text, builtin=False,
                        category=src.category)

    def copy_name(self, name: str) -> str:
        """"X (copy)", then "X (copy 2)", "X (copy 3)" ..."""
        taken = {p.name.strip().lower() for p in self.list()}
        cand, n = f"{name} (copy)", 2
        while cand.lower() in taken:
            cand, n = f"{name} (copy {n})", n + 1
        return cand

    # -- built-ins ----------------------------------------------------------
    @staticmethod
    def builtin_text(name: str) -> str | None:
        """The shipped text of a built-in prompt (None if `name` isn't one)."""
        for n, _cat, text in BUILTIN_PROMPTS:
            if n == name:
                return text
        return None

    def is_modified(self, p: SavedPrompt) -> bool:
        """True for a built-in whose text was edited away from the shipped one."""
        shipped = self.builtin_text(p.name) if p.builtin else None
        return shipped is not None and shipped != p.text

    def restore_builtin(self, prompt_id: str) -> SavedPrompt | None:
        """Put a built-in's shipped text and category back."""
        p = self.get(prompt_id)
        if not p or not p.builtin:
            return None
        for n, cat, text in BUILTIN_PROMPTS:
            if n == p.name:
                p.text, p.category = text, cat
                self._write(p)
                return p
        return None

    # -- import / export ----------------------------------------------------
    def export_file(self, prompts: list[SavedPrompt], path: str | Path) -> Path:
        """Write prompts to a JSON file that import_file() reads back exactly
        (name, category, favourite and the text character-for-character)."""
        path = Path(path)
        rows = [{"name": p.name, "category": p.category, "favorite": p.favorite,
                 "text": p.text} for p in prompts]
        atomic_write_text(path, json.dumps({"mico360_prompts": 1, "prompts": rows},
                                           indent=2, ensure_ascii=False))
        return path

    def import_file(self, path: str | Path) -> "PromptImportResult":
        """Import prompts from a JSON export, or one prompt from a .txt / .md
        file (named after the file). Nothing existing is overwritten: an
        identical prompt is skipped, a different one with the same name is
        imported as "Name (2)". Imported prompts are always Custom."""
        path = Path(path)
        ext = path.suffix.lower()
        raw = path.read_text(encoding="utf-8-sig")
        if ext == ".json":
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("prompts"), list):
                records = data["prompts"]
            elif isinstance(data, list):
                records = data
            elif isinstance(data, dict):
                records = [data]
            else:
                raise ValueError("This file doesn't contain prompts.")
        elif ext in (".txt", ".md"):
            records = [{"name": path.stem.replace("_", " ").strip(), "text": raw}]
        else:
            raise ValueError(f"Unsupported file type: {ext or '(no extension)'} - "
                             "use a .json prompt export, or a .txt / .md file")
        res = PromptImportResult()
        have = {(p.name.strip().lower(), p.category.strip().lower(), p.text) for p in self.list()}
        for rec in records:
            if not isinstance(rec, dict):
                res.invalid += 1
                continue
            text = rec.get("text")
            if not isinstance(text, str) or not text.strip():
                res.invalid += 1
                continue
            text = text.replace("\r\n", "\n").replace("\r", "\n")     # line endings only
            name = str(rec.get("name") or "").strip() or "Imported prompt"
            category = str(rec.get("category") or "").strip() or "General"
            key = (name.lower(), category.lower(), text)
            if key in have:
                res.skipped.append(name)
                continue
            new_name = self.unique_name(name)
            if new_name != name:
                res.renamed.append((name, new_name))
            p = self.add(new_name, text, builtin=False, category=category)
            if rec.get("favorite") is True:
                p = self.set_favorite(p.id, True) or p
            have.add((new_name.lower(), category.lower(), text))
            res.imported.append(p)
        log.info("imported %d prompt(s) from %s (skipped %d, invalid %d)",
                 len(res.imported), path.name, len(res.skipped), res.invalid)
        return res

    def _write(self, p: SavedPrompt) -> None:
        # temp file + replace: a crash mid-save can't leave a half-written prompt
        atomic_write_text(self.dir / f"{p.id}.json",
                          json.dumps(p.to_dict(), indent=2, ensure_ascii=False))


@dataclass
class PromptImportResult:
    """What a prompt import did (shown to the user)."""
    imported: list = field(default_factory=list)        # SavedPrompt
    skipped: list = field(default_factory=list)         # names already in the library, identical
    renamed: list = field(default_factory=list)         # (old name, new name)
    invalid: int = 0                                    # records with no usable text

    def summary(self) -> str:
        parts = [f"Imported {len(self.imported)} prompt(s)"]
        if self.skipped:
            parts.append(f"skipped {len(self.skipped)} already in the library")
        if self.renamed:
            parts.append("renamed " + ", ".join(f'"{a}" to "{b}"' for a, b in self.renamed[:3]))
        if self.invalid:
            parts.append(f"{self.invalid} entr{'y' if self.invalid == 1 else 'ies'} "
                         "without prompt text ignored")
        return "; ".join(parts) + "."
