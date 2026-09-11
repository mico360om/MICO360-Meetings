"""Write action items with deadlines to an .ics file (RFC 5545) so they show
up in Outlook / Google / Apple Calendar. All-day events on the deadline, with
an optional reminder. Pure logic — no Qt, no network.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from . import tasks as T


def dated_items(items) -> list:
    """Items whose deadline parses to a real date (the ones we can schedule)."""
    return [it for it in items if T.parse_deadline(it.deadline) is not None]


def _esc(s: str) -> str:
    return (str(s or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 line folding: continuation lines start with a space."""
    out = []
    while len(line) > 73:
        out.append(line[:73]); line = " " + line[73:]
    out.append(line)
    return "\r\n".join(out)


def build_ics(items, today: date | None = None, reminder_days: int = 1,
              calname: str = "MICO360 Action Items") -> str:
    today = today or date.today()
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//MICO360//Meetings//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(calname)}"]
    for it in items:
        d = T.parse_deadline(it.deadline)
        if d is None:
            continue
        owner = (it.owner or "").strip()
        named = owner and owner.lower() not in T._PLACEHOLDER
        summary = f"[{owner}] {it.task}" if named else it.task
        desc = []
        if named:
            desc.append(f"Owner: {owner}")
        if it.meeting_title:
            desc.append(f"Meeting: {it.meeting_title}")
        if it.priority:
            desc.append(f"Priority: {it.priority}")
        desc.append(f"Status: {T.normalize_status(it.status)}")
        ev = ["BEGIN:VEVENT",
              f"UID:{uuid.uuid4().hex}@mico360",
              f"DTSTAMP:{stamp}",
              f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
              f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}",
              f"SUMMARY:{_esc(summary)}",
              "DESCRIPTION:" + "\\n".join(_esc(p) for p in desc),
              "TRANSP:TRANSPARENT"]
        if reminder_days:
            ev += ["BEGIN:VALARM", "ACTION:DISPLAY",
                   f"DESCRIPTION:{_esc('Reminder: ' + it.task)}",
                   f"TRIGGER:-P{reminder_days}D", "END:VALARM"]
        ev.append("END:VEVENT")
        lines += ev
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


def write_ics(path, items, **kw) -> int:
    """Write dated items to `path`; return how many events were written."""
    dated = dated_items(items)
    from pathlib import Path
    Path(path).write_text(build_ics(dated, **kw), encoding="utf-8")
    return len(dated)
