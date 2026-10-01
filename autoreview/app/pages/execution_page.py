"""Execution: which requests are ready to be approved in NBO, which really were, and the dry-run / live mode.

Live approval needs NBO's official approval API, which is not connected yet; until then a person approves the 'ready' ones
in the embedded NBO page and the app records what NBO then shows. The owner alone may ever switch the mode."""
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QListWidget, QListWidgetItem, QMessageBox, QTableWidget,
                               QTableWidgetItem)

from ... import execution, jalali, workflow
from ..theme import C
from ..widgets import Card, StatTile, Switch, button, label, ltr
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
        mode.lay.addWidget(label("حالت واقعی با هر بار باز کردن برنامه دوباره آزمایشی می‌شود؛ فقط مدیر (mohammadreza.vahab) می‌تواند عوضش کند.",
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
        b_open = button("باز کردن در NBO", "primary", "nbo")
        b_open.clicked.connect(self._open_selected)
        ready.header("آماده‌ی تأیید در NBO", "یکی را انتخاب کن و در NBO تأیید بزن؛ با دریافت بعدی NBO، اینجا «تأیید شد» می‌خورد", b_open)
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
        self.readiness.setText(self.control.readiness)
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
        self.t_ready.set_value(len(ready))
        self.t_instore.set_value(len(by_state.get("WAIT_INSTORE", [])))
        self.t_done.set_value(len(by_state.get("DONE_APPROVED", [])))
        self.t_outside.set_value(sum(1 for x in records if x["state"] == "APPROVED_IN_NBO" and "بدون" in x["detail"]))
        self.ready_list.clear()
        for c in ready:
            item = QListWidgetItem(f"{ltr(c['smr'])}   •   {'آنلاین + حضوری' if c['channel'] == 'both' else 'فقط آنلاین'}   •   "
                                   f"{ltr(c.get('site') or '')}   •   آماده از {jalali.ago(c.get('updated_at'))}")
            item.setData(Qt.ItemDataRole.UserRole, c["smr"])
            self.ready_list.addItem(item)
        if not ready:
            item = QListWidgetItem("فعلاً موردی آماده‌ی تأیید نیست.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.ready_list.addItem(item)
        self.table.setRowCount(len(records))
        for i, x in enumerate(records):
            for j, value in enumerate((x["smr"], x["label"], jalali.jdatetime(x["updated_at"]) if x["updated_at"] else "", x["detail"])):
                self.table.setItem(i, j, QTableWidgetItem(html.unescape(str(value))))

    def _open_selected(self):
        item = self.ready_list.currentItem()
        smr = item.data(Qt.ItemDataRole.UserRole) if item else None
        if smr:
            self.shell.open_in_nbo(smr)
