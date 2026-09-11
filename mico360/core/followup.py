"""Per-owner follow-up email drafts from open action items. Pure logic."""
from __future__ import annotations

from datetime import date

from . import tasks as T


def open_items(items):
    return [it for it in items
            if T.normalize_status(it.status) not in ("Completed", "Cancelled")]


def by_owner(items) -> dict:
    """Group OPEN items by responsible person (blank/placeholder owners skipped)."""
    groups: dict[str, list] = {}
    for it in open_items(items):
        o = (it.owner or "").strip()
        if not o or o.lower() in T._PLACEHOLDER:
            continue
        groups.setdefault(o, []).append(it)
    return dict(sorted(groups.items(), key=lambda kv: kv[0].lower()))


def _line(it, today) -> str:
    parts = [f"• {it.task}"]
    if (it.deadline or "").strip():
        overdue = " — OVERDUE" if T.is_overdue(it, today) else ""
        parts.append(f" (due {it.deadline.strip()}{overdue})")
    if it.priority:
        parts.append(f" [{it.priority}]")
    if it.meeting_title:
        parts.append(f" — from “{it.meeting_title}”")
    return "".join(parts)


def build_followup(owner: str, items, today: date | None = None) -> tuple[str, str]:
    """Return (subject, body) for one person's open action items."""
    today = today or date.today()
    n = len(items)
    subject = f"Follow-up: your {n} open action item{'s' if n != 1 else ''}"

    def sort_key(it):
        d = T.parse_deadline(it.deadline)
        return (0 if T.is_overdue(it, today) else 1, d or date.max)

    lines = [f"Hi {owner},", "",
             "Here are your open action items from our recent meetings:", ""]
    lines += [_line(it, today) for it in sorted(items, key=sort_key)]
    lines += ["", "Thanks,", "— Sent from MICO360 Meetings"]
    return subject, "\n".join(lines)


def build_all(items, today: date | None = None):
    """[(owner, subject, body, count)] for every owner with open items."""
    today = today or date.today()
    return [(owner, *build_followup(owner, its, today), len(its))
            for owner, its in by_owner(items).items()]
