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
    title = "کنترل اجرا"
    subtitle = "آماده‌ی تأیید در NBO، تأییدشده‌ها، و حالت آزمایشی / واقعی"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell, self.control = session, shell, shell.execution

        mode = Card()
        mode.header("حالت اجرا", "فقط آنلاین: تأیید Online کافی است • آنلاین + حضوری: تأیید هر دو تیم لازم است")
        r = QHBoxLayout()
        self.switch = Switch()
        self.switch.toggled.connect(self._toggled)
        r.addWidget(self.switch)
        self.mode_text = label("", "h3")
        r.addWidget(self.mode_text)
        r.addStretch(1)
        mode.lay.addLayout(r)
        self.readiness = label("", "muted", wrap=True)
        mode.lay.addWidget(self.readiness)
        auto = QHBoxLayout()
        auto.addWidget(label("در حالت واقعی، خودکار انجام شود:", "muted"))
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
        mode.lay.addWidget(label("حالت واقعی با هر بار باز کردن برنامه دوباره آزمایشی می‌شود و فقط مدیر (mohammadreza.vahab) روشنش می‌کند. "
                                 "یکی‌یکی جلو می‌رود، با اولین مشکل می‌ایستد، و بعد از اولین تغییر واقعی هم خودش می‌ایستد تا در NBO نگاهش کنی.",
                                 "caption", wrap=True))
        self.body.addWidget(mode)

        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.t_ready = StatTile("آماده‌ی تأیید در NBO", C["accent_text"], C["accent_soft"], "check")
        self.t_instore = StatTile("منتظر نظر Instore", C["info"], C["info_soft"], "clock")
        self.t_done = StatTile("تأییدشده در NBO", C["approve"], C["approve_soft"], "shield")
        self.t_outside = StatTile("تأیید بدون روند کامل", C["warn"], C["warn_soft"], "alert")
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

        crm = Card(soft=True)
        crm.header("تأیید در CRM — مرحله‌ی بعد", "فیلد new_merchantstatus = ۱۰۰۰۰۰۰۰۵ «درخواست تایید شده است»")
        crm.lay.addWidget(label("فیلد و مقدار ثبت شده؛ اجرای خودکار CRM خاموش است تا دسترسی تأیید CRM بگیری و مسیر رسمی‌اش "
                                "بررسی شود. تأیید NBO به معنی تأیید CRM نیست.", "muted", wrap=True))
        self.body.addWidget(crm)
        self.body.addStretch(1)
        self.control.changed.connect(self._render_if_visible)

    def _render_if_visible(self):
        if self.isVisible():
            self.render()

    def on_show(self):
        self.control.refresh()
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
        self.mode_text.setText("واقعی — تأیید خودکار در NBO" if live else "آزمایشی — اپ چیزی در NBO تغییر نمی‌دهد")
        self.readiness.setText("دستی: هر کس با حساب NBO خودش، روی یک درخواست. خودکار: فقط مدیر، با سوییچ بالا. "
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
            item = QListWidgetItem(f"{act}   •   {ltr(c['smr'])}   •   {'آنلاین + حضوری' if c['channel'] == 'both' else 'فقط آنلاین'}   •   "
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
