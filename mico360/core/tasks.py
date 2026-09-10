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
from datetime import date, datetime
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


@dataclass
class ActionItem:
    task: str
    owner: str
    deadline: str
    status: str
    meeting_id: int = 0
    meeting_title: str = ""
    meeting_date: str = ""

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
        for row in blk.rows:
            def get(ci):
                return _clean(row[ci]) if 0 <= ci < len(row) else ""
            task = get(ci_task)
            if not task or task.lower() in _PLACEHOLDER:
                continue
            items.append(ActionItem(
                task=task, owner=get(ci_owner), deadline=get(ci_due),
                status=get(ci_status) or "Pending"))
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

    def all_items(self, search: str = "") -> list[ActionItem]:
        import time
        out: list[ActionItem] = []
        for m in self.history.list():
            for it in extract_action_items(m.minutes):
                it.meeting_id = m.id
                it.meeting_title = m.title
                it.meeting_date = time.strftime("%Y-%m-%d", time.localtime(m.updated_at))
                if it.key() in self._overrides:
                    it.status = self._overrides[it.key()]
                it.status = normalize_status(it.status)   # fold "Done" etc. -> canonical
                out.append(it)
        if search.strip():
            q = search.lower()
            out = [i for i in out if q in i.task.lower() or q in i.owner.lower()
                   or q in i.meeting_title.lower() or q in i.status.lower()]
        return out

    def set_status(self, item: ActionItem, status: str):
        self._overrides[item.key()] = status
        item.status = status
        self._save()

    def drop_meeting(self, meeting_id: int) -> None:
        """Remove any status overrides belonging to a deleted meeting."""
        prefix = f"{meeting_id}:"
        removed = [k for k in self._overrides if k.startswith(prefix)]
        for k in removed:
            del self._overrides[k]
        if removed:
            self._save()

    def cycle_status(self, item: ActionItem) -> str:
        cur = item.status if item.status in STATUS_CYCLE else "Pending"
        nxt = STATUS_CYCLE[(STATUS_CYCLE.index(cur) + 1) % len(STATUS_CYCLE)]
        self.set_status(item, nxt)
        return nxt

    def export_csv(self, path: str | Path, items: list[ActionItem]) -> Path:
        path = Path(path)
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Task", "Responsible", "Deadline", "Status", "Meeting", "Meeting date"])
            for i in items:
                w.writerow([i.task, i.owner, i.deadline, i.status, i.meeting_title, i.meeting_date])
        return path
