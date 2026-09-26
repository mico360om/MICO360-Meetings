"""Action-item extraction and cross-meeting tracking.

Parses the "Action Items" table out of generated minutes, aggregates items
across all meetings in history, and lets the user override an item's status
(persisted separately so the original minutes are never modified).
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ..config import DATA_DIR, atomic_write_text, quarantine_bad_file
from ..export import md_blocks

log = logging.getLogger("mico360.tasks")

STATUS_FILE = DATA_DIR / "action_status.json"
# The settable workflow statuses. "Overdue" is NOT here — it is a derived state
# (a past deadline on a still-open task), computed by is_overdue().
STATUS_CYCLE = ["Pending", "In Progress", "Completed", "Cancelled"]
OVERDUE = "Overdue"
_CLOSED = {"Completed", "Cancelled"}
_PLACEHOLDER = {"", "...", "-", "—", "…", "n/a", "na", "not specified", "tbd"}

# Map the many ways minutes phrase a status onto our four canonical ones.
_STATUS_SYNONYMS = {
    "done": "Completed", "complete": "Completed", "completed": "Completed",
    "closed": "Completed", "resolved": "Completed", "finished": "Completed",
    "cancelled": "Cancelled", "canceled": "Cancelled", "dropped": "Cancelled",
    "in progress": "In Progress", "in-progress": "In Progress", "wip": "In Progress",
    "ongoing": "In Progress", "doing": "In Progress", "started": "In Progress",
    "pending": "Pending", "open": "Pending", "to do": "Pending", "todo": "Pending",
    "to-do": "Pending", "not started": "Pending", "new": "Pending", "": "Pending",
}

# Deadline strings in minutes vary wildly; try the common explicit-date shapes.
_DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
                 "%d.%m.%Y", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
                 "%b %d %Y", "%B %d %Y", "%d %b, %Y", "%d %B, %Y"]
# Year-less shapes ("10 Jan", "Oct 5"). They are parsed against a leap year so
# "29 Feb" is valid, then the real year is chosen relative to a reference date.
_YEARLESS_FORMATS = ["%d %b", "%d %B", "%b %d", "%B %d"]
_ORDINAL_RE = re.compile(r"\b(\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)
# A year-less date more than this far before the reference is taken to mean the
# NEXT year ("10 Jan" written in a meeting on 20 Dec is next January).
_YEARLESS_PAST_GRACE = timedelta(days=90)


def normalize_status(status: str) -> str:
    """Fold a free-text status onto a canonical STATUS_CYCLE value (unknown
    values are kept, title-preserved, so nothing is silently lost)."""
    s = (status or "").strip()
    return _STATUS_SYNONYMS.get(s.lower(), s or "Pending")


def _resolve_year(month: int, day: int, ref: date) -> date | None:
    """Pick the year for a year-less month/day: the reference year, or the next
    one when that would put the date well before the reference. 29 Feb moves to
    the next leap year."""
    year = ref.year
    try:
        cand = date(year, month, day)
    except ValueError:
        cand = None
    if cand is None or cand < ref - _YEARLESS_PAST_GRACE:
        year += 1
        cand = None
    for y in range(year, year + 8):
        try:
            return date(y, month, day)
        except ValueError:
            continue
    return None


def parse_deadline(text: str, ref: date | None = None) -> date | None:
    """Best-effort parse of a free-text deadline into a date, or None.

    `ref` is the date the deadline was written (the meeting date) and is used
    only for year-less deadlines; it defaults to today."""
    s = (text or "").strip()
    if not s or s.lower() in _PLACEHOLDER:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        pass
    s = _ORDINAL_RE.sub(r"\1", s).strip().rstrip(".")
    s = re.sub(r"\s+", " ", s)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            continue
    for fmt in _YEARLESS_FORMATS:
        try:
            # parse with an explicit leap year (no 1900 default → 29 Feb works,
            # and no Python 3.13+ "no year" DeprecationWarning)
            d = datetime.strptime(f"{s} 2000", fmt + " %Y").date()
        except Exception:
            continue
        return _resolve_year(d.month, d.day, ref or date.today())
    return None


def _ref_date(item: "ActionItem") -> date | None:
    """The date an item was written (its meeting date), if known."""
    try:
        return date.fromisoformat((item.meeting_date or "")[:10])
    except Exception:
        return None


def deadline_date(item: "ActionItem") -> date | None:
    """An item's deadline as a date, resolving year-less deadlines against the
    date of the meeting that set them."""
    return parse_deadline(item.deadline, _ref_date(item))


def is_overdue(item: "ActionItem", today: date | None = None) -> bool:
    """True when a task has a parseable past deadline and is still open."""
    if normalize_status(item.status) in _CLOSED:
        return False
    d = deadline_date(item)
    return d is not None and d < (today or date.today())


def effective_status(item: "ActionItem", today: date | None = None) -> str:
    """The status to display/filter on: OVERDUE overrides an open task."""
    return OVERDUE if is_overdue(item, today) else normalize_status(item.status)


def reminder_counts(items, today: date | None = None, within_days: int = 7):
    """(overdue, due_soon) counts across open items — for the startup digest."""
    today = today or date.today()
    overdue = due_soon = 0
    for it in items:
        if normalize_status(it.status) in _CLOSED:
            continue
        if is_overdue(it, today):
            overdue += 1
            continue
        d = deadline_date(it)
        if d is not None and today <= d <= today + timedelta(days=within_days):
            due_soon += 1
    return overdue, due_soon


PRIORITIES = ["High", "Medium", "Low"]
_PRIORITY_SYNONYMS = {
    "high": "High", "urgent": "High", "critical": "High", "h": "High", "p1": "High",
    "medium": "Medium", "med": "Medium", "normal": "Medium", "m": "Medium", "p2": "Medium",
    "low": "Low", "l": "Low", "minor": "Low", "p3": "Low",
}


def normalize_priority(value: str) -> str:
    """Fold a free-text priority onto High/Medium/Low, or '' when unset."""
    s = (value or "").strip()
    if not s or s.lower() in _PLACEHOLDER:
        return ""
    return _PRIORITY_SYNONYMS.get(s.lower(), s)


@dataclass
class ActionItem:
    task: str
    owner: str
    deadline: str
    status: str
    meeting_id: int = 0
    meeting_title: str = ""
    meeting_date: str = ""
    priority: str = ""
    notes: str = ""
    okey: str = ""          # stable override key (from the ORIGINAL task text)
    occ: int = 0            # n-th row with this same task text in its meeting

    def _task_hash(self) -> str:
        return hashlib.md5(self.task.strip().lower().encode("utf-8")).hexdigest()[:10]

    def legacy_key(self) -> str:
        """The pre-1.2.3 override key (meeting + task text only). Two rows with the
        same task text in one meeting shared it — see key()."""
        return f"{self.meeting_id}:{self._task_hash()}"

    def key(self) -> str:
        """Override key, unique per row: meeting + task text + which occurrence of
        that text it is within the meeting. (Alice's and Bob's identical "Send
        report" rows no longer share one saved status/edit.)"""
        return f"{self.meeting_id}:{self._task_hash()}:{self.occ}"


def _clean(v: str) -> str:
    return (v or "").replace("**", "").strip()


def extract_action_items(minutes_md: str) -> list[ActionItem]:
    """Return the action items found in a minutes Markdown string."""
    items: list[ActionItem] = []
    for blk in md_blocks.parse(minutes_md or ""):
        if blk.kind != "table" or not blk.headers:
            continue
        hdr = [h.lower() for h in blk.headers]
        if not any("task" in h or "action" in h for h in hdr):
            continue

        def col(keys):
            for i, h in enumerate(hdr):
                if any(k in h for k in keys):
                    return i
            return -1

        ci_task = col(["task", "action", "item"])
        ci_owner = col(["responsible", "person", "owner", "assign", "who"])
        ci_due = col(["deadline", "due", "date", "by when", "when"])
        ci_status = col(["status", "state"])
        ci_prio = col(["priority", "importance", "urgency"])
        for row in blk.rows:
            def get(ci):
                return _clean(row[ci]) if 0 <= ci < len(row) else ""
            task = get(ci_task)
            if not task or task.lower() in _PLACEHOLDER:
                continue
            items.append(ActionItem(
                task=task, owner=get(ci_owner), deadline=get(ci_due),
                status=get(ci_status) or "Pending",
                priority=normalize_priority(get(ci_prio))))
    return items


class ActionItemStore:
    """Aggregates action items across history + persists status overrides."""

    def __init__(self, history, status_file: Path = STATUS_FILE):
        self.history = history
        self.status_file = status_file
        self._overrides = self._load()

    def _load(self) -> dict:
        try:
            if self.status_file.exists():
                data = json.loads(self.status_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("action status file is not a JSON object")
                return data
        except Exception:
            log.warning("could not load action status overrides", exc_info=True)
            # keep the unreadable file so the next save can't wipe every edit
            quarantine_bad_file(self.status_file)
        return {}

    def _save(self):
        try:
            atomic_write_text(self.status_file, json.dumps(self._overrides, indent=2))
        except Exception:
            log.warning("could not save action status overrides", exc_info=True)

    def _migrate_legacy(self, items: list[ActionItem]) -> bool:
        """Move overrides saved under the old per-text key onto the new per-row
        keys. Unambiguous (one row with that text) → moved to that row. Several
        rows shared the old key (and so all showed the edit) → each row gets its
        own copy, after which they are edited independently. Returns True if
        anything changed."""
        groups: dict[str, list[ActionItem]] = {}
        for it in items:
            groups.setdefault(it.legacy_key(), []).append(it)
        changed = False
        for old, rows in groups.items():
            if old not in self._overrides:
                continue
            ov = self._overrides.pop(old)
            changed = True
            for it in rows:
                if it.okey not in self._overrides:
                    self._overrides[it.okey] = dict(ov) if isinstance(ov, dict) else ov
        return changed

    # Fields a user may override per action item (persisted in the overrides file).
    _EDITABLE = ("task", "owner", "deadline", "priority", "status", "notes")

    def _apply_override(self, item: ActionItem) -> None:
        """Overlay any saved edits onto a freshly-extracted item. Supports the
        legacy shape where an override was just a status string."""
        ov = self._overrides.get(item.okey)
        if not ov:
            return
        if isinstance(ov, str):          # legacy: status-only override
            item.status = ov
            return
        for f in self._EDITABLE:
            if f in ov and ov[f] is not None:
                setattr(item, f, ov[f])

    def all_items(self, search: str = "") -> list[ActionItem]:
        import time
        out: list[ActionItem] = []
        migrated = False
        for m in all_meetings(self.history):
            # the meeting date is when it was HELD (created), not last edited
            ts = getattr(m, "created_at", 0) or getattr(m, "updated_at", 0) or 0
            items = extract_action_items(m.minutes)
            seen: dict[str, int] = {}
            for it in items:
                it.meeting_id = m.id
                it.meeting_title = m.title
                it.meeting_date = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
                h = it.legacy_key()
                it.occ = seen.get(h, 0); seen[h] = it.occ + 1
                it.okey = it.key()                    # stable per-row key (ORIGINAL task)
            migrated |= self._migrate_legacy(items)
            for it in items:
                self._apply_override(it)
                it.status = normalize_status(it.status)     # fold "Done" etc. -> canonical
                it.priority = normalize_priority(it.priority)
                out.append(it)
        if migrated:
            self._save()
        if search.strip():
            q = search.lower()
            out = [i for i in out if q in i.task.lower() or q in i.owner.lower()
                   or q in i.meeting_title.lower() or q in i.status.lower()
                   or q in (i.notes or "").lower()]
        return out

    def _override_dict(self, item: ActionItem) -> dict:
        ov = self._overrides.get(item.okey or item.key())
        if isinstance(ov, dict):
            return dict(ov)
        if isinstance(ov, str):
            return {"status": ov}
        return {}

    def set_status(self, item: ActionItem, status: str):
        key = item.okey or item.key()
        ov = self._override_dict(item)
        ov["status"] = status
        self._overrides[key] = ov
        item.status = status
        self._save()

    def update_item(self, item: ActionItem, *, task: str, owner: str, deadline: str,
                    priority: str, status: str, notes: str) -> None:
        """Persist a full set of edits for an action item (no minutes are touched)."""
        key = item.okey or item.key()
        self._overrides[key] = {
            "task": task.strip(), "owner": owner.strip(), "deadline": deadline.strip(),
            "priority": normalize_priority(priority), "status": status.strip(),
            "notes": notes.strip(),
        }
        (item.task, item.owner, item.deadline, item.priority, item.status, item.notes) = (
            task.strip(), owner.strip(), deadline.strip(),
            normalize_priority(priority), status.strip(), notes.strip())
        self._save()

    def reset_item(self, item: ActionItem) -> None:
        """Drop all saved edits for an item, reverting to the minutes' values."""
        key = item.okey or item.key()
        if key in self._overrides:
            del self._overrides[key]
            self._save()

    def set_priority(self, item: ActionItem, priority: str):
        key = item.okey or item.key()
        ov = self._override_dict(item)
        ov["priority"] = normalize_priority(priority)
        self._overrides[key] = ov
        item.priority = normalize_priority(priority)
        self._save()

    def drop_meeting(self, meeting_id: int) -> None:
        """Remove any status overrides belonging to a deleted meeting."""
        prefix = f"{meeting_id}:"
        removed = [k for k in self._overrides if k.startswith(prefix)]
        for k in removed:
            del self._overrides[k]
        if removed:
            self._save()

    def export_csv(self, path: str | Path, items: list[ActionItem]) -> Path:
        # utf-8-sig: the BOM makes Excel read the file as UTF-8 (Arabic intact);
        # every cell is neutralised against CSV formula injection.
        path = Path(path)
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Task", "Responsible", "Deadline", "Priority", "Status",
                        "Notes", "Meeting", "Meeting date"])
            for i in items:
                w.writerow([csv_safe(v) for v in (
                    i.task, i.owner, i.deadline, i.priority, i.status,
                    i.notes, i.meeting_title, i.meeting_date)])
        return path


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    """Prefix a cell that a spreadsheet would run as a formula (= + - @, or a
    leading tab/CR) with an apostrophe so it is shown as text."""
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_FORMULA_PREFIXES) else s


def all_meetings(history) -> list:
    """Every meeting in history, with no row cap (aggregation must not silently
    stop at the list view's 500). Falls back to list() for simple stand-ins."""
    fn = getattr(history, "list_all", None)
    return list(fn() if callable(fn) else history.list())
