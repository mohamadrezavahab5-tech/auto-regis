"""Dashboard: today's work (done / left / every state), the decision mix, the 14-day trend, why requests went to a person,
and the count of every NBO and CRM status (what the old Main sheet's dashboard showed)."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ... import jalali, store, workflow
from ...texts import REASON_FA, cause_fa
from ..session import run_bg
from .. import theme
from ..theme import C
from ..widgets import BarList, Card, DailyBars, Donut, Legend, Ring, SegmentBar, StatTile, button, label, num
from .common import ACTION_COLORS, ScrollPage, nbo_status_fa, state_items


class DashboardPage(ScrollPage):
    title = "داشبورد"
    subtitle = "کار امروز، وضعیت همه‌ی درخواست‌ها و روند بررسی‌ها"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell = session, shell

        # today's work
        work = Card()
        b_start = button("شروع بررسی", "primary", "play")
        b_start.clicked.connect(lambda: shell.go("review"))
        b_results = button("نتایج", None, "results")
        b_results.clicked.connect(lambda: shell.go("results"))
        work.header("کار امروز", "درخواست‌های آنلاینِ حقیقیِ در انتظار، از جدیدترین", right=[b_start, b_results])
        row = QHBoxLayout()
        row.setSpacing(22)
        self.ring = Ring(136)
        row.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(10)
        self.work_text = label("", "h2")
        self.work_sub = label("", "muted", wrap=True)
        col.addWidget(self.work_text)
        col.addWidget(self.work_sub)
        self.bar = SegmentBar(18)
        col.addWidget(self.bar)
        self.legend = Legend()
        col.addWidget(self.legend)
        self.both_text = label("", "caption", wrap=True)
        col.addWidget(self.both_text)
        col.addStretch(1)
        row.addLayout(col, 1)
        work.lay.addLayout(row)
        self.body.addWidget(work)

        # the two-team workflow: every state, open and finished
        flow = Card()
        b_flow = button("گردش کار", None, "list-check")
        b_flow.clicked.connect(lambda: shell.go("workflow"))
        b_exec = button("آماده‌ی تأیید در NBO", "primary", "check")
        b_exec.clicked.connect(lambda: shell.go("execution"))
        flow.header("گردش کار دو تیم", "از نظر تیم Online تا تأیید در NBO — هم‌زمان در شیت خودت", right=[b_flow, b_exec])
        self.flow_text = label("", "h2")
        flow.lay.addWidget(self.flow_text)
        self.flow_bar = SegmentBar(14)
        flow.lay.addWidget(self.flow_bar)
        self.flow_legend = Legend()
        flow.lay.addWidget(self.flow_legend)
        self.body.addWidget(flow)

        # decision tiles (last 7 days)
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_approve = StatTile("تایید (۷ روز)", ACTION_COLORS["APPROVE"], C["approve_soft"], "check")
        self.t_edit = StatTile("نیاز به اصلاح", ACTION_COLORS["EDIT"], C["edit_soft"], "alert")
        self.t_cancel = StatTile("لغو", ACTION_COLORS["CANCEL"], C["cancel_soft"], "x")
        self.t_manual = StatTile("بررسی دستی", ACTION_COLORS["MANUAL"], C["manual_soft"], "user")
        self.t_speed = StatTile("میانگین زمان هر بررسی", C["text"], C["surface2"], "clock")
        for t in (self.t_approve, self.t_edit, self.t_cancel, self.t_manual, self.t_speed):
            tiles.addWidget(t)
        self.body.addLayout(tiles)

        grid = QGridLayout()
        grid.setSpacing(14)
        trend = Card()
        trend.header("روند ۱۴ روز اخیر", "تعداد تصمیم‌ها در هر روز")
        self.daily = DailyBars(("APPROVE", "EDIT", "CANCEL", "MANUAL"), ACTION_COLORS)
        trend.lay.addWidget(self.daily)
        lg = Legend(show_counts=False)
        lg.set_items([("تایید", 0, ACTION_COLORS["APPROVE"]), ("اصلاح", 0, ACTION_COLORS["EDIT"]),
                      ("لغو", 0, ACTION_COLORS["CANCEL"]), ("دستی", 0, ACTION_COLORS["MANUAL"])])
        trend.lay.addWidget(lg)
        grid.addWidget(trend, 0, 0)
        mix = Card()
        mix.header("سهم تصمیم‌ها", "۷ روز اخیر")
        mrow = QHBoxLayout()
        self.donut = Donut(170)
        mrow.addWidget(self.donut)
        self.mix_legend = QVBoxLayout()
        self.mix_legend.setSpacing(8)
        holder = QWidget()
        holder.setLayout(self.mix_legend)
        mrow.addWidget(holder, 1)
        mix.lay.addLayout(mrow)
        grid.addWidget(mix, 0, 1)

        reasons = Card()
        reasons.header("پرتکرارترین دلیل‌های اصلاح و لغو", "۳۰ روز اخیر، با متن خود NBO")
        self.reasons = BarList(C["edit"])
        reasons.lay.addWidget(self.reasons)
        grid.addWidget(reasons, 1, 0)
        manual = Card()
        manual.header("چرا به بررسی دستی رفت", "۳۰ روز اخیر — راهنمای اینکه کدام قاعده یا داده باید کامل شود")
        self.manual = BarList(C["manual"])
        manual.lay.addWidget(self.manual)
        grid.addWidget(manual, 1, 1)

        nbo = Card()
        self.nbo_head = label("", "caption")
        nbo.header("وضعیت‌ها در NBO", None, self.nbo_head)
        self.nbo_bars = BarList(C["accent"])
        nbo.lay.addWidget(self.nbo_bars)
        grid.addWidget(nbo, 2, 0)
        crm = Card()
        self.crm_head = label("", "caption")
        crm.header("وضعیت‌ها در CRM", None, self.crm_head)
        self.crm_bars = BarList(C["info"])
        crm.lay.addWidget(self.crm_bars)
        grid.addWidget(crm, 2, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        self.body.addLayout(grid)
        self.body.addStretch(1)

        session.data_changed.connect(self.on_show)
        session.run_changed.connect(self._run_tick)
        self._last_tick = 0

    def _run_tick(self):
        now = datetime.now().timestamp()
        if now - self._last_tick > 4 and self.isVisible():          # live, but not on every single decision
            self._last_tick = now
            self.on_show()

    def on_show(self):
        def work(_p):
            s = self.session
            board = s.board()
            db = s.db()
            try:
                return {"board": board, "flow": workflow.counts(db), "week": store.totals(db, days=7), "days": store.daily_counts(db, 14),
                        "reasons": store.reason_counts(db, days=30, limit=8), "manual": store.manual_causes(db, days=30, limit=8)}
            finally:
                db.close()
        run_bg(work, self._show)

    def _show(self, d):
        b = d["board"]["backlog"]
        if b["total"]:
            self.ring.set_value(b["percent"], "بررسی‌شده")
            self.work_text.setText(f"{num(b['reviewed'])} از {num(b['total'])} بررسی شده — {num(b['left'])} مانده")
            self.work_sub.setText(f"{num(b['needs_person'])} مورد منتظر بررسی دستی یکی از همکاران است.")
        else:
            self.ring.set_value(0, "")
            self.work_text.setText("هنوز داده‌ی NBO بارگذاری نشده")
            self.work_sub.setText("از صفحه‌ی «بررسی»، خروجی NBO را دریافت کن تا کار امروز اینجا دیده شود.")
        items = state_items(b["counts"])
        self.bar.set_parts([(n, c) for _, n, c in items])
        self.legend.set_items(items)
        both = d["board"]["both"]
        self.both_text.setText(f"صف آنلاین + حضوری: {num(both['reviewed'])} از {num(both['total'])} بررسی شده، {num(both['left'])} مانده.")
        f = d["flow"]
        order = ("WAIT_ONLINE", "MANUAL", "WAIT_INSTORE", "CONFLICT", "EDIT", "CANCEL", "READY", "DONE_APPROVED", "DONE_CLOSED")
        items = [(workflow.STATES[k].split("؛")[0], f.get(k, 0), theme.WORKFLOW[k][0]) for k in order]
        self.flow_bar.set_parts([(n, c) for _, n, c in items])
        self.flow_legend.set_items([it for it in items if it[1]] or items[:1])
        open_n = sum(f.get(k, 0) for k in workflow.OPEN_STATES)
        self.flow_text.setText(f"{num(f.get('READY', 0))} آماده‌ی تأیید در NBO • {num(f.get('WAIT_INSTORE', 0))} منتظر Instore • "
                               f"{num(open_n)} باز • {num(f.get('DONE_APPROVED', 0))} تأییدشده")
        w = d["week"]
        self.t_approve.set_value(w["counts"]["APPROVE"])
        self.t_edit.set_value(w["counts"]["EDIT"])
        self.t_cancel.set_value(w["counts"]["CANCEL"])
        self.t_manual.set_value(w["counts"]["MANUAL"])
        if w["avg_seconds"]:
            self.t_speed.value.setText(num(w["avg_seconds"], 1) + " ثانیه")
        else:
            self.t_speed.value.setText("—")
        days = [(jalali.fa_digits(jalali.jdate(datetime.fromisoformat(day), False)[5:]), c) for day, c in d["days"]]
        self.daily.set_days(days)
        counts = w["counts"]
        self.donut.set_parts([(counts[k], ACTION_COLORS[k]) for k in ("APPROVE", "EDIT", "CANCEL", "MANUAL")], "تصمیم")
        while self.mix_legend.count():
            it = self.mix_legend.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        total = max(1, w["total"])
        for k, text in (("APPROVE", "تایید"), ("EDIT", "نیاز به اصلاح"), ("CANCEL", "لغو"), ("MANUAL", "بررسی دستی")):
            pct = jalali.fa_digits(f"{100 * counts[k] / total:.0f}") + "٪"
            row = label(f"<span style='color:{ACTION_COLORS[k]}; font-size:16px'>●</span>&nbsp; {text}: <b>{num(counts[k])}</b> "
                        f"<span style='color:{C['text3']}'>({pct})</span>")
            self.mix_legend.addWidget(row)
        self.mix_legend.addStretch(1)
        self.reasons.set_items([(REASON_FA.get(code, code), n, None) for code, n in d["reasons"]])
        self.manual.set_items([(cause_fa(k), n, None) for k, n in d["manual"]])
        sc = d["board"]["status_counts"]
        self.nbo_bars.set_items([(nbo_status_fa(k), n, None) for k, n in sc["nbo"].items()][:10])
        self.crm_bars.set_items([(k, n, None) for k, n in sc["crm"].items()][:10])
        m_nbo, m_crm = d["board"]["nbo_meta"], d["board"]["crm_meta"]
        self.nbo_head.setText(f"{num(m_nbo['rows'])} درخواست — {jalali.ago(m_nbo['loaded_at'])}" if m_nbo else "بارگذاری نشده")
        self.crm_head.setText(f"{num(m_crm['rows'])} درخواست — {jalali.ago(m_crm['loaded_at'])}" if m_crm else "بارگذاری نشده")
