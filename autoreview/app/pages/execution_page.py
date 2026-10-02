"""Execution: applying decisions in NBO by hand or automatically (live mode, owner only), what is ready, and what NBO really
shows afterwards. The screen flow is the team's old one (app/nbo_actor.py), made safe."""
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QListWidget, QListWidgetItem, QMessageBox, QTableWidget,
                               QTableWidgetItem)

from ... import execution, jalali, settings, workflow
from ..theme import C
from ..widgets import Card, StatTile, Switch, button, label, ltr
from ..execution_control import ACTION_FA
from .common import ScrollPage


class ExecutionPage(ScrollPage):
    title = "اعمال در NBO"
    subtitle = "مرحله‌ی 5 از 5: تأیید / اصلاح / لغو در خود NBO — همه با هم یا یکی‌یکی، با تمرین قبلش"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell, self.control = session, shell, shell.execution

        # one clear place for "all at once" (owner 2026-10-02): rehearse them all, apply them all, stop
        allc = Card()
        self.b_rehearse_all = button("تمرین همه (بدون تغییر در NBO)", None, "play")
        self.b_rehearse_all.clicked.connect(self._rehearse_all)
        self.b_apply_all = button("اعمال همه در NBO", "primary", "check")
        self.b_apply_all.clicked.connect(self._apply_all)
        self.b_stop_all = button("توقف", "danger", "stop", icon_color=C["danger"])
        self.b_stop_all.clicked.connect(self.control.stop_all)
        allc.header("همه با هم", "آماده‌ها به ترتیب، قدیمی‌ترها اول — فقط نوع‌هایی که پایین‌تر روشن است",
                    [self.b_stop_all, self.b_rehearse_all, self.b_apply_all])
        self.all_count = label("", "h2")
        allc.lay.addWidget(self.all_count)
        self.batch_line = label("", "h3", wrap=True)
        allc.lay.addWidget(self.batch_line)
        allc.lay.addWidget(label("«اعمال همه» با حساب NBO که داخل اپ وارد شده، یکی‌یکی همان مراحل NBO را می‌زند "
                                 "(Assign to me ← Change Status ← گزینه ← دلیل)، با اولین خطا می‌ایستد، و بعد از اولین تغییر "
                                 "واقعی هم می‌ایستد تا در NBO نگاهش کنی؛ دوباره بزنی بقیه را ادامه می‌دهد. فقط مدیر. "
                                 "«تمرین همه» همه‌ی مراحل را تا قبل از Assign و ثبت نهایی می‌رود و چیزی را در NBO عوض نمی‌کند.",
                                 "caption", wrap=True))
        self.body.addWidget(allc)

        mode = Card()
        mode.header("چه چیزهایی اعمال شود", "فقط آنلاین: تأیید Online کافی است • آنلاین + حضوری: تأیید هر دو تیم لازم است")
        r = QHBoxLayout()
        self.switch = Switch()                      # live mode; driven by «اعمال همه» / «توقف» above, kept for its state
        self.switch.toggled.connect(self._toggled)
        self.switch.setVisible(False)
        r.addWidget(self.switch)
        self.mode_text = label("", "h3")
        r.addWidget(self.mode_text)
        r.addStretch(1)
        mode.lay.addLayout(r)
        self.readiness = label("", "muted", wrap=True)
        mode.lay.addWidget(self.readiness)
        auto = QHBoxLayout()
        auto.addWidget(label("در «اعمال همه» این نوع‌ها انجام شود:", "muted"))
        self.auto_switches = {}
        for key, text in (("APPROVE", "تأیید"), ("EDIT", "نیاز به اصلاح"), ("CANCEL", "لغو")):
            sw = Switch()
            sw.toggled.connect(self._auto_changed)
            self.auto_switches[key] = sw
            auto.addWidget(sw)
            auto.addWidget(label(text, "muted"))
            auto.addSpacing(10)
        auto.addStretch(1)
        mode.lay.addLayout(auto)
        self.stopped = label("", "h3", wrap=True)
        mode.lay.addWidget(self.stopped)
        mode.lay.addWidget(label("«اعمال همه» با هر بار باز کردن برنامه خاموش است و فقط مدیر (mohammadreza.vahab) روشنش می‌کند.",
                                 "caption", wrap=True))
        self.body.addWidget(mode)

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

        ready = Card()
        b_apply = button("اعمال در NBO", "primary", "check")
        b_apply.clicked.connect(lambda: self._apply_selected(False))
        b_try = button("تمرین (بدون ثبت)", None, "play")
        b_try.clicked.connect(lambda: self._apply_selected(True))
        b_open = button("باز کردن در NBO", None, "nbo")
        b_open.clicked.connect(self._open_selected)
        ready.header("آماده‌ی اقدام در NBO", "تأییدها، و اصلاح/لغوهایی که دلیل NBO دارند — دستی: یکی را انتخاب کن و «اعمال در NBO»",
                     [b_try, b_apply, b_open])
        self.ready_list = QListWidget()
        self.ready_list.setMinimumHeight(180)
        self.ready_list.itemDoubleClicked.connect(lambda _it: self._open_selected())
        ready.lay.addWidget(self.ready_list)
        self.body.addWidget(ready)

        ledger = Card()
        ledger.header("دفتر اجرا", "هر تغییر وضعیت، با نسخه‌ی پرونده — همین جدول در تب «Execution» شیت خودت هم هست")
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

    def _rehearse_all(self):
        n = self.control.rehearse_all()
        if n == 'busy':
            QMessageBox.information(self, "تمرین همه", "یک اجرای دیگر در جریان است؛ اول تمام شود یا «توقف» را بزن.")
        elif not n:
            QMessageBox.information(self, "تمرین همه", "الان درخواستی آماده‌ی اعمال نیست.")
        self.render()

    def _apply_all(self):
        todo = self.control.ready_cases()
        if not todo:
            QMessageBox.information(self, "اعمال همه", "الان درخواستی آماده‌ی اعمال نیست.")
            return
        kinds = {}
        for c in todo:
            kinds[execution.target(c)[0]] = kinds.get(execution.target(c)[0], 0) + 1
        parts = "، ".join(f"{n} {ACTION_FA[k]}" for k, n in kinds.items())
        if QMessageBox.question(self, "اعمال همه در NBO",
                                f"{len(todo)} درخواست ({parts}) با حساب NBO که داخل اپ وارد شده، یکی‌یکی در NBO ثبت می‌شود.\n"
                                "با اولین خطا می‌ایستد و بعد از اولین تغییر واقعی هم می‌ایستد تا در NBO نگاهش کنی. شروع کنم؟") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.control.apply_all()
        except (PermissionError, ValueError) as e:
            QMessageBox.information(self, "اعمال همه", str(e))
        self.render()

    def _toggled(self, on):
        try:
            self.control.set_live(on)
        except (PermissionError, ValueError) as e:
            QMessageBox.information(self, "حالت واقعی", str(e))
        self.render()

    def render(self):
        live = self.control.mode.live
        self.switch.blockSignals(True)
        self.switch.setChecked(live)
        self.switch.setEnabled(self.control.owner())
        self.switch.blockSignals(False)
        self.mode_text.setText("«اعمال همه» در جریان است — اپ خودش در NBO ثبت می‌کند" if live
                               else "آزمایشی — تا «اعمال همه» را نزنی، اپ چیزی در NBO تغییر نمی‌دهد")
        todo = self.control.ready_cases()
        kinds = {}
        for c in todo:
            kinds[execution.target(c)[0]] = kinds.get(execution.target(c)[0], 0) + 1
        self.all_count.setText(f"{len(todo)} آماده‌ی اعمال" + (" — " + "، ".join(f"{n} {ACTION_FA[k]}" for k, n in kinds.items())
                                                              if kinds else ""))
        busy = bool(self.control.batch) or live
        owner = self.control.owner()
        self.b_apply_all.setEnabled(owner and not busy and bool(todo))
        self.b_rehearse_all.setEnabled(not busy and bool(todo))
        self.b_stop_all.setEnabled(busy)
        self.batch_line.setText(self.control.batch_text() or ("" if not live else "در حال اعمال، یکی‌یکی…"))
        self.readiness.setText("یکی‌یکی: درخواست را از فهرست پایین انتخاب کن و «اعمال در NBO». همه با هم: «اعمال همه» در بالا (فقط مدیر). "
                               "اپ همان مراحل اسکریپت قدیمی را در صفحه‌ی NBO می‌زند (Assign to me ← Change Status ← گزینه ← دلیل)، "
                               "ولی دکمه‌ی غیرفعال را به‌زور نمی‌زند، دلیل را دقیقاً از فهرست خود NBO انتخاب می‌کند و اگر صفحه مال همان "
                               "درخواست نباشد کاری نمی‌کند.")
        self.stopped.setText(self.control.last_stop)
        allowed = self.control.auto_actions()
        for key, sw in self.auto_switches.items():
            sw.blockSignals(True)
            sw.setChecked(key in allowed)
            sw.setEnabled(self.control.owner())
            sw.blockSignals(False)
        db = self.session.db()
        try:
            cases = workflow.cases(db)
            records = execution.records(db, limit=300)
        finally:
            db.close()
        by_state = {}
        for c in cases:
            by_state.setdefault(c.get("state") or workflow.state(c), []).append(c)
        ready = sorted(by_state.get("READY", []), key=lambda c: c.get("updated_at") or "")
        actionable = sorted([c for c in cases if execution.target(c)], key=lambda c: c.get("updated_at") or "")
        self.t_ready.set_value(len(ready))
        self.t_instore.set_value(len(by_state.get("WAIT_INSTORE", [])))
        self.t_done.set_value(len(by_state.get("DONE_APPROVED", [])))
        self.t_outside.set_value(sum(1 for x in records if x["state"] == "APPROVED_IN_NBO" and "بدون" in x["detail"]))
        self.ready_list.clear()
        for c in actionable:
            act = ACTION_FA[execution.target(c)[0]]
            item = QListWidgetItem(f"{act}   •   {ltr(c['smr'])}   •   {'Online + Instore' if c['channel'] == 'both' else 'Online'}   •   "
                                   f"{ltr(c.get('site') or '')}   •   از {jalali.ago(c.get('updated_at'))}")
            item.setData(Qt.ItemDataRole.UserRole, c["smr"])
            self.ready_list.addItem(item)
        if not actionable:
            item = QListWidgetItem("فعلاً موردی آماده‌ی تأیید نیست.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.ready_list.addItem(item)
        self.table.setRowCount(len(records))
        for i, x in enumerate(records):
            for j, value in enumerate((x["smr"], x["label"], jalali.jdatetime(x["updated_at"]) if x["updated_at"] else "", x["detail"])):
                self.table.setItem(i, j, QTableWidgetItem(html.unescape(str(value))))

    def _auto_changed(self, _on):
        over = settings.load_user()
        over.setdefault("rules", {})["execution.auto_actions"] = [k for k, sw in self.auto_switches.items() if sw.isChecked()]
        settings.save_user(over)

    def _selected_case(self):
        item = self.ready_list.currentItem()
        smr = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not smr:
            return None
        db = self.session.db()
        try:
            return workflow.get(db, smr)
        finally:
            db.close()

    def _apply_selected(self, rehearsal):
        case = self._selected_case()
        if not case:
            QMessageBox.information(self, "NBO", "اول یک درخواست را از فهرست انتخاب کن.")
            return
        apply_case(self, self.control, case, rehearsal)

    def _open_selected(self):
        item = self.ready_list.currentItem()
        smr = item.data(Qt.ItemDataRole.UserRole) if item else None
        if smr:
            self.shell.open_in_nbo(smr)


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
