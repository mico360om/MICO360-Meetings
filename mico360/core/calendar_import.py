"""Minimal .ics (iCalendar) reader — pull meeting title, date/time and attendees
to pre-fill the minutes header (so they aren't left as 'Not specified')."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]          # RFC 5545 line folding
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    """RFC 5545 TEXT unescaping: \\, \\; \\n \\N \\\\ (one pass, left to right)."""
    return re.sub(r"\\([\\,;nN])",
                  lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def _split_prop(line: str) -> tuple[str, dict, str]:
    """Split 'NAME;P1=a;P2="b:c":value' into (NAME, {P1: a, P2: b:c}, value).
    Colons/semicolons inside quoted parameter values are respected."""
    in_q = False
    parts: list[str] = []
    buf = []
    value = ""
    for i, ch in enumerate(line):
        if ch == '"':
            in_q = not in_q
            buf.append(ch)
        elif ch == ";" and not in_q:
            parts.append("".join(buf)); buf = []
        elif ch == ":" and not in_q:
            parts.append("".join(buf))
            value = line[i + 1:]
            break
        else:
            buf.append(ch)
    else:
        parts.append("".join(buf))
    name = (parts[0] if parts else "").strip().upper()
    params: dict[str, str] = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.strip().upper()] = v.strip().strip('"')
    return name, params, value


def _name_from_attendee(value: str) -> str:
    m = re.search(r'CN="([^"]*)"', value, re.IGNORECASE) or \
        re.search(r"CN=([^;:]+)", value, re.IGNORECASE)
    if m and m.group(1).strip():
        return _unescape(m.group(1).strip().strip('"'))
    m = re.search(r"mailto:([^;\s]+)", value, re.IGNORECASE)
    if m:
        local = m.group(1).split("@")[0]
        return local.replace(".", " ").replace("_", " ").title()
    return value.strip()


def _fmt_dt(value: str, tzid: str = "") -> tuple[str, str]:
    """Return (date, time) in LOCAL time from a DTSTART value.

    '20250714T100000Z' is UTC and is converted to the computer's local time;
    a TZID the system knows is converted too; a floating time (no Z/TZID) is
    shown as written."""
    v = value.split(":")[-1].strip()
    m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?", v)
    if not m:
        return v, ""
    y, mo, d, hh, mm, ss, z = m.groups()
    if not hh:
        return f"{y}-{mo}-{d}", ""
    try:
        dt = datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0))
        tz = None
        if z:
            tz = timezone.utc
        elif tzid:
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tzid)
            except Exception:
                tz = None                  # e.g. a Windows zone name — keep as written
        if tz is not None:
            dt = dt.replace(tzinfo=tz).astimezone()     # → local time
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except (ValueError, OverflowError, OSError):
        return f"{y}-{mo}-{d}", f"{hh}:{mm}"


def parse_ics(path: str | Path) -> dict:
    """Return {title, date, time, attendees:[...]} from the first VEVENT.

    Only the VEVENT's OWN properties are read: nested components such as a
    VALARM (whose SUMMARY/ATTENDEE describe the reminder, not the meeting) are
    skipped."""
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    lines = _unfold(text)
    title = organizer = ""
    date = time = ""
    attendees: list[str] = []
    stack: list[str] = []                  # open components, innermost last
    for line in lines:
        if not line.strip():
            continue
        name, params, value = _split_prop(line)
        if name == "BEGIN":
            stack.append(value.strip().upper())
            continue
        if name == "END":
            comp = value.strip().upper()
            if comp == "VEVENT" and "VEVENT" in stack:
                break                      # only the first event
            if stack and stack[-1] == comp:
                stack.pop()
            continue
        if not stack or stack[-1] != "VEVENT":
            continue                       # outside an event, or inside VALARM etc.
        if name == "SUMMARY":
            title = _unescape(value).strip()
        elif name == "DTSTART":
            date, time = _fmt_dt(value, params.get("TZID", ""))
        elif name == "ATTENDEE":
            n = _name_from_attendee(line)
            if n and n not in attendees:
                attendees.append(n)
        elif name == "ORGANIZER":
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
