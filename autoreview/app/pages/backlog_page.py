"""NBO backlog: how many requests NBO holds in each of ITS OWN statuses, apart for Online-only and Online + Instore, and
how long the pending ones have been waiting. Straight from the last NBO export - nothing of the app's own workflow is
mixed in. Owner 2026-10-03: "we want to know how much is PENDING - a Backlog tab with NBO's own statuses"."""
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem

from ... import jalali, reference
from ..session import run_bg
from ..theme import C
from ..widgets import BarList, Card, StatTile, button, label, num
from .common import ScrollPage, nbo_status_fa

# Only what is the Online team's: requests with an Online side, of the ownership the queue takes (individuals). NBO's
# legal (company) requests and Instore-only requests are not this team's work (owner 2026-10-03).
PATHS = (("online", "فقط Online"), ("both", "Online + Instore"))
OPEN = ("PENDING", "COMMERCIAL_IN_PROGRESS")
AGES = ((0, 0, "امروز"), (1, 3, "1 تا 3 روز"), (4, 7, "4 تا 7 روز"), (8, 30, "8 تا 30 روز"), (31, 10 ** 6, "بیش از 30 روز"))


def path_of(has_online, has_instore):
    online, instore = str(has_online).lower() == "true", str(has_instore).lower() == "true"
    return "both" if online and instore else ("online" if online else "instore")


def numbers(db, today=None, ownership="INDIVIDUAL"):
    """-> {statuses: [(status, {path: n}, total)], pending: {path: n}, ages: {path: [n per AGES]}, meta}"""
    today = today or date.today()
    by_status, ages = {}, {k: [0] * len(AGES) for k, _ in PATHS}
    for status, has_online, has_instore, created, owner in db.execute(
            "SELECT status, has_online, has_instore, created_at, ownership FROM ref_nbo"):
        path = path_of(has_online, has_instore)
        if path == "instore" or (ownership and owner != ownership):
            continue
        row = by_status.setdefault(status or "?", {k: 0 for k, _ in PATHS})
        row[path] += 1
        if status == "PENDING":
            made = jalali.parse_jdate(created)
            if made:
                days = (today - made).days
                for i, (lo, hi, _t) in enumerate(AGES):
                    if lo <= days <= hi:
                        ages[path][i] += 1
                        break
    order = sorted(by_status, key=lambda s: (s not in OPEN, OPEN.index(s) if s in OPEN else 0, -sum(by_status[s].values())))
    return dict(statuses=[(s, by_status[s], sum(by_status[s].values())) for s in order],
                pending=by_status.get("PENDING", {k: 0 for k, _ in PATHS}), ages=ages, meta=reference.meta(db, "nbo"))


class BacklogPage(ScrollPage):
    title = "بک‌لاگ NBO"
    subtitle = "هر وضعیتِ خود NBO چند درخواست دارد — Online و Online + Instore جدا"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell = session, shell
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_all = StatTile("Pending — جمع", C["text"], C["surface2"], "clock")
        self.t_online = StatTile("Pending — فقط Online", C["accent_text"], C["accent_soft"], "review")
        self.t_both = StatTile("Pending — Online + Instore", C["info"], C["info_soft"], "list-check")
        self.t_progress = StatTile("Commercial in progress", C["warn"], C["warn_soft"], "play")
        for t in (self.t_all, self.t_online, self.t_both, self.t_progress):
            tiles.addWidget(t)
        self.body.addLayout(tiles)

        table = Card()
        b_refresh = button("دریافت دوباره از NBO", None, "refresh", "صفحه‌ی «دریافت و بررسی» را باز می‌کند")
        b_refresh.clicked.connect(lambda: shell.go("review"))
        table.header("تعداد در هر وضعیت NBO", "فقط درخواست‌های حقیقی که سمت Online دارند — حقوقی‌ها و فقط‌حضوری‌ها کار این تیم نیست",
                     [b_refresh])
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["وضعیت در NBO", "فقط Online", "Online + Instore", "جمع"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.lay.addWidget(self.table)
        self.fresh = label("", "caption", wrap=True)
        table.lay.addWidget(self.fresh)
        self.body.addWidget(table)

        age = Card()
        age.header("Pendingها چند روز است منتظرند", "از تاریخ ثبت در NBO تا امروز")
        row = QHBoxLayout()
        row.setSpacing(18)
        self.age_bars = {}
        for key, text in PATHS[:2]:
            col = Card(soft=True)
            col.lay.addWidget(label(text, "h3"))
            bars = BarList(C["accent"] if key == "online" else C["info"])
            col.lay.addWidget(bars)
            self.age_bars[key] = bars
            row.addWidget(col, 1)
        age.lay.addLayout(row)
        self.body.addWidget(age)
        self.body.addStretch(1)
        session.data_changed.connect(self._reload_if_visible)

    def _reload_if_visible(self):
        if self.isVisible():
            self.on_show()

    def on_show(self):
        def work(_p):
            db = self.session.db()
            try:
                return numbers(db, ownership=self.session.rules().get("backlog", {}).get("ownership", "INDIVIDUAL"))
            finally:
                db.close()
        run_bg(work, self._show)

    def _show(self, d):
        p = d["pending"]
        self.t_all.set_value(sum(p.values()))
        self.t_online.set_value(p.get("online", 0))
        self.t_both.set_value(p.get("both", 0))
        self.t_progress.set_value(next((total for s, _row, total in d["statuses"] if s == "COMMERCIAL_IN_PROGRESS"), 0))
        self.table.setRowCount(len(d["statuses"]))
        for i, (status, row, total) in enumerate(d["statuses"]):
            cells = [f"{nbo_status_fa(status)}  ({status})"] + [num(row[k]) for k, _ in PATHS] + [num(total)]
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if j:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if status in OPEN:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                    item.setForeground(QColor(C["accent_text"] if status == "PENDING" else C["text"]))
                self.table.setItem(i, j, item)
        self.table.setFixedHeight(42 + 36 * max(1, len(d["statuses"])))
        for key, bars in self.age_bars.items():
            bars.set_items([(text, d["ages"][key][i], None) for i, (_lo, _hi, text) in enumerate(AGES)])
        m = d["meta"]
        if m:
            self.fresh.setText(f"از خروجی NBO — آخرین دریافت: {jalali.ago(m['loaded_at'])}. Pending و Commercial in progress هر 10 دقیقه "
                               "تازه می‌شوند و بقیه‌ی وضعیت‌ها هر 3 ساعت؛ برای همین عدد وضعیت‌های دیگر ممکن است چند ساعت قدیمی باشد.")
        else:
            self.fresh.setText("هنوز خروجی NBO دریافت نشده — از «دریافت و بررسی» بگیر.")
