"""Auto-record support: know when a meeting is happening so the app can offer
to record it. Two signals, both local and privacy-preserving:

* Live detection — a Teams / Google Meet / Zoom / Webex meeting *window* is
  open on this PC (title-based, via pywin32; no hooks into the apps).
* Calendar — upcoming meetings from an .ics file or the user's own Outlook
  calendar (COM), with the join link extracted from the invite.

Pure logic with injectable inputs so it is fully testable without a real call.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("mico360.meeting_watch")

# ---------------------------------------------------------------------------
# Live-meeting detection from window titles
# ---------------------------------------------------------------------------
# Teams' main window is titled "<Section> | Microsoft Teams"; a meeting/call is
# a separate window titled "<Meeting subject> | Microsoft Teams".
_TEAMS_SECTIONS = {"chat", "teams", "calendar", "calls", "activity", "apps", "files",
                   "onedrive", "copilot", "microsoft teams", "notifications", "settings"}
_MEET_RE = re.compile(r"^Meet\s*[-–—]\s*(.+)$")
_MEET_CODE_RE = re.compile(r"\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b")


def classify_window(title: str):
    """Return (app, meeting_title) if this window title is a live meeting, else None."""
    t = (title or "").strip()
    if not t:
        return None
    if " | Microsoft Teams" in t or t.endswith("Microsoft Teams"):
        prefix = t.split(" | Microsoft Teams")[0].strip() if " | Microsoft Teams" in t else ""
        if prefix and prefix.lower() not in _TEAMS_SECTIONS:
            return ("Teams", prefix)
        return None
    m = _MEET_RE.match(t)
    if m or "Google Meet" in t:
        rest = (m.group(1) if m else t.replace("- Google Meet", "").replace("Google Meet", "")).strip(" -–—")
        if rest and (m or _MEET_CODE_RE.search(t)):
            return ("Google Meet", rest)
        return None
    if "Zoom Meeting" in t or "Zoom Webinar" in t:
        return ("Zoom", t)
    if "Webex" in t and re.search(r"meeting|call|\d", t, re.IGNORECASE):
        return ("Webex", t)
    return None


def detect_live_meeting(titles=None):
    """First live meeting among `titles` (or the current visible windows)."""
    if titles is None:
        titles = list_window_titles()
    for t in titles:
        hit = classify_window(t)
        if hit:
            return hit
    return None


def list_window_titles() -> list[str]:
    """Visible top-level window titles on Windows; [] elsewhere or on error."""
    try:
        import win32gui
    except Exception:
        return []
    out: list[str] = []

    def cb(h, _):
        try:
            if win32gui.IsWindowVisible(h):
                t = win32gui.GetWindowText(h)
                if t:
                    out.append(t)
        except Exception:
            pass
    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        log.debug("window enumeration failed", exc_info=True)
    return out


# ---------------------------------------------------------------------------
# Calendar sources
# ---------------------------------------------------------------------------
@dataclass
class MeetingInfo:
    title: str
    start: datetime          # local, naive
    end: datetime | None
    join_url: str = ""
    source: str = ""         # "ics" | "outlook"

    def key(self) -> str:
        return f"{self.title}|{self.start.isoformat(timespec='minutes')}"


_JOIN_RE = re.compile(
    r"https?://(?:"
    r"teams\.microsoft\.com/l/meetup-join/[^\s<>\"']+"
    r"|teams\.live\.com/meet/[^\s<>\"']+"
    r"|meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}[^\s<>\"']*"
    r"|[\w.-]*zoom\.us/j/\d+[^\s<>\"']*"
    r"|[\w.-]*webex\.com/[^\s<>\"']+"
    r")", re.IGNORECASE)


def extract_join_url(text: str) -> str:
    """First Teams / Meet / Zoom / Webex join link in free text (ICS-unescaped)."""
    s = (text or "").replace("\\,", ",").replace("\\;", ";").replace("\\n", "\n")
    m = _JOIN_RE.search(s)
    return m.group(0).rstrip(".,;)") if m else ""


def _parse_ics_dt(value: str) -> datetime | None:
    v = value.split(":")[-1].strip()
    m = re.match(r"(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?)?(Z?)", v)
    if not m:
        return None
    y, mo, d, hh, mi, ss, z = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0))
    if z:                                   # UTC -> local naive
        dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    return dt


def upcoming_from_ics_text(text: str, now: datetime | None = None,
                           horizon_hours: float = 24) -> list[MeetingInfo]:
    """All VEVENTs starting from `now` (minus a little slack) within `horizon_hours`."""
    from .calendar_import import _unfold
    now = now or datetime.now()
    lines = _unfold(text)
    events: list[MeetingInfo] = []
    cur: dict | None = None
    for line in lines:
        u = line.upper()
        if u.startswith("BEGIN:VEVENT"):
            cur = {"title": "", "start": None, "end": None, "blob": ""}
        elif u.startswith("END:VEVENT") and cur is not None:
            if cur["start"]:
                events.append(MeetingInfo(cur["title"], cur["start"], cur["end"],
                                          extract_join_url(cur["blob"]), "ics"))
            cur = None
        elif cur is not None:
            if u.startswith("SUMMARY"):
                cur["title"] = line.split(":", 1)[-1].strip()
            elif u.startswith("DTSTART"):
                cur["start"] = _parse_ics_dt(line)
            elif u.startswith("DTEND"):
                cur["end"] = _parse_ics_dt(line)
            elif u.startswith(("DESCRIPTION", "LOCATION", "X-MICROSOFT-SKYPETEAMSMEETINGURL",
                               "X-GOOGLE-CONFERENCE", "URL")):
                cur["blob"] += line + "\n"
    lo, hi = now - timedelta(minutes=5), now + timedelta(hours=horizon_hours)
    return sorted((e for e in events if lo <= e.start <= hi), key=lambda e: e.start)


def upcoming_from_ics(path: str | Path, **kw) -> list[MeetingInfo]:
    try:
        return upcoming_from_ics_text(Path(path).read_text(encoding="utf-8", errors="replace"), **kw)
    except Exception:
        log.warning("could not read calendar file %s", path, exc_info=True)
        return []


def upcoming_from_outlook(horizon_hours: float = 24) -> list[MeetingInfo]:
    """Upcoming appointments from the user's Outlook calendar via COM (Windows,
    Outlook installed). Best-effort: returns [] on any failure."""
    try:
        import pythoncom                             # type: ignore
        import win32com.client                       # type: ignore
        pythoncom.CoInitialize()                     # called from a worker thread
        ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        items = ns.GetDefaultFolder(9).Items          # 9 = olFolderCalendar
        items.IncludeRecurrences = True
        items.Sort("[Start]")
        now = datetime.now()
        hi = now + timedelta(hours=horizon_hours)
        fmt = "%m/%d/%Y %I:%M %p"
        items = items.Restrict(f"[Start] >= '{(now - timedelta(minutes=5)).strftime(fmt)}' "
                               f"AND [Start] <= '{hi.strftime(fmt)}'")
        out: list[MeetingInfo] = []
        for it in items:
            try:
                start = datetime(it.Start.year, it.Start.month, it.Start.day,
                                 it.Start.hour, it.Start.minute)
                end = datetime(it.End.year, it.End.month, it.End.day, it.End.hour, it.End.minute)
                blob = " ".join(str(x) for x in (getattr(it, "Location", ""), getattr(it, "Body", "")))
                out.append(MeetingInfo(str(it.Subject or "Meeting"), start, end,
                                       extract_join_url(blob), "outlook"))
            except Exception:
                continue
        return sorted(out, key=lambda e: e.start)
    except Exception:
        log.debug("Outlook calendar unavailable", exc_info=True)
        return []


def next_due(meetings, now: datetime | None = None, lead_minutes: float = 3) -> MeetingInfo | None:
    """The meeting to prompt for now: starts within `lead_minutes` (or started up
    to 2 min ago) and hasn't ended. Earliest wins."""
    now = now or datetime.now()
    lo, hi = now - timedelta(minutes=2), now + timedelta(minutes=lead_minutes)
    due = [m for m in meetings if lo <= m.start <= hi and (m.end is None or m.end > now)]
    return min(due, key=lambda m: m.start) if due else None
