"""The control room (how the queue moves, when it will be empty, what waited longest, who did what) and the engine's accuracy
(how often people kept the engine's verdict, which rule they overrule most). Owner 2026-10-02."""
import html
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QGridLayout, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout

from ... import insights, jalali, reference, store, workflow
from ...texts import ACTION_FA, REASON_FA
from .. import theme
from ..session import run_bg
from ..theme import C
from ..widgets import BarList, Card, DailyBars, EmptyState, Legend, Ring, StatTile, button, label, num
from .common import ScrollPage

SERIES = {"engine": C["accent"], "human": C["manual"], "nbo": C["approve"]}


def _days(value):
    if value is None:
        return "—"
    if value < 1:
        return "کمتر از یک روز"
    return f"{num(value, 1 if value < 10 else 0)} روز"


class ControlRoomPage(ScrollPage):
    title = "اتاق کنترل"
    subtitle = "صف با چه سرعتی جلو می‌رود، کی خالی می‌شود، کدام درخواست بیشتر منتظر مانده و چه کسی چقدر کار کرده"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell = session, shell
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_open = StatTile("درخواست باز", C["text"], C["surface2"], "list-check")
        self.t_undecided = StatTile("هنوز بی‌نظر", C["manual"], C["manual_soft"], "user")
        self.t_speed = StatTile("تصمیم همکاران در روز", C["accent_text"], C["accent_soft"], "play")
        self.t_eta = StatTile("صف خالی می‌شود در", C["info"], C["info_soft"], "clock")
        self.t_done = StatTile("میانگین تا نتیجه‌ی NBO", C["approve"], C["approve_soft"], "shield")
        for t in (self.t_open, self.t_undecided, self.t_speed, self.t_eta, self.t_done):
            tiles.addWidget(t)
        self.body.addLayout(tiles)

        trend = Card()
        trend.header("کار هر روز — 14 روز اخیر", "بررسی‌های موتور، نظرهای آدم‌ها و نتیجه‌هایی که در NBO نشست")
        self.daily = DailyBars(tuple(SERIES), SERIES)
        trend.lay.addWidget(self.daily)
        lg = Legend(show_counts=False)
        lg.set_items([("موتور", 0, SERIES["engine"]), ("نظر آدم‌ها", 0, SERIES["human"]), ("نتیجه در NBO", 0, SERIES["nbo"])])
        trend.lay.addWidget(lg)
        self.body.addWidget(trend)

        grid = QGridLayout()
        grid.setSpacing(14)
        old = Card()
        b_flow = button("نظر تیم‌ها", None, "list-check")
        b_flow.clicked.connect(lambda: shell.go("workflow"))
        old.header("بیشترین انتظار", "درخواست‌های باز، از تاریخ ثبت در NBO", b_flow)
        self.oldest = BarList(C["warn"])
        self.oldest.empty_text = "درخواست بازی نیست"
        old.lay.addWidget(self.oldest)
        old.lay.addStretch(1)                     # side-by-side cards keep their content at the top
        grid.addWidget(old, 0, 0)
        ppl = Card()
        ppl.header("چه کسی چقدر", "7 روز اخیر — نظرهای ثبت‌شده و بررسی‌های موتور")
        self.people = BarList(C["manual"])
        self.people.empty_text = "هنوز کسی نظری ثبت نکرده"
        ppl.lay.addWidget(self.people)
        ppl.lay.addStretch(1)                     # side-by-side cards keep their content at the top
        grid.addWidget(ppl, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)
        self.note = label("", "caption", wrap=True)
        self.body.addWidget(self.note)
        self.body.addStretch(1)
        session.data_changed.connect(self._reload_if_visible)

    def _reload_if_visible(self):
        if self.isVisible():
            self.on_show()

    def on_show(self):
        def work(_p):
            db = self.session.db()
            try:
                since = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
                # one engine decision per request per day; internal errors are not decisions
                per_day = {}
                for smr, at, action in db.execute(f"SELECT smr, decided_at, action FROM results WHERE decided_at >= ? "
                                                  f"AND {store._REVIEWED} ORDER BY decided_at", (since,)):
                    per_day[(at[:10], smr)] = (at, action)
                results = list(per_day.values())
                created = reference.created_dates(db)
                return insights.control_room(workflow.cases(db), insights.events(db), results, created,
                                             self.session.rules()["approved_statuses"]["nbo"])
            finally:
                db.close()
        run_bg(work, self._show)

    def _show(self, d):
        self.t_open.set_value(d["open"])
        self.t_undecided.set_value(d["undecided"])
        self.t_speed.value.setText(num(d["speed"], 0) if d["speed"] else "—")
        self.t_eta.value.setText(_days(d["eta_days"]))
        self.t_done.value.setText(_days(d["avg_days_to_done"]))
        self.daily.set_days([(jalali.fa_digits(jalali.jdate(datetime(day.year, day.month, day.day), False)[5:]), c) for day, c in d["daily"]])
        self.oldest.set_items([(f"{smr} — {workflow.STATES[st].split('؛')[0]}", days, theme.WORKFLOW[st][0]) for days, smr, st, _site in d["oldest"]])
        self.people.set_items([(who, n, None) for who, n in d["people"]])
        self.note.setText("«تصمیم همکاران در روز» = میانگین نظرهایی که همکاران (Online و Instore) در روزهای کاری ۷ روز اخیر ثبت کرده‌اند؛ "
                          "«صف خالی می‌شود» = درخواست‌های بی‌نظر تقسیم بر همین عدد. این‌ها منتظر آدم‌اند، پس سرعت موتور در آن حساب نمی‌شود؛ "
                          "تا همکاری نظری ثبت نکرده، تخمینی نشان داده نمی‌شود.")


class AccuracyPage(ScrollPage):
    title = "دقت موتور"
    subtitle = "وقتی آدم‌ها نظر موتور را دیدند، چقدر همان را نگه داشتند — و کدام قاعده بیشتر برگردانده شد"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell = session, shell
        head = Card()
        head.header("هم‌نظری تیم با موتور", "از نظرهایی که یک نفر روی پیشنهاد «تأیید / اصلاح / لغو» موتور ثبت کرده")
        row = QHBoxLayout()
        row.setSpacing(22)
        self.ring = Ring(150, C["approve"])
        row.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        self.headline = label("", "h2", wrap=True)
        self.subline = label("", "muted", wrap=True)
        col.addWidget(self.headline)
        col.addWidget(self.subline)
        tiles = QHBoxLayout()
        self.t_cmp = StatTile("بررسی‌شده توسط آدم", C["text"], C["surface2"], "user")
        self.t_ok = StatTile("همان نظر موتور", C["approve"], C["approve_soft"], "check")
        self.t_over = StatTile("برگردانده شد", C["cancel"], C["cancel_soft"], "x")
        self.t_nbo = StatTile("تأیید نتیجه در NBO", C["info"], C["info_soft"], "shield")
        for t in (self.t_cmp, self.t_ok, self.t_over, self.t_nbo):
            tiles.addWidget(t)
        col.addLayout(tiles)
        row.addLayout(col, 1)
        head.lay.addLayout(row)
        self.body.addWidget(head)

        grid = QGridLayout()
        grid.setSpacing(14)
        rules = Card()
        rules.header("قاعده‌هایی که بیشتر برگردانده شدند", "تعداد برگردانده‌شده (از کل بررسی‌شده)")
        self.rules = BarList(C["cancel"])
        self.rules.empty_text = "هنوز چیزی برگردانده نشده"
        rules.lay.addWidget(self.rules)
        rules.lay.addStretch(1)                     # side-by-side cards keep their content at the top
        grid.addWidget(rules, 0, 0)
        man = Card()
        man.header("تصمیم آدم‌ها روی موارد «دستی»", "کمک می‌کند قاعده‌ی تازه بسازیم تا این‌ها هم خودکار شوند")
        self.manual = BarList(C["manual"])
        self.manual.empty_text = "هنوز مورد دستی‌ای تصمیم نگرفته"
        man.lay.addWidget(self.manual)
        man.lay.addStretch(1)                     # side-by-side cards keep their content at the top
        grid.addWidget(man, 0, 1)
        self.body.addLayout(grid)

        dis = Card()
        dis.header("آخرین اختلاف‌ها", "جایی که یک نفر نظر موتور را عوض کرد — برای بهتر کردن قاعده‌ها")
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["کد درخواست", "موتور گفت", "نظر نهایی", "چه کسی", "توضیح", "کی"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((120, 220, 150, 130, 260)):
            self.table.setColumnWidth(i, w)
        self.table.setMinimumHeight(260)
        self.table.cellDoubleClicked.connect(lambda *_: shell.go("workflow"))
        dis.lay.addWidget(self.table)
        self.empty = EmptyState("check", "هنوز مقایسه‌ای نیست",
                                "وقتی یک نفر در «گردش کار» روی پیشنهاد موتور نظر ثبت کند (همان یا متفاوت)، عددها اینجا ساخته می‌شوند.")
        dis.lay.addWidget(self.empty)
        self.body.addWidget(dis)
        self.body.addStretch(1)
        session.data_changed.connect(self._reload_if_visible)

    def _reload_if_visible(self):
        if self.isVisible():
            self.on_show()

    def on_show(self):
        def work(_p):
            db = self.session.db()
            try:
                return insights.accuracy(workflow.cases(db))
            finally:
                db.close()
        run_bg(work, self._show)

    def _show(self, a):
        rate = a["rate"]
        self.ring.set_value(rate or 0, "هم‌نظر")
        if rate is None:
            self.headline.setText("هنوز کسی پیشنهاد موتور را بررسی نکرده")
            self.subline.setText("نظرهای تیم در «گردش کار» یا تب‌های شیت که ثبت شوند، این عدد ساخته می‌شود.")
        else:
            self.headline.setText(f"تیم در {num(rate)}% موارد همان نظر موتور را داد")
            self.subline.setText(f"{num(a['agreed'])} از {num(a['compared'])} پیشنهادی که یک نفر بررسی کرد.")
        self.t_cmp.set_value(a["compared"])
        self.t_ok.set_value(a["agreed"])
        self.t_over.set_value(a["compared"] - a["agreed"])
        self.t_nbo.set_value(a["outcome_confirmed"], f"از {num(a['outcome_settled'])} نتیجه‌ای که NBO نهایی کرد" if a["outcome_settled"] else "")
        self.rules.set_items([(f"{REASON_FA.get(r, ACTION_FA.get(r, r))} — {num(over)} از {num(n)}", over, None)
                              for r, n, over in a["by_rule"] if over][:8])
        self.manual.set_items([(ACTION_FA.get(k, k), n, theme.ACTION.get(k, (None,))[0]) for k, n in
                               sorted(a["manual"].items(), key=lambda x: -x[1])])
        rows = a["disagreements"]
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.table.setRowCount(len(rows))
        for i, d in enumerate(rows):
            engine = ACTION_FA.get(d["engine"], d["engine"]) + (f" — {REASON_FA.get(d['engine_reasons'][0], d['engine_reasons'][0])}"
                                                               if d["engine_reasons"] else "")
            human = ACTION_FA.get(d["human"], d["human"]) + (f" — {REASON_FA.get(d['human_reason'], d['human_reason'])}" if d["human_reason"] else "")
            values = (d["smr"], engine, human, d["actor"], d["note"], jalali.jdatetime(d["at"]) if d["at"] else "")
            for j, v in enumerate(values):
                item = QTableWidgetItem(html.unescape(str(v)))
                item.setToolTip(str(v))
                self.table.setItem(i, j, item)

