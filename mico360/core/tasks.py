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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ..config import DATA_DIR
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
                 "%b %d %Y", "%d %b", "%d %B"]


def normalize_status(status: str) -> str:
    """Fold a free-text status onto a canonical STATUS_CYCLE value (unknown
    values are kept, title-preserved, so nothing is silently lost)."""
    s = (status or "").strip()
    return _STATUS_SYNONYMS.get(s.lower(), s or "Pending")


def parse_deadline(text: str) -> date | None:
    """Best-effort parse of a free-text deadline into a date, or None."""
    s = (text or "").strip()
    if not s or s.lower() in _PLACEHOLDER:
        return None
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        pass
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            if "%Y" not in fmt:                    # year-less → assume current year
                d = d.replace(year=date.today().year)
            return d
        except Exception:
            continue
    return None


def is_overdue(item: "ActionItem", today: date | None = None) -> bool:
    """True when a task has a parseable past deadline and is still open."""
    if normalize_status(item.status) in _CLOSED:
        return False
    d = parse_deadline(item.deadline)
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
        d = parse_deadline(it.deadline)
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

    def key(self) -> str:
        h = hashlib.md5(self.task.strip().lower().encode("utf-8")).hexdigest()[:10]
        return f"{self.meeting_id}:{h}"


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
                return json.loads(self.status_file.read_text(encoding="utf-8"))
        except Exception:
            log.warning("could not load action status overrides", exc_info=True)
        return {}

    def _save(self):
        try:
            self.status_file.write_text(json.dumps(self._overrides, indent=2), encoding="utf-8")
        except Exception:
            log.warning("could not save action status overrides", exc_info=True)

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
        for m in self.history.list():
            for it in extract_action_items(m.minutes):
                it.meeting_id = m.id
                it.meeting_title = m.title
                it.meeting_date = time.strftime("%Y-%m-%d", time.localtime(m.updated_at))
                it.okey = it.key()                    # stable key from the ORIGINAL task
                self._apply_override(it)
                it.status = normalize_status(it.status)     # fold "Done" etc. -> canonical
                it.priority = normalize_priority(it.priority)
                out.append(it)
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
        path = Path(path)
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Task", "Responsible", "Deadline", "Priority", "Status",
                        "Notes", "Meeting", "Meeting date"])
            for i in items:
                w.writerow([i.task, i.owner, i.deadline, i.priority, i.status,
                            i.notes, i.meeting_title, i.meeting_date])
        return path
