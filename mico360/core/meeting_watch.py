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
import sys
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
                   "onedrive", "copilot", "microsoft teams", "notifications", "settings",
                   "search", "people", "communities", "community", "assignments", "planner",
                   "shifts", "approvals", "home", "meet", "help", "viva insights", "workflows"}
# Parts of a Teams meeting-window title that aren't the meeting's name.
_TEAMS_MEETING_CHROME = {"meeting compact view", "meeting controls", "meeting", "call"}
_MEET_RE = re.compile(r"^Meet\s*[-–—]\s*(.+)$")
_MEET_CODE_RE = re.compile(r"\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b")

# Browser window titles end with "<page title> - <browser>". Edge inserts a
# zero-width space in "Microsoft​ Edge" and may put the profile name before it
# ("<page> - Profile 1 - Microsoft Edge", "<page> and 3 more pages - Personal -
# Microsoft Edge"). K1: only the browser suffix is generic — stripping "any
# hyphen-free segment" as a profile ate the name of a named Meet call
# ("Meet – Weekly Sync - Google Chrome" became the idle page "Meet").
_BROWSERS = ("Google Chrome", "Microsoft Edge", "Mozilla Firefox", "Firefox", "Brave",
             "Opera", "Vivaldi", "Chromium", "Arc", "Yandex")
_BROWSER_SUFFIX_RE = re.compile(
    r"\s+[-–—]\s+(" + "|".join(re.escape(b) for b in _BROWSERS) + r")\s*$")
# Edge only: a trailing "- <profile>" segment, and the multi-tab marker.
_EDGE_SEGMENT_RE = re.compile(r"\s+[-–—]\s+([^-–—]+?)\s*$")
_EDGE_MORE_PAGES_RE = re.compile(r"\s+and\s+\d+\s+more\s+pages?\s*$", re.IGNORECASE)
# Edge's own profile names (a custom-named profile is only recognised when the
# title also carries the "and N more pages" marker, which Edge only shows in
# front of the profile segment).
_EDGE_PROFILE_RE = re.compile(
    r"^(?:profile\s*\d+|personal|work|school|default|family|guest(?:\s+profile)?|inprivate"
    r"|default\s+profile|microsoft\s+account)$", re.IGNORECASE)


def _strip_edge_profile(page: str) -> str:
    m = _EDGE_SEGMENT_RE.search(page)
    if m:
        head = page[:m.start()].strip()
        if head and (_EDGE_PROFILE_RE.match(m.group(1).strip())
                     or _EDGE_MORE_PAGES_RE.search(head)):
            page = head
    return _EDGE_MORE_PAGES_RE.sub("", page).strip()


def split_browser(title: str) -> tuple[str, str | None]:
    """('<page title>', '<browser>') for a browser window, else (title, None)."""
    t = (title or "").replace("​", "").strip()
    m = _BROWSER_SUFFIX_RE.search(t)
    if not m:
        return t, None
    page, browser = t[:m.start()].strip(), m.group(1)
    if browser == "Microsoft Edge":
        page = _strip_edge_profile(page)
    return page, browser


def _classify_teams(t: str):
    if " | Microsoft Teams" not in t:
        return None                                        # bare "Microsoft Teams"
    prefix = t.rsplit(" | Microsoft Teams", 1)[0].strip()
    parts = [p.strip() for p in prefix.split(" | ") if p.strip()]
    if not parts:
        return None
    # M36: "Chat | Jane Doe | Microsoft Teams", "Calendar | Calendar | Microsoft
    # Teams" … are the main window showing a section, not a meeting.
    if any(p.lower() in _TEAMS_SECTIONS for p in parts):
        return None
    named = [p for p in parts if p.lower() not in _TEAMS_MEETING_CHROME]
    return ("Teams", " | ".join(named or parts))


def classify_window(title: str):
    """Return (app, meeting_title) if this window title is a live meeting, else None."""
    t, _browser = split_browser(title)
    if not t:
        return None
    if " | Microsoft Teams" in t or t.endswith("Microsoft Teams"):
        return _classify_teams(t)
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


@dataclass
class LiveMeeting:
    app: str
    title: str
    window_title: str
    browser: str | None          # browser name when the meeting runs in a browser tab

    @property
    def in_browser(self) -> bool:
        # Google Meet only ever runs in a browser (or a browser-hosted PWA window).
        return self.browser is not None or self.app == "Google Meet"


def find_live_meeting(titles=None) -> LiveMeeting | None:
    """Like detect_live_meeting, but also says whether it runs in a browser tab."""
    if titles is None:
        titles = list_window_titles()
    for t in titles:
        hit = classify_window(t)
        if hit:
            return LiveMeeting(hit[0], hit[1], t, split_browser(t)[1])
    return None


def browser_window_open(titles, browser: str | None) -> bool:
    """Whether any window of `browser` is still open among `titles`."""
    if not browser:
        return True                                        # unknown: assume still open
    return any(split_browser(t)[1] == browser for t in titles)


_MEET_IDLE_PAGES = {"meet", "google meet"}


def meet_left(titles) -> bool:
    """A browser tab shows Google Meet's home page (what Meet shows after you
    leave a call) — the meeting is over, not merely in a background tab."""
    for t in titles or ():
        page, browser = split_browser(t)
        if browser and page.lower() in _MEET_IDLE_PAGES:
            return True
    return False


def detect_live_meeting(titles=None):
    """First live meeting among `titles` (or the current visible windows)."""
    hit = find_live_meeting(titles)
    return (hit.app, hit.title) if hit else None


def detection_available() -> bool:
    """True if live-meeting detection can work (needs pywin32 on Windows)."""
    try:
        import win32gui  # noqa: F401
        return True
    except Exception:
        return False


_warned_no_win32 = False


def list_window_titles() -> list[str]:
    """Visible top-level window titles on Windows; [] elsewhere or on error."""
    global _warned_no_win32
    try:
        import win32gui
    except Exception:
        if sys.platform == "win32" and not _warned_no_win32:
            _warned_no_win32 = True
            log.warning("pywin32 is not installed — live-meeting detection is off")
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


def _locale_datetime_str(dt: datetime) -> str:
    """`dt` in the user's short-date + short-time format — the format Outlook's
    Items.Restrict parses — e.g. '26/09/2026 14:05' on en-GB, '9/26/2026 2:05 PM'
    on en-US. Falls back to the US format off Windows or on error."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class SYSTEMTIME(ctypes.Structure):
                _fields_ = [(n, wintypes.WORD) for n in (
                    "wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute",
                    "wSecond", "wMilliseconds")]
            st = SYSTEMTIME(dt.year, dt.month, 0, dt.day, dt.hour, dt.minute, 0, 0)
            k32 = ctypes.windll.kernel32
            DATE_SHORTDATE, TIME_NOSECONDS = 0x1, 0x2
            dbuf = ctypes.create_unicode_buffer(128)
            tbuf = ctypes.create_unicode_buffer(128)
            if (k32.GetDateFormatEx(None, DATE_SHORTDATE, ctypes.byref(st), None, dbuf, 128, None)
                    and k32.GetTimeFormatEx(None, TIME_NOSECONDS, ctypes.byref(st), None, tbuf, 128)):
                return f"{dbuf.value} {tbuf.value}"
        except Exception:
            log.debug("locale date formatting failed", exc_info=True)
    return dt.strftime("%m/%d/%Y %I:%M %p")


def outlook_restrict_filter(lo: datetime, hi: datetime, fmt=None) -> str:
    """Jet filter for appointments starting in [lo, hi], in the locale format (M35)."""
    fmt = fmt or _locale_datetime_str
    return f"[Start] >= '{fmt(lo)}' AND [Start] <= '{fmt(hi)}'"


def _outlook_app(client):
    """The RUNNING Outlook instance, or None. Never launches Outlook (M35: a
    Dispatch() on every poll started Outlook when the user had closed it)."""
    try:
        return client.GetActiveObject("Outlook.Application")
    except Exception:
        return None


def upcoming_from_outlook(horizon_hours: float = 24, now: datetime | None = None,
                          _client=None, _com=None) -> list[MeetingInfo]:
    """Upcoming appointments from the user's Outlook calendar via COM (Windows,
    Outlook running). Best-effort: returns [] on any failure. `_client`/`_com`
    stand in for win32com.client / pythoncom in tests."""
    try:
        if _client is None:
            import win32com.client as _client        # type: ignore
        if _com is None:
            import pythoncom as _com                  # type: ignore
        _com.CoInitialize()                           # called from a worker thread
        app = _outlook_app(_client)
        if app is None:
            return []
        ns = app.GetNamespace("MAPI")
        items = ns.GetDefaultFolder(9).Items          # 9 = olFolderCalendar
        items.Sort("[Start]")                         # Sort BEFORE IncludeRecurrences (MS docs)
        items.IncludeRecurrences = True
        now = now or datetime.now()
        lo, hi = now - timedelta(minutes=5), now + timedelta(hours=horizon_hours)
        items = items.Restrict(outlook_restrict_filter(lo, hi))
        out: list[MeetingInfo] = []
        for n, it in enumerate(items):
            if n >= 500:                              # a runaway recurring series
                break
            try:
                start = datetime(it.Start.year, it.Start.month, it.Start.day,
                                 it.Start.hour, it.Start.minute)
                end = datetime(it.End.year, it.End.month, it.End.day, it.End.hour, it.End.minute)
                # Belt and braces: never trust the filter's date parsing alone.
                if not (lo <= start <= hi):
                    continue
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
