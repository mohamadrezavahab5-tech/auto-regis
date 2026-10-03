"""Execution: applying decisions in NBO - a chosen set (kinds, the day of the verdict, how many, or ticked by hand), one
request, or automatically (owner only) - and what NBO really shows afterwards. Clicking a request shows its file: what was
checked and why it was decided. The screen flow is the team's old one (app/nbo_actor.py), made safe."""
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QListWidget, QListWidgetItem,
                               QMessageBox, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QSplitter)

from ... import execution, jalali, settings, workflow
from ..theme import C
from ..widgets import Card, StatTile, Switch, button, label, ltr
from ..execution_control import ACTION_FA
from ..date_range import DateRange
from .case_view import case_widget, open_case
from .common import ScrollPage


KINDS = ((None, "همه‌ی نوع‌ها"), ("APPROVE", "فقط تأیید"), ("EDIT", "فقط نیاز به اصلاح"), ("CANCEL", "فقط لغو"))
PATHS = ((None, "هر دو مسیر"), ("online", "فقط Online"), ("both", "Online + Instore"))
PERIODS = (("today", "امروز"), ("2", "دیروز و امروز"), ("7", "7 روز اخیر"), ("all", "همه"), ("custom", "بازه‌ی دلخواه"))


class ExecutionPage(ScrollPage):
    title = "ثبت در NBO"
    subtitle = "درخواست‌ها را انتخاب کن و «ثبت در NBO» را بزن — هر چه اینجا ثبت شود واقعی است"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell, self.control = session, shell, shell.execution
        self._shown = []                                    # the cases the list shows, in order
        self._checked = set()

        # one place to say WHAT goes to NBO (owner 2026-10-03: "today's approved ones, 10 of them" - it was all or one)
        pick = Card()
        self.b_stop = button("توقف", "danger", "stop", icon_color=C["danger"])
        self.b_stop.clicked.connect(self.control.stop_all)
        self.b_rehearse = button("پیش‌نمایش بدون ثبت", None, "play")     # not shown: the owner works for real only
        self.b_rehearse.setVisible(False)
        self.b_rehearse.clicked.connect(lambda: self._run(True))
        self.b_apply = button("ثبت در NBO", "primary", "check")
        self.b_apply.clicked.connect(lambda: self._run(False))
        pick.header("۱. انتخاب درخواست‌ها", "فقط درخواست‌هایی نمایش داده می‌شوند که تصمیمشان کامل و قابل اجراست")
        row = QHBoxLayout()
        row.setSpacing(10)
        # One list instead of three look-alike switches (owner 2026-10-03: "the filters are not clear - I want only the
        # ones that need approving and cannot"). It opens on approvals only.
        row.addWidget(label("نوع تصمیم:", "muted"))
        self.kind = QComboBox()
        self.kind.setMinimumWidth(230)
        self.kind.setAccessibleName('نوع تصمیم')
        for key, text in KINDS:
            self.kind.addItem(text, key)
        self.kind.setCurrentIndex(self.kind.findData("APPROVE"))
        self.kind.currentIndexChanged.connect(self._kinds_changed)
        row.addWidget(self.kind)
        row.addSpacing(14)
        row.addWidget(label("مسیر:", "muted"))
        self.path = QComboBox()                             # owner 2026-10-03: "why can't Online be set apart here"
        self.path.setMinimumWidth(190)
        self.path.setAccessibleName('مسیر درخواست')
        for key, text in PATHS:
            self.path.addItem(text, key)
        self.path.currentIndexChanged.connect(lambda _i: self.render())
        row.addWidget(self.path)
        row.addStretch(1)
        pick.lay.addLayout(row)
        dates = QHBoxLayout()
        dates.addWidget(label('تاریخ تصمیم:', 'muted'))
        self.date_range = DateRange()
        self.period = self.date_range.period
        self.date_range.changed.connect(self.render)
        dates.addWidget(self.date_range, 1)
        pick.lay.addLayout(dates)
        row = QHBoxLayout()
        row.addSpacing(14)
        row.addWidget(label("دسته:", "muted"))
        self.category = QComboBox()                         # owner 2026-10-03: "why can't I pick a category here"
        self.category.setMinimumWidth(170)
        self.category.setMaximumWidth(310)
        self.category.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.category.setAccessibleName('دسته‌بندی درخواست‌ها')
        self.category.addItem("همه‌ی دسته‌ها", None)
        self.category.currentIndexChanged.connect(lambda _i: self.render())
        row.addWidget(self.category)
        row.addSpacing(14)
        self.selection_mode = QComboBox()
        self.selection_mode.addItem('تعداد مشخص از ابتدای فهرست', 'first')
        self.selection_mode.addItem('فقط موارد تیک‌خورده', 'checked')
        self.selection_mode.addItem('همهٔ نتایج همین فیلتر', 'all')
        self.selection_mode.setAccessibleName('روش انتخاب برای ثبت')
        self.selection_mode.currentIndexChanged.connect(lambda _i: self._summary())
        row.addWidget(self.selection_mode)
        self.limit = QSpinBox()
        self.limit.setRange(1, 5000)
        self.limit.setValue(10)
        self.limit.setFixedWidth(90)
        self.limit.setToolTip("تعداد درخواست از ابتدای فهرست؛ برای انتخاب همه، روش انتخاب را تغییر بده")
        self.limit.valueChanged.connect(lambda _v: self._summary())
        row.addWidget(self.limit)
        row.addStretch(1)
        pick.lay.addLayout(row)
        self.count = label("", "h2", wrap=True)
        pick.lay.addWidget(self.count)
        actions = QHBoxLayout()
        actions.addWidget(self.b_apply)
        actions.addStretch(1)
        actions.addWidget(self.b_stop)
        pick.lay.addLayout(actions)
        pick.lay.addWidget(label('«ثبت در NBO» وضعیت همین انتخاب را واقعاً در NBO تغییر می‌دهد و با اولین خطا می‌ایستد.', 'caption', wrap=True))
        self.batch_line = label("", "h3", wrap=True)
        pick.lay.addWidget(self.batch_line)
        self.stopped = label("", "h3", wrap=True)
        pick.lay.addWidget(self.stopped)
        self.body.addWidget(pick)
        auto_card = Card()
        auto_card.header('اجرای خودکار — پیشرفته', 'این تنظیمات مستقل از فیلتر و انتخاب دستی بالاست')
        auto = QHBoxLayout()
        self.switch = Switch()                              # Autopilot in Real: every newly ready request by itself
        self.switch.toggled.connect(self._toggled)
        auto.addWidget(self.switch)
        self.mode_text = label("", "muted", wrap=True)
        auto.addWidget(self.mode_text, 1)
        auto_card.lay.addLayout(auto)
        auto_kinds = QHBoxLayout()
        auto_kinds.addWidget(label('عملیات مجاز خودکار:', 'muted'))
        self.auto_kind_switches = {}
        for key, text in ACTION_FA.items():
            sw = Switch()
            sw.setAccessibleName('اجرای خودکار ' + text)
            sw.setChecked(key in self.control.auto_actions())
            sw.toggled.connect(self._auto_kinds_changed)
            self.auto_kind_switches[key] = sw
            auto_kinds.addWidget(sw)
            auto_kinds.addWidget(label(text))
        auto_kinds.addStretch(1)
        auto_card.lay.addLayout(auto_kinds)
        auto_card.lay.addWidget(label('اجرای خودکار تمام درخواست‌های آماده از نوع‌های مجاز را دربر می‌گیرد؛ '
            'بازه، دسته و تعدادِ انتخاب دستی روی آن اثر ندارند. با اولین خطا متوقف می‌شود.', 'caption', wrap=True))

        ready = Card()
        b_all = button("تیک همه", None, "check")
        b_all.clicked.connect(lambda: self._tick_all(True))
        b_none = button("برداشتن تیک‌ها", None, "x")
        b_none.clicked.connect(lambda: self._tick_all(False))
        b_file = button("پرونده در پنجره‌ی جدا", None, "external")
        b_file.clicked.connect(self._open_file)
        ready.header("۲. مرور درخواست‌ها و شواهد", "کلیک روی ردیف، پرونده را نشان می‌دهد؛ تیک زدن، آن را برای ثبت انتخاب می‌کند",
                     [b_none, b_all, b_file])
        split = QSplitter(Qt.Orientation.Horizontal)
        self.ready_list = QListWidget()
        self.ready_list.setMinimumHeight(420)
        self.ready_list.itemChanged.connect(self._selection_changed)
        self.ready_list.setWordWrap(True)
        self.ready_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ready_list.currentItemChanged.connect(lambda _c, _p: self._show_file())
        self.ready_list.itemDoubleClicked.connect(lambda _it: self._open_file())
        split.addWidget(self.ready_list)
        self.file_area = QScrollArea()
        self.file_area.setWidgetResizable(True)
        self.file_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.file_area.setMinimumHeight(420)
        split.addWidget(self.file_area)
        split.setSizes([450, 450])
        split.setChildrenCollapsible(False)
        ready.lay.addWidget(split)
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
        self.body.insertLayout(0, tiles)

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
        self.body.addWidget(auto_card)

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
        return self.date_range.bounds()

    def _ticked(self):
        out = []
        for i in range(self.ready_list.count()):
            item = self.ready_list.item(i)
            if item.checkState() == Qt.CheckState.Checked and item.data(Qt.ItemDataRole.UserRole):
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def _chosen(self):
        """What a press of the button takes now: the ticked ones, else the first `limit` of the list."""
        if self._days() is None:
            return []
        mode = self.selection_mode.currentData()
        if mode == 'checked':
            return [c for c in self._shown if c['smr'] in self._checked]
        return list(self._shown) if mode == 'all' else self._shown[:self.limit.value()]

    def _selection_changed(self, _item):
        visible = {c['smr'] for c in self._shown}
        self._checked.difference_update(visible)
        self._checked.update(self._ticked())
        self.selection_mode.setCurrentIndex(self.selection_mode.findData('checked'))
        self._summary()

    @staticmethod
    def _kinds_text(cases):
        kinds = {}
        for c in cases:
            kinds[execution.target(c)[0]] = kinds.get(execution.target(c)[0], 0) + 1
        return "، ".join(f"{n} {ACTION_FA[k]}" for k, n in kinds.items())

    def _summary(self):
        chosen = self._chosen()
        checked = self.selection_mode.currentData() == 'checked'
        self.limit.setEnabled(self.selection_mode.currentData() == 'first')
        parts = self._kinds_text(self._shown)
        if self._days() is None:
            text = "تاریخ شروع باید قبل از تاریخ پایان یا برابر آن باشد."
        elif not self._shown:
            text = "با این انتخاب درخواستی آماده‌ی ثبت نیست — روز یا نوع را عوض کن."
        elif checked:
            text = f"{len(self._shown)} درخواست ({parts}) — {len(chosen)} تای تیک‌خورده ثبت می‌شود"
        elif len(chosen) < len(self._shown):
            text = f"{len(self._shown)} درخواست ({parts}) — {len(chosen)} تای اول (قدیمی‌ترها) ثبت می‌شود"
        else:
            text = f"{len(self._shown)} درخواست ({parts}) — همه ثبت می‌شوند"
        self.count.setText(text)
        if checked:
            hidden = self._checked - {c['smr'] for c in self._shown}
            if hidden:
                self.count.setText(text + f' — {len(hidden)} انتخاب خارج از این فیلتر است و ارسال نمی‌شود')
        self.b_apply.setText(f'ثبت واقعی {len(chosen)} درخواست' if chosen else 'ثبت واقعی در NBO')
        busy = bool(self.control.batch) or self.control.mode.live or self.control.actor.busy
        self.b_apply.setEnabled(self.control.may_apply() and not busy and bool(chosen))
        self.b_rehearse.setEnabled(not busy and bool(chosen))
        self.b_stop.setEnabled(busy)

    def _tick_all(self, on):
        if not on:
            self._checked.clear()
        else:
            self._checked.update(c['smr'] for c in self._shown)
        self.ready_list.blockSignals(True)
        for i in range(self.ready_list.count()):
            item = self.ready_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole):
                item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.ready_list.blockSignals(False)
        self.selection_mode.setCurrentIndex(self.selection_mode.findData('checked'))
        self._summary()

    def _run(self, rehearsal):
        self.render()  # recompute fresh eligibility before the confirmation
        chosen = self._chosen()
        if not chosen:
            QMessageBox.information(self, "NBO", "با این انتخاب درخواستی آماده نیست.")
            return
        parts = self._kinds_text(chosen)
        if rehearsal:
            text = f"تمرین روی {len(chosen)} درخواست ({parts}): مراحل NBO طی می‌شود ولی چیزی ثبت نمی‌شود. شروع کنم؟"
        else:
            text = (f"{len(chosen)} درخواست ({parts}) با حساب NBO که داخل اپ وارد شده، یکی‌یکی واقعاً در NBO ثبت می‌شود.\n"
                    f"بازه: {self.date_range.description()}\nدسته: {self.category.currentText()}\n"
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
                "با روشن شدن، هر درخواست آماده از نوع‌های مجاز خودکار، مستقل از فیلتر تاریخ و دسته و تعداد، در NBO ثبت می‌شود — "
                "تا وقتی خاموشش کنی یا اپ بسته شود. روشن شود؟") != QMessageBox.StandardButton.Yes:
            self.render()
            return
        try:
            self.control.set_live(on)
        except (PermissionError, ValueError) as e:
            QMessageBox.information(self, "ثبت خودکار", str(e))
        self.render()

    def _kinds_changed(self, _on):
        self.render()

    def _auto_kinds_changed(self, _on):
        over = settings.load_user()
        over.setdefault("rules", {})["execution.auto_actions"] = [k for k, sw in self.auto_kind_switches.items() if sw.isChecked()]
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
        kind = self.kind.currentData()
        allowed = set(ACTION_FA) if kind is None else {kind}
        for key, sw in self.auto_kind_switches.items():
            sw.blockSignals(True)
            sw.setChecked(key in self.control.auto_actions())
            sw.setEnabled(owner and not live and not self.control.batch)
            sw.blockSignals(False)
        days = self._days()
        every = self.control.ready_cases(set(ACTION_FA), days[0], days[1]) if days is not None else []
        per_kind = {}
        for c in every:
            per_kind[execution.target(c)[0]] = per_kind.get(execution.target(c)[0], 0) + 1
        self.kind.blockSignals(True)                     # each choice shows how many requests it holds right now
        for i, (key, text) in enumerate(KINDS):
            self.kind.setItemText(i, f"{text} ({len(every) if key is None else per_kind.get(key, 0)})")
        self.kind.blockSignals(False)
        per_path = {}
        for c in every:
            per_path[c.get("channel") or "online"] = per_path.get(c.get("channel") or "online", 0) + 1
        self.path.blockSignals(True)
        for i, (key, text) in enumerate(PATHS):
            self.path.setItemText(i, f"{text} ({len(every) if key is None else per_path.get(key, 0)})")
        self.path.blockSignals(False)
        path = self.path.currentData()
        ready = [c for c in every if execution.target(c)[0] in allowed and (path is None or (c.get("channel") or "online") == path)]
        # the categories of what is ready now, with how many each has; the chosen one stays chosen while it exists
        counts = {}
        for c in ready:
            name = c.get("category") or ""
            counts[name] = counts.get(name, 0) + 1
        chosen_cat = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItem(f"همه‌ی دسته‌ها ({len(ready)})", None)
        for name in sorted(counts, key=lambda n: (-counts[n], n)):
            self.category.addItem(f"{name or 'بدون دسته'} ({counts[name]})", name)
        if chosen_cat is not None and chosen_cat not in counts:
            self.category.addItem(f"{chosen_cat or 'بدون دسته'} (0)", chosen_cat)
        at = self.category.findData(chosen_cat) if chosen_cat is not None else 0
        self.category.setCurrentIndex(max(0, at))
        self.category.blockSignals(False)
        picked = self.category.currentData()
        self._shown = ready if picked is None else [c for c in ready if (c.get("category") or "") == picked]
        ticked = self._checked
        current = self._current_smr()
        self.ready_list.blockSignals(True)
        self.ready_list.clear()
        for c in self._shown:
            act = ACTION_FA[execution.target(c)[0]]
            item = QListWidgetItem(f"{ltr(c['smr'])}  ·  {act}  ·  {'Online + Instore' if c['channel'] == 'both' else 'Online'}\n"
                                   f"{ltr(c.get('site') or '')}\n{c.get('category') or 'بدون دسته'}  ·  {jalali.ago(c.get('updated_at'))}")
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
        else:
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
