"""Cross-meeting Insights dashboard — a bird's-eye view across all meetings.

Charts are drawn with QPainter (no external chart library) so the app stays
offline and dependency-light, and they follow the theme palette.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from . import metrics as M
from . import theme
from .components import EmptyState, section_title, subtitle, tip
from .context import AppContext

# Fixed semantic status colours (match the Action Items page).
STATUS_COLORS = {
    "Overdue": "#DC2626", "Pending": "#B8760F", "In Progress": "#A83326",
    "Completed": "#16A34A", "Cancelled": "#9A9AA0",
}


def _scroll(inner: QWidget) -> QScrollArea:
    sa = QScrollArea(); sa.setWidgetResizable(True); sa.setFrameShape(QScrollArea.NoFrame)
    inner.setMaximumWidth(1440)
    sa.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    sa.setWidget(inner)
    return sa


# ---------------------------------------------------------------------------
# Chart widgets
# ---------------------------------------------------------------------------
class _HBars(QWidget):
    """Horizontal bar list: [(label, value)] with elided labels + values."""
    def __init__(self):
        super().__init__()
        self._rows: list[tuple[str, int]] = []
        self._bar = "#A83326"; self._track = "#EEE"; self._text = "#222"; self._muted = "#888"

    def set_data(self, rows, bar, track, text, muted):
        self._rows = rows
        self._bar, self._track, self._text, self._muted = bar, track, text, muted
        self.setMinimumHeight(max(60, len(rows) * 30))
        self.update()

    def paintEvent(self, _):
        if not self._rows:
            return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        n = len(self._rows); rowh = H / n
        maxv = max((v for _, v in self._rows), default=1) or 1
        label_w, val_w, gap = 132, 34, 8
        bar_x = label_w + gap
        bar_max = max(12.0, W - bar_x - val_w - 6)
        f = p.font(); f.setPointSizeF(9.5); p.setFont(f); fm = p.fontMetrics()
        for i, (lab, val) in enumerate(self._rows):
            y = i * rowh; cy = y + rowh / 2
            p.setPen(QColor(self._text))
            p.drawText(QRectF(0, y, label_w, rowh), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(str(lab), Qt.ElideRight, label_w))
            bh = min(16.0, rowh - 8); by = cy - bh / 2
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self._track)); p.drawRoundedRect(QRectF(bar_x, by, bar_max, bh), bh / 2, bh / 2)
            w = bar_max * (val / maxv)
            p.setBrush(QColor(self._bar)); p.drawRoundedRect(QRectF(bar_x, by, max(bh, w), bh), bh / 2, bh / 2)
            p.setPen(QColor(self._muted))
            p.drawText(QRectF(W - val_w, y, val_w, rowh), Qt.AlignVCenter | Qt.AlignRight, str(val))
        p.end()


class _Columns(QWidget):
    """Vertical column chart: [(label, value)] over time."""
    def __init__(self):
        super().__init__()
        self._pts: list[tuple[str, int]] = []
        self._bar = "#A83326"; self._track = "#EEE"; self._muted = "#888"
        self.setMinimumHeight(150)

    def set_data(self, pts, bar, track, muted):
        self._pts = pts; self._bar, self._track, self._muted = bar, track, muted
        self.update()

    def paintEvent(self, _):
        if not self._pts:
            return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        n = len(self._pts); maxv = max((v for _, v in self._pts), default=1) or 1
        pad_t, pad_b, gap = 16.0, 22.0, 8.0
        plot_h = H - pad_t - pad_b
        colw = max(6.0, (W - gap * (n + 1)) / n)
        f = p.font(); f.setPointSizeF(8.5); p.setFont(f); fm = p.fontMetrics()
        for i, (lab, val) in enumerate(self._pts):
            x = gap + i * (colw + gap)
            bh = plot_h * (val / maxv) if maxv else 0
            by = pad_t + (plot_h - bh)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self._bar if val else self._track))
            p.drawRoundedRect(QRectF(x, by, colw, max(3.0, bh)), 3, 3)
            if val:
                p.setPen(QColor(self._muted))
                p.drawText(QRectF(x - 4, by - pad_t, colw + 8, pad_t),
                           Qt.AlignBottom | Qt.AlignHCenter, str(val))
            p.setPen(QColor(self._muted))
            p.drawText(QRectF(x - 4, H - pad_b, colw + 8, pad_b),
                       Qt.AlignTop | Qt.AlignHCenter, fm.elidedText(str(lab), Qt.ElideRight, colw + 8))
        p.end()


class _SegmentBar(QWidget):
    """A single stacked horizontal bar: [(label, value, colour)]."""
    def __init__(self):
        super().__init__()
        self._segs: list[tuple[str, int, str]] = []
        self.setFixedHeight(30)

    def set_data(self, segs):
        self._segs = segs; self.update()

    def paintEvent(self, _):
        if not self._segs:
            return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        total = sum(v for _, v, _ in self._segs) or 1
        bh = 22.0; by = (H - bh) / 2
        clip = QPainterPath(); clip.addRoundedRect(QRectF(0, by, W, bh), bh / 2, bh / 2)
        p.setClipPath(clip)
        x = 0.0
        for _, val, col in self._segs:
            w = W * (val / total)
            p.fillRect(QRectF(x, by, w + 1, bh), QColor(col))
            x += w
        p.end()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
class InsightsPage(QWidget):
    def __init__(self, ctx: AppContext, toast):
        super().__init__()
        self.ctx = ctx; self.toast = toast

        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        v = QVBoxLayout(content); v.setContentsMargins(*M.PAGE_MARGINS); v.setSpacing(M.PAGE_GAP)
        title = QLabel("Insights"); title.setObjectName("PageTitle")
        v.addWidget(title)
        v.addWidget(subtitle("A bird's-eye view across all your meetings — action-item health, "
                             "who owns what, meeting cadence and recurring themes."))

        # KPI cards (compact so five fit even on a narrow window)
        self.kpi_row = QHBoxLayout(); self.kpi_row.setSpacing(M.SM)
        self._kpis = {}
        for key, label in (("meetings", "Meetings"), ("items", "Tasks"),
                           ("open", "Open"), ("overdue", "Overdue"), ("done", "Done")):
            card = QWidget(); card.setObjectName("Card")
            cl = QVBoxLayout(card); cl.setContentsMargins(M.MD, M.SM, M.MD, M.SM); cl.setSpacing(0)
            num = QLabel("—"); num.setObjectName("KpiNum")
            cap = QLabel(label); cap.setObjectName("KpiLabel")
            cl.addWidget(num); cl.addWidget(cap)
            self._kpis[key] = num
            self.kpi_row.addWidget(card, 1)
        v.addLayout(self.kpi_row)

        # chart grid
        grid = QGridLayout(); grid.setHorizontalSpacing(M.MD); grid.setVerticalSpacing(M.MD)
        self.status_bar = _SegmentBar()
        # legend wraps into a 3-column grid so it never forces horizontal overflow
        self.status_legend = QGridLayout()
        self.status_legend.setHorizontalSpacing(M.MD); self.status_legend.setVerticalSpacing(2)
        status_card = self._card("Action-item status")
        status_card.layout().addWidget(self.status_bar)
        lg = QWidget(); lg.setLayout(self.status_legend)
        status_card.layout().addWidget(lg)
        status_card.layout().addStretch()

        self.cadence = _Columns()
        cadence_card = self._card("Meeting cadence (per week)")
        cadence_card.layout().addWidget(self.cadence, 1)

        self.owners = _HBars()
        owners_card = self._card("Open tasks by owner")
        owners_card.layout().addWidget(self.owners, 1)

        self.keywords = _HBars()
        kw_card = self._card("Recurring themes")
        kw_card.layout().addWidget(self.keywords, 1)

        self._grid = grid
        self._chart_cards = [status_card, cadence_card, owners_card, kw_card]
        self._cols = 0
        self.grid_host = QWidget(); self.grid_host.setLayout(grid)
        self._relayout_grid(2)
        v.addWidget(self.grid_host, 1)

        self.empty = EmptyState("📊", "No insights yet",
                                "Generate minutes for a few meetings and this dashboard fills in "
                                "with action-item health, owners, cadence and recurring themes.")
        v.addWidget(self.empty)
        v.addStretch()
        outer.addWidget(_scroll(content))

    def _relayout_grid(self, cols: int):
        if cols == self._cols:
            return
        self._cols = cols
        for c in self._chart_cards:
            self._grid.removeWidget(c)
        for i, c in enumerate(self._chart_cards):
            self._grid.addWidget(c, i // cols, i % cols)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1 if cols > 1 else 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # two chart columns when there's room, one when the window is narrow
        self._relayout_grid(2 if self.width() >= 980 else 1)

    def _card(self, title_text: str) -> QWidget:
        card = QWidget(); card.setObjectName("Card")
        lay = QVBoxLayout(card); lay.setContentsMargins(*M.CARD_MARGINS); lay.setSpacing(M.SM)
        lay.addWidget(section_title(title_text))
        return card

    def reload(self):
        from ..core.insights import compute_insights
        pal = theme.palette(self.ctx.settings.get("theme", "light"))
        ins = compute_insights(self.ctx.history, self.ctx.action_items)

        self.grid_host.setVisible(ins.has_data)
        for c in (self.kpi_row.itemAt(i).widget() for i in range(self.kpi_row.count())):
            if c:
                c.setVisible(ins.has_data)
        self.empty.setVisible(not ins.has_data)
        if not ins.has_data:
            return

        # KPIs
        self._kpis["meetings"].setText(str(ins.total_meetings))
        self._kpis["items"].setText(str(ins.total_items))
        self._kpis["open"].setText(str(ins.open_items))
        self._kpis["overdue"].setText(str(ins.overdue_items))
        self._kpis["done"].setText(f"{round(ins.completion_rate * 100)}%")
        self._kpis["overdue"].setStyleSheet(
            f"color:{STATUS_COLORS['Overdue']};" if ins.overdue_items else "")

        # status segmented bar + legend
        segs = [(k, v, STATUS_COLORS.get(k, pal["muted"])) for k, v in ins.status_counts.items()]
        self.status_bar.set_data(segs)
        while self.status_legend.count():
            it = self.status_legend.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        for idx, (k, v) in enumerate(ins.status_counts.items()):
            lbl = QLabel(f"● {k}  {v}")
            lbl.setStyleSheet(f"color:{STATUS_COLORS.get(k, pal['muted'])}; font-size:9pt; font-weight:600;")
            self.status_legend.addWidget(lbl, idx // 3, idx % 3)   # wrap every 3
        self.status_legend.setColumnStretch(3, 1)

        # charts
        self.cadence.set_data(ins.cadence, pal["accent"], pal["surface2"], pal["muted"])
        self.owners.set_data(ins.by_owner or [], pal["accent"], pal["surface2"], pal["text"], pal["muted"])
        self.keywords.set_data(
            [(w.capitalize(), c) for w, c in ins.keywords] or [],
            pal["accent"], pal["surface2"], pal["text"], pal["muted"])
