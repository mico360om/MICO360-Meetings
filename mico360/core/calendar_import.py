"""Minimal .ics (iCalendar) reader — pull meeting title, date/time and attendees
to pre-fill the minutes header (so they aren't left as 'Not specified')."""
from __future__ import annotations

import re
from pathlib import Path


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]          # RFC 5545 line folding
        else:
            lines.append(raw)
    return lines


def _name_from_attendee(value: str) -> str:
    m = re.search(r"CN=([^;:]+)", value, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    m = re.search(r"mailto:([^;\s]+)", value, re.IGNORECASE)
    if m:
        local = m.group(1).split("@")[0]
        return local.replace(".", " ").replace("_", " ").title()
    return value.strip()


def _fmt_dt(value: str) -> tuple[str, str]:
    """Return (date, time) from a DTSTART value like 20250714T100000Z."""
    v = value.split(":")[-1].strip()
    m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2}))?", v)
    if not m:
        return v, ""
    y, mo, d, hh, mm = m.groups()
    date = f"{y}-{mo}-{d}"
    time = f"{hh}:{mm}" if hh else ""
    return date, time


def parse_ics(path: str | Path) -> dict:
    """Return {title, date, time, attendees:[...]} from the first VEVENT."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = _unfold(text)
    title = organizer = ""
    date = time = ""
    attendees: list[str] = []
    in_event = False
    for line in lines:
        u = line.upper()
        if u.startswith("BEGIN:VEVENT"):
            in_event = True
            continue
        if u.startswith("END:VEVENT"):
            break                          # only the first event
        if not in_event:
            continue
        if u.startswith("SUMMARY"):
            title = line.split(":", 1)[-1].strip()
        elif u.startswith("DTSTART"):
            date, time = _fmt_dt(line)
        elif u.startswith("ATTENDEE"):
            n = _name_from_attendee(line)
            if n and n not in attendees:
                attendees.append(n)
        elif u.startswith("ORGANIZER"):
            organizer = _name_from_attendee(line)
    if organizer and organizer not in attendees:
        attendees.insert(0, organizer)
    when = date + (f" {time}" if time else "")
    return {"title": title, "date": when.strip(), "attendees": attendees}


def format_preamble(details: dict) -> str:
    """Build a 'known meeting details' block to guide the LLM (only real values)."""
    parts = []
    if details.get("title"):
        parts.append(f"Meeting Title: {details['title']}")
    if details.get("date"):
        parts.append(f"Date and Time: {details['date']}")
    att = details.get("attendees") or []
    if att:
        parts.append("Attendees: " + ", ".join(att))
    if not parts:
        return ""
    return ("Known meeting details (use these exactly in the minutes header):\n"
            + "\n".join(parts) + "\n\n")
