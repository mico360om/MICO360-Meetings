"""Cross-meeting insights: aggregate History + Action Items into a few metrics
for the dashboard. Pure logic (no Qt) so it is easy to test.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import tasks as T

# Status display order (Overdue first — it's what needs attention).
STATUS_ORDER = ["Overdue", "Pending", "In Progress", "Completed", "Cancelled"]

# Words ignored when mining recurring themes from minutes text.
_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "will", "have", "has", "was", "were",
    "are", "not", "but", "you", "your", "our", "their", "they", "them", "from", "into",
    "out", "about", "over", "under", "more", "most", "some", "any", "all", "each", "such",
    "than", "then", "there", "here", "which", "who", "what", "when", "where", "how", "why",
    "can", "could", "should", "would", "may", "might", "must", "shall", "also", "been",
    "being", "does", "did", "done", "make", "made", "need", "needs", "want", "like", "just",
    "only", "very", "much", "many", "well", "get", "got", "one", "two", "next", "new",
    # meeting-domain filler that isn't a "theme"
    "meeting", "minutes", "action", "items", "item", "task", "tasks", "discussion",
    "discussed", "point", "points", "note", "notes", "summary", "agenda", "attendees",
    "decision", "decisions", "date", "time", "status", "pending", "specified", "team",
    "person", "responsible", "deadline", "review", "reviewed", "update", "updates",
}


@dataclass
class Insights:
    total_meetings: int = 0
    total_items: int = 0
    open_items: int = 0
    overdue_items: int = 0
    completion_rate: float = 0.0                       # completed / non-cancelled
    status_counts: dict = field(default_factory=dict)  # status -> count (STATUS_ORDER)
    by_owner: list = field(default_factory=list)       # [(owner, open_count)] desc
    cadence: list = field(default_factory=list)        # [(week_label, count)] last N weeks
    keywords: list = field(default_factory=list)       # [(word, count)] desc

    @property
    def has_data(self) -> bool:
        return self.total_meetings > 0 or self.total_items > 0


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())             # Monday of that week


def _ts_to_date(ts) -> date | None:
    try:
        return date.fromtimestamp(ts) if ts else None
    except Exception:
        return None


def compute_insights(history, action_store, *, weeks: int = 8,
                     top_owners: int = 8, top_keywords: int = 12,
                     today: date | None = None) -> Insights:
    today = today or date.today()
    meetings = list(history.list())
    items = list(action_store.all_items())

    # -- action-item status breakdown (Overdue is derived) -----------------
    sc = Counter(T.effective_status(it, today) for it in items)
    status_counts = {k: sc.get(k, 0) for k in STATUS_ORDER if sc.get(k, 0)}
    open_items = sum(1 for it in items
                     if T.normalize_status(it.status) not in ("Completed", "Cancelled"))
    overdue_items = sum(1 for it in items if T.is_overdue(it, today))
    non_cancelled = sum(1 for it in items if T.normalize_status(it.status) != "Cancelled")
    completed = sum(1 for it in items if T.normalize_status(it.status) == "Completed")
    completion_rate = (completed / non_cancelled) if non_cancelled else 0.0

    # -- open tasks by responsible person ----------------------------------
    owner_ct: Counter = Counter()
    for it in items:
        if T.normalize_status(it.status) in ("Completed", "Cancelled"):
            continue
        o = (it.owner or "").strip()
        if o and o.lower() not in T._PLACEHOLDER:
            owner_ct[o] += 1
    by_owner = owner_ct.most_common(top_owners)

    # -- meeting cadence: continuous last N weeks --------------------------
    cad: Counter = Counter()
    for m in meetings:
        d = _ts_to_date(getattr(m, "updated_at", 0) or getattr(m, "created_at", 0))
        if d:
            cad[_week_start(d)] += 1
    end = _week_start(today)
    cadence = [( (end - timedelta(weeks=i)).strftime("%d %b"),
                 cad.get(end - timedelta(weeks=i), 0) )
               for i in range(weeks - 1, -1, -1)]

    # -- recurring themes / keywords across minutes ------------------------
    blob = " ".join((getattr(m, "minutes", "") or "") for m in meetings).lower()
    blob = re.sub(r"[^a-z\s]", " ", blob)
    words = [w for w in blob.split() if len(w) >= 4 and w not in _STOPWORDS]
    keywords = Counter(words).most_common(top_keywords)

    return Insights(
        total_meetings=len(meetings), total_items=len(items),
        open_items=open_items, overdue_items=overdue_items,
        completion_rate=completion_rate, status_counts=status_counts,
        by_owner=by_owner, cadence=cadence, keywords=keywords,
    )
