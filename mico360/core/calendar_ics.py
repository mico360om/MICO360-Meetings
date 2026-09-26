"""Write action items with deadlines to an .ics file (RFC 5545) so they show
up in Outlook / Google / Apple Calendar. All-day events on the deadline, with
an optional reminder. Pure logic — no Qt, no network.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from . import tasks as T

# RFC 5545 §3.1: lines SHOULD NOT be longer than 75 OCTETS (excluding CRLF).
_MAX_OCTETS = 75


def dated_items(items) -> list:
    """Items whose deadline parses to a real date (the ones we can schedule)."""
    return [it for it in items if T.deadline_date(it) is not None]


def _esc(s: str) -> str:
    return (str(s or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")
            .replace("\r", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 line folding by OCTETS (UTF-8), never splitting a character:
    the first line holds at most 75 octets, each continuation line a leading
    space plus at most 74. (Folding by character count produced 128-octet
    Arabic lines.)"""
    out: list[str] = []
    cur: list[str] = []
    size = 0
    limit = _MAX_OCTETS
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > limit:
            out.append("".join(cur))
            cur, size, limit = [" "], 1, _MAX_OCTETS    # continuation: space + 74
        cur.append(ch)
        size += n
    out.append("".join(cur))
    return "\r\n".join(out)


def event_uid(item) -> str:
    """A UID that is STABLE across exports for the same action item, so
    re-importing an updated .ics updates the event instead of duplicating it.
    Based on the item's override key (meeting + original task text + row)."""
    key = getattr(item, "okey", "") or item.key()
    return hashlib.sha1(f"mico360-action:{key}".encode("utf-8")).hexdigest()[:24] + "@mico360"


def build_ics(items, today: date | None = None, reminder_days: int = 1,
              calname: str = "MICO360 Action Items") -> str:
    today = today or date.today()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//MICO360//Meetings//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(calname)}"]
    seen: set[str] = set()
    for it in items:
        d = T.deadline_date(it)
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
        uid = event_uid(it)
        n = 2
        while uid in seen:                       # two identical ad-hoc items
            uid = event_uid(it).replace("@", f"-{n}@"); n += 1
        seen.add(uid)
        ev = ["BEGIN:VEVENT",
              f"UID:{uid}",
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
    # Bytes, not text mode: build_ics already uses CRLF, and Windows text mode
    # would turn each into "\r\r\n" (breaks folded lines / RFC 5545).
    Path(path).write_bytes(build_ics(dated, **kw).encode("utf-8"))
    return len(dated)
