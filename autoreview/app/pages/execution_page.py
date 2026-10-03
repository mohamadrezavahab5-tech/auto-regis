"""Execution: applying decisions in NBO - a chosen set (kinds, the day of the verdict, how many, or ticked by hand), one
request, or automatically (owner only) - and what NBO really shows afterwards. Clicking a request shows its file: what was
checked and why it was decided. The screen flow is the team's old one (app/nbo_actor.py), made safe."""
import html
from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem)

from ... import execution, jalali, settings, workflow
from ..theme import C
from ..widgets import Card, StatTile, Switch, button, label, ltr
from ..execution_control import ACTION_FA
from .case_view import case_widget, open_case
from .common import ScrollPage


PERIODS = (("today", "امروز"), ("2", "دیروز و امروز"), ("7", "7 روز اخیر"), ("all", "همه"), ("custom", "بازه‌ی دلخواه"))


class ExecutionPage(ScrollPage):
    title = "اعمال در NBO"
    subtitle = "مرحله‌ی 5 از 5: چه چیزی، نظرِ چه روزی و چند تا — بعد «ثبت در NBO»"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell, self.control = session, shell, shell.execution
        self._shown = []                                    # the cases the list shows, in order

        # one place to say WHAT goes to NBO (owner 2026-10-03: "today's approved ones, 10 of them" - it was all or one)
        pick = Card()
        self.b_stop = button("توقف", "danger", "stop", icon_color=C["danger"])
        self.b_stop.clicked.connect(self.control.stop_all)
        self.b_rehearse = button("تمرین (بدون تغییر در NBO)", None, "play")
        self.b_rehearse.clicked.connect(lambda: self._run(True))
        self.b_apply = button("ثبت در NBO", "primary", "check")
        self.b_apply.clicked.connect(lambda: self._run(False))
        pick.header("ثبت در NBO", "نوع، روز و تعداد را انتخاب کن؛ یا در فهرست پایین هر کدام را می‌خواهی تیک بزن",
                    [self.b_stop, self.b_rehearse, self.b_apply])
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(label("چه چیزی:", "muted"))
        self.kind_switches = {}
        for key, text in (("APPROVE", "تأیید"), ("EDIT", "نیاز به اصلاح"), ("CANCEL", "لغو")):
            sw = Switch()
            sw.toggled.connect(self._kinds_changed)
            self.kind_switches[key] = sw
            row.addWidget(sw)
            row.addWidget(label(text))
            row.addSpacing(8)
        row.addSpacing(14)
        row.addWidget(label("نظرِ چه روزی:", "muted"))
        self.period = QComboBox()
        for key, text in PERIODS:
            self.period.addItem(text, key)
        self.period.currentIndexChanged.connect(lambda _i: self.render())
        row.addWidget(self.period)
        self.d_from, self.d_to = QLineEdit(), QLineEdit()
        for w, tip in ((self.d_from, "از — مثل 1405/07/01"), (self.d_to, "تا — مثل 1405/07/11")):
            w.setPlaceholderText(tip)
            w.setFixedWidth(160)
            w.setVisible(False)
            w.editingFinished.connect(self.render)
            row.addWidget(w)
        row.addSpacing(14)
        row.addWidget(label("چند تا:", "muted"))
        self.limit = QSpinBox()
        self.limit.setRange(0, 5000)
        self.limit.setValue(10)
        self.limit.setSpecialValueText("همه")
        self.limit.setFixedWidth(90)
        self.limit.setToolTip("حداکثر تعداد در این نوبت؛ «همه» یعنی بدون سقف")
        self.limit.valueChanged.connect(lambda _v: self._summary())
        row.addWidget(self.limit)
        row.addStretch(1)
        pick.lay.addLayout(row)
        self.count = label("", "h2", wrap=True)
        pick.lay.addWidget(self.count)
        self.batch_line = label("", "h3", wrap=True)
        pick.lay.addWidget(self.batch_line)
        self.stopped = label("", "h3", wrap=True)
        pick.lay.addWidget(self.stopped)
        auto = QHBoxLayout()
        self.switch = Switch()                              # Autopilot in Real: every newly ready request by itself
        self.switch.toggled.connect(self._toggled)
        auto.addWidget(self.switch)
        self.mode_text = label("", "muted", wrap=True)
        auto.addWidget(self.mode_text, 1)
        pick.lay.addLayout(auto)
        pick.lay.addWidget(label("اپ با حساب NBO که داخل خودش وارد شده، یکی‌یکی همان مراحل NBO را می‌زند (Assign to me ← Change Status ← "
                                 "گزینه ← دلیل) و با اولین خطا می‌ایستد. «تمرین» همه‌ی مراحل را تا قبل از Assign و ثبت نهایی می‌رود و "
                                 "چیزی را در NBO عوض نمی‌کند. ثبت گروهی و ثبت خودکار فقط برای مدیر است.", "caption", wrap=True))
        self.body.addWidget(pick)

        ready = Card()
        b_all = button("تیک همه", None, "check")
        b_all.clicked.connect(lambda: self._tick_all(True))
        b_none = button("برداشتن تیک‌ها", None, "x")
        b_none.clicked.connect(lambda: self._tick_all(False))
        b_file = button("پرونده در پنجره‌ی جدا", None, "external")
        b_file.clicked.connect(self._open_file)
        ready.header("درخواست‌های این انتخاب", "روی هر کدام بزن تا ببینی چه چیزهایی چک شد و چرا این تصمیم گرفته شد — "
                     "اگر چیزی را تیک بزنی، فقط همان‌ها ثبت می‌شوند", [b_none, b_all, b_file])
        split = QHBoxLayout()
        split.setSpacing(12)
        self.ready_list = QListWidget()
        self.ready_list.setMinimumHeight(420)
        self.ready_list.itemChanged.connect(lambda _it: self._summary())
        self.ready_list.currentItemChanged.connect(lambda _c, _p: self._show_file())
        self.ready_list.itemDoubleClicked.connect(lambda _it: self._open_file())
        split.addWidget(self.ready_list, 5)
        self.file_area = QScrollArea()
        self.file_area.setWidgetResizable(True)
        self.file_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.file_area.setMinimumHeight(420)
        split.addWidget(self.file_area, 6)
        ready.lay.addLayout(split)
        self.body.addWidget(ready)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_ready = StatTile("آماده‌ی تأیید در NBO", C["accent_text"], C["accent_soft"], "check")
        self.t_instore = StatTile("منتظر نظر Instore", C["info"], C["info_soft"], "clock")
        self.t_done = StatTile("تأییدشده در NBO", C["approve"], C["approve_soft"], "shield")
        self.t_outside = StatTile("تأیید مستقیم در NBO", C["warn"], C["warn_soft"], "alert")
        self.t_outside.setToolTip("در NBO تأیید شده‌اند ولی نظر «تأیید» تیم‌های لازم (Online و برای Online + Instore، Instore هم) "
                                  "در اپ ثبت نشده بود؛ یعنی کسی مستقیم در NBO تأیید زده است. خطا نیست، فقط برای پیگیری.")
        for t in (self.t_ready, self.t_instore, self.t_done, self.t_outside):
            tiles.addWidget(t)
        self.body.addLayout(tiles)

        ledger = Card()
        ledger.header("دفتر اجرا", "هر تغییر وضعیت، با نسخه‌ی پرونده — روی هر ردیف بزن تا پرونده‌ی همان درخواست باز شود")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["کد درخواست", "نتیجه", "زمان", "توضیح"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, w in enumerate((130, 190, 140)):
            self.table.setColumnWidth(i, w)
        self.table.setMinimumHeight(240)
        self.table.cellClicked.connect(lambda r, _c: open_case(self.shell, self.table.item(r, 0).text()) if self.table.item(r, 0) else None)
        ledger.lay.addWidget(self.table)
        self.body.addWidget(ledger)

        # no CRM approval card: CRM is only read (export); its legal requests go to the sheet's Legal tab (owner 2026-10-02)
        self.body.addStretch(1)
        self.control.changed.connect(self._render_if_visible)

    def _render_if_visible(self):
        if self.isVisible():
            self.render()

    def on_show(self):
        self.control.refresh()
        self.render()

    # ---- the choice
    def _days(self):
        """-> (since, until) dates for the chosen period, or None when a custom date cannot be read."""
        key = self.period.currentData()
        today = date.today()
        if key == "today":
            return today, today
        if key in ("2", "7"):
            return today - timedelta(days=int(key) - 1), today
        if key == "custom":
            a, b = self.d_from.text().strip(), self.d_to.text().strip()
            since = jalali.parse_jdate(a) if a else None
            until = jalali.parse_jdate(b) if b else None
            if (a and not since) or (b and not until):
                return None
            return since, until
        return None, None

    def _ticked(self):
        out = []
        for i in range(self.ready_list.count()):
            item = self.ready_list.item(i)
            if item.checkState() == Qt.CheckState.Checked and item.data(Qt.ItemDataRole.UserRole):
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def _chosen(self):
        """What a press of the button takes now: the ticked ones, else the first `limit` of the list."""
        ticked = set(self._ticked())
        if ticked:
            return [c for c in self._shown if c["smr"] in ticked]
        return self._shown[:self.limit.value()] if self.limit.value() else list(self._shown)

    @staticmethod
    def _kinds_text(cases):
        kinds = {}
        for c in cases:
            kinds[execution.target(c)[0]] = kinds.get(execution.target(c)[0], 0) + 1
        return "، ".join(f"{n} {ACTION_FA[k]}" for k, n in kinds.items())

    def _summary(self):
        chosen, ticked = self._chosen(), self._ticked()
        parts = self._kinds_text(self._shown)
        if self._days() is None:
            text = "تاریخ را مثل 1405/07/11 بنویس."
        elif not self._shown:
            text = "با این انتخاب درخواستی آماده‌ی ثبت نیست — روز یا نوع را عوض کن."
        elif ticked:
            text = f"{len(self._shown)} درخواست ({parts}) — {len(chosen)} تای تیک‌خورده ثبت می‌شود"
        elif len(chosen) < len(self._shown):
            text = f"{len(self._shown)} درخواست ({parts}) — {len(chosen)} تای اول (قدیمی‌ترها) ثبت می‌شود"
        else:
            text = f"{len(self._shown)} درخواست ({parts}) — همه ثبت می‌شوند"
        self.count.setText(text)
        busy = bool(self.control.batch) or self.control.mode.live
        self.b_apply.setEnabled(self.control.owner() and not busy and bool(chosen))
        self.b_rehearse.setEnabled(not busy and bool(chosen))
        self.b_stop.setEnabled(busy)

    def _tick_all(self, on):
        self.ready_list.blockSignals(True)
        for i in range(self.ready_list.count()):
            item = self.ready_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole):
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.ready_list.blockSignals(False)
        self._summary()

    def _run(self, rehearsal):
        chosen = self._chosen()
        if not chosen:
            QMessageBox.information(self, "NBO", "با این انتخاب درخواستی آماده نیست.")
            return
        parts = self._kinds_text(chosen)
        if rehearsal:
            text = f"تمرین روی {len(chosen)} درخواست ({parts}): مراحل NBO طی می‌شود ولی چیزی ثبت نمی‌شود. شروع کنم؟"
        else:
            text = (f"{len(chosen)} درخواست ({parts}) با حساب NBO که داخل اپ وارد شده، یکی‌یکی واقعاً در NBO ثبت می‌شود.\n"
                    "با اولین خطا می‌ایستد. شروع کنم؟")
        if QMessageBox.question(self, "تمرین" if rehearsal else "ثبت در NBO", text) != QMessageBox.StandardButton.Yes:
            return
        try:
            n = self.control.start_batch(chosen, rehearsal)
        except (PermissionError, ValueError) as e:
            QMessageBox.information(self, "NBO", str(e))
            return
        if n == 'busy':
            QMessageBox.information(self, "NBO", "یک اجرای دیگر در جریان است؛ اول تمام شود یا «توقف» را بزن.")
        self.render()

    def _toggled(self, on):
        if on and QMessageBox.question(
                self, "ثبت خودکار",
                "با روشن شدن، هر درخواستی که آماده شود (از نوع‌های روشنِ بالا) خودش در NBO ثبت می‌شود، بدون سؤال — "
                "تا وقتی خاموشش کنی یا اپ بسته شود. روشن شود؟") != QMessageBox.StandardButton.Yes:
            self.render()
            return
        try:
            self.control.set_live(on)
        except (PermissionError, ValueError) as e:
            QMessageBox.information(self, "ثبت خودکار", str(e))
        self.render()

    def _kinds_changed(self, _on):
        over = settings.load_user()
        over.setdefault("rules", {})["execution.auto_actions"] = [k for k, sw in self.kind_switches.items() if sw.isChecked()]
        settings.save_user(over)
        self.render()

    def render(self):
        live = self.control.mode.live
        owner = self.control.owner()
        self.switch.blockSignals(True)
        self.switch.setChecked(live)
        self.switch.setEnabled(owner and not self.control.batch)
        self.switch.blockSignals(False)
        self.mode_text.setText("ثبت خودکار روشن است (Real): هر درخواست آماده خودش در NBO ثبت می‌شود" if live
                               else "ثبت خودکار (Autopilot): خاموش — فقط چیزی ثبت می‌شود که خودت «ثبت در NBO» بزنی")
        allowed = self.control.auto_actions()
        for key, sw in self.kind_switches.items():
            sw.blockSignals(True)
            sw.setChecked(key in allowed)
            sw.setEnabled(owner)
            sw.blockSignals(False)
        custom = self.period.currentData() == "custom"
        self.d_from.setVisible(custom)
        self.d_to.setVisible(custom)
        days = self._days()
        self._shown = self.control.ready_cases(allowed, days[0], days[1]) if days is not None else []
        ticked = set(self._ticked())
        current = self._current_smr()
        self.ready_list.blockSignals(True)
        self.ready_list.clear()
        for c in self._shown:
            act = ACTION_FA[execution.target(c)[0]]
            item = QListWidgetItem(f"{act}   •   {ltr(c['smr'])}   •   {'Online + Instore' if c['channel'] == 'both' else 'Online'}   •   "
                                   f"{ltr(c.get('site') or '')}   •   از {jalali.ago(c.get('updated_at'))}")
            item.setData(Qt.ItemDataRole.UserRole, c["smr"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if c["smr"] in ticked else Qt.CheckState.Unchecked)
            self.ready_list.addItem(item)
            if c["smr"] == current:
                self.ready_list.setCurrentItem(item)
        if not self._shown:
            item = QListWidgetItem("با این انتخاب موردی آماده‌ی ثبت نیست — روز یا نوع را عوض کن.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.ready_list.addItem(item)
        self.ready_list.blockSignals(False)
        if self._shown and not self._current_smr():
            self.ready_list.setCurrentRow(0)                # shows its file
        elif self._current_smr() != getattr(self, "_file_smr", None):
            self._show_file()
        self._summary()
        self.batch_line.setText(self.control.batch_text() or ("" if not live else "ثبت خودکار در جریان است، یکی‌یکی…"))
        self.batch_line.setVisible(bool(self.batch_line.text()))
        self.stopped.setText(self.control.last_stop)
        self.stopped.setVisible(bool(self.control.last_stop))
        db = self.session.db()
        try:
            cases = workflow.cases(db)
            records = execution.records(db, limit=300)
        finally:
            db.close()
        by_state = {}
        for c in cases:
            by_state.setdefault(c.get("state") or workflow.state(c), []).append(c)
        self.t_ready.set_value(len(by_state.get("READY", [])))
        self.t_instore.set_value(len(by_state.get("WAIT_INSTORE", [])))
        self.t_done.set_value(len(by_state.get("DONE_APPROVED", [])))
        self.t_outside.set_value(sum(1 for x in records if x["state"] == "APPROVED_IN_NBO" and "بدون" in x["detail"]))
        self.table.setRowCount(len(records))
        for i, x in enumerate(records):
            for j, value in enumerate((x["smr"], x["label"], jalali.jdatetime(x["updated_at"]) if x["updated_at"] else "", x["detail"])):
                self.table.setItem(i, j, QTableWidgetItem(html.unescape(str(value))))

    # ---- the selected request's file
    def _current_smr(self):
        item = self.ready_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_file(self):
        smr = self._current_smr()
        self._file_smr = smr
        body = case_widget(self.session, self.shell, smr) if smr else None
        self.file_area.setWidget(body if body is not None else label("یک درخواست را از فهرست انتخاب کن.", "muted"))

    def _open_file(self):
        smr = self._current_smr()
        if smr:
            open_case(self.shell, smr)


def apply_case(parent, control, case, rehearsal=False):
    """Manual apply (or rehearsal) of one case, with the person's confirmation; shared by the Execution and Workflow pages."""
    from ..widgets import toast
    if not control.may_apply():
        QMessageBox.information(parent, "NBO", "اعمال در NBO برای مدیر و تیم Online است.")
        return
    tgt = execution.target(case)
    if not tgt:
        QMessageBox.information(parent, "NBO", "این پرونده هنوز اقدامی در NBO ندارد (نظر تیم‌ها کامل نیست).")
        return
    reason = control.reason_label(case)
    what = ACTION_FA[tgt[0]] + (f" — دلیل: «{reason}»" if reason else "")
    if rehearsal:
        text = f"تمرین روی {case['smr']} ({what}): همه‌ی مراحل در NBO طی می‌شود جز ثبت نهایی؛ چیزی تغییر نمی‌کند. شروع شود؟"
    else:
        text = f"{case['smr']} در NBO «{what}» می‌شود، با حساب NBO خودت. صفحه‌ی NBO را هم می‌بینی. انجام شود؟"
    if QMessageBox.question(parent, "اعمال در NBO", text) != QMessageBox.StandardButton.Yes:
        return

    def done(res):
        if res.get("ok"):
            toast(parent.window(), "تمرین درست بود؛ چیزی ثبت نشد" if rehearsal else f"{case['smr']} در NBO ثبت شد")
        else:
            QMessageBox.warning(parent, "NBO", res.get("message") or "انجام نشد")
    control.apply(case, done, rehearsal=rehearsal, parent=parent)
