"""Logs: what the app did, live - sign-ins, data loads (rows, seconds), every decision, sheet writes, errors with details.
Never passwords, tokens or cookies."""
import os
import re

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget, QPlainTextEdit, QSplitter)

from ... import activity, jalali, logs
from ...paths import logs_dir
from ..theme import C
from ..widgets import SearchBox, button, label, num, toast

LEVEL_FA = {"INFO": "اطلاعات", "WARNING": "هشدار", "ERROR": "خطا", "DEBUG": "جزئیات", "CRITICAL": "خطا"}
LEVEL_COLOR = {"WARNING": C["warn"], "ERROR": C["danger"], "CRITICAL": C["danger"]}


class _Bridge(QObject):
    item = Signal(dict)


class LogsPage(QWidget):
    title = "سابقه و جزئیات عملیات"
    subtitle = "سابقهٔ درخواست‌ها پس از بستن برنامه هم می‌ماند؛ یک ردیف را برای دیدن جزئیات انتخاب کن"

    def __init__(self, session, shell):
        super().__init__()
        self.setObjectName("page")
        self.shell, self.session = shell, session
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        bar = QHBoxLayout()
        self.source = QComboBox()
        self.source.addItem('سابقهٔ درخواست‌ها', 'audit')
        self.source.addItem('گزارش فنی این نشست', 'technical')
        self.source.currentIndexChanged.connect(self._reload)
        bar.addWidget(self.source)
        self.level = QComboBox()
        for text, key in (("همه", ""), ("هشدار و خطا", "WARNING"), ("فقط خطا", "ERROR")):
            self.level.addItem(text, key)
        self.level.currentIndexChanged.connect(self._reload)
        bar.addWidget(self.level)
        self.search = SearchBox("جستجو در لاگ‌ها…")
        self.search.textChanged.connect(self._reload)
        bar.addWidget(self.search, 1)
        b_copy = button("کپی ردیف‌های انتخاب‌شده", None, "copy")
        b_copy.clicked.connect(self._copy)
        b_folder = button("پوشه‌ی لاگ‌ها", None, "folder")
        b_folder.clicked.connect(lambda: os.startfile(str(logs_dir())))
        bar.addWidget(b_copy)
        bar.addWidget(b_folder)
        v.addLayout(bar)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(("زمان", "رویداد / نتیجه", "درخواست / بخش", "کاربر", "توضیح"))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        for i, w in enumerate((150, 240, 130, 150)):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.cellDoubleClicked.connect(self._open_request)
        self.table.itemSelectionChanged.connect(self._details)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText('یک ردیف را انتخاب کن؛ جزئیات کامل اینجا نمایش داده می‌شود. دوبار کلیک، پرونده را باز می‌کند.')
        split.addWidget(self.details)
        split.setSizes([480, 180])
        v.addWidget(split, 1)
        self.hint = label("", "caption")
        v.addWidget(self.hint)
        self._bridge = _Bridge()
        self._bridge.item.connect(self._append)
        logs.subscribe(self._bridge.item.emit)          # log calls come from any thread; the signal brings them here
        session.data_changed.connect(lambda: self._reload() if self.isVisible() else None)

    def on_show(self):
        self._reload()

    def _open_request(self, row, _col):
        """A log line about a request opens that request's file: what was checked, the decision, what happened since."""
        cell = self.table.item(row, 2)
        m = re.search(r"SMR-\d+", (cell.toolTip() or cell.text()) if cell else "")
        if m:
            from .case_view import open_case
            if open_case(self.shell, m.group(0)) is None:
                toast(self.window(), f"{m.group(0)} در داده‌ی فعلی NBO نیست", "warn")

    def _match(self, it):
        lvl = self.level.currentData()
        if lvl == "WARNING" and it["level"] not in ("WARNING", "ERROR", "CRITICAL"):
            return False
        if lvl == "ERROR" and it["level"] not in ("ERROR", "CRITICAL"):
            return False
        q = self.search.text().strip().lower()
        return not q or q in it["message"].lower() or q in it["source"].lower()

    def _reload(self, *_):
        if self.source.currentData() == 'audit':
            self.level.setEnabled(False)
            db = self.session.db()
            try:
                rows = activity.audit_rows(db, self.search.text())
            finally:
                db.close()
            self.table.setRowCount(0)
            for row in rows:
                detail = row['detail']
                self._add_row(dict(ts=jalali.jdatetime(row['ts']), level='INFO', title=row['title'],
                    source=row['smr'] or 'برنامه', actor=row['actor'],
                    message=str(detail.get('detail') or detail.get('note') or detail.get('action') or row['title']),
                    full=activity.detail_text(row)), at_end=True)
            self.hint.setText(f'{num(len(rows))} رویداد ذخیره‌شده؛ حداکثر ۱۰۰۰ نتیجهٔ آخر. جستجو در کد درخواست و متن جزئیات.')
            return
        self.level.setEnabled(True)
        items = [it for it in list(logs.RING) if self._match(it)][-3000:]
        self.table.setRowCount(0)
        for it in reversed(items):
            self._add_row(it, at_end=True)
        self.hint.setText(f"{num(len(items))} مورد (آخرین 5,000 مورد در حافظه؛ همه در پوشه‌ی لاگ‌ها)")

    def _append(self, it):
        if self.isVisible() and self.source.currentData() == 'technical' and self._match(it):
            self._add_row(it, at_end=False)

    def _add_row(self, it, at_end):
        r = self.table.rowCount() if at_end else 0
        self.table.insertRow(r)
        vals = (it["ts"], it.get('title') or LEVEL_FA.get(it["level"], it["level"]), it["source"],
                it.get('actor', '—'), (it["message"].splitlines() or [""])[0][:400])
        for j, val in enumerate(vals):
            cell = QTableWidgetItem(val)
            if j == 4:
                cell.setToolTip(it["message"][:3000])
                cell.setData(Qt.ItemDataRole.UserRole, it.get('full') or it['message'])
            if it["level"] in LEVEL_COLOR:
                cell.setForeground(QColor(LEVEL_COLOR[it["level"]]))
            self.table.setItem(r, j, cell)
        if self.table.rowCount() > 3000:
            self.table.removeRow(self.table.rowCount() - 1)

    def _details(self):
        cell = self.table.item(self.table.currentRow(), 4)
        if hasattr(self, 'details'):
            self.details.setPlainText(str(cell.data(Qt.ItemDataRole.UserRole) or cell.text()) if cell else '')

    def _copy(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        text = "\n\n".join(str(self.table.item(r, 4).data(Qt.ItemDataRole.UserRole) or self.table.item(r, 4).text()) for r in rows)
        if text:
            QGuiApplication.clipboard().setText(text)
            toast(self.window(), "کپی شد")
