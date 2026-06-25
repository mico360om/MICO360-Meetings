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
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ..config import PROMPTS_DIR

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


def build_reduce_prompt(template: str, style: str, partial_notes: str) -> str:
    style_hint = OUTPUT_STYLES.get(style, "")
    intro = (template.split("Transcript:")[0].strip()
             if "Transcript:" in template else template.strip())
    return (
        f"{intro}\n\n{MINUTES_STRUCTURE}\n"
        f"Style instruction: {style_hint}\n\n"
        "Below are factual notes extracted from consecutive parts of one meeting. "
        "Merge them into a single, coherent set of minutes. Do not add anything not "
        "present in the notes.\n\nNotes:\n" + partial_notes
    )


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
        return SavedPrompt(**{k: v for k, v in d.items() if k in valid})

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

    def _write(self, p: SavedPrompt) -> None:
        (self.dir / f"{p.id}.json").write_text(
            json.dumps(p.to_dict(), indent=2), encoding="utf-8"
        )
