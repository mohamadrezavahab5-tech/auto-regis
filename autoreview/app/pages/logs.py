"""Logs: what the app did, live - sign-ins, data loads (rows, seconds), every decision, sheet writes, errors with details.
Never passwords, tokens or cookies."""
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ... import logs
from ...paths import logs_dir
from ..theme import C
from ..widgets import SearchBox, button, label, num, toast

LEVEL_FA = {"INFO": "اطلاعات", "WARNING": "هشدار", "ERROR": "خطا", "DEBUG": "جزئیات", "CRITICAL": "خطا"}
LEVEL_COLOR = {"WARNING": C["warn"], "ERROR": C["danger"], "CRITICAL": C["danger"]}


class _Bridge(QObject):
    item = Signal(dict)


class LogsPage(QWidget):
    title = "لاگ‌ها"
    subtitle = "هر کاری که برنامه انجام داده، با زمان — زنده"

    def __init__(self, session, shell):
        super().__init__()
        self.setObjectName("page")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        bar = QHBoxLayout()
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
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(("زمان", "سطح", "بخش", "پیام"))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        for i, w in enumerate((150, 80, 90)):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        v.addWidget(self.table, 1)
        self.hint = label("", "caption")
        v.addWidget(self.hint)
        self._bridge = _Bridge()
        self._bridge.item.connect(self._append)
        logs.subscribe(self._bridge.item.emit)          # log calls come from any thread; the signal brings them here

    def on_show(self):
        self._reload()

    def _match(self, it):
        lvl = self.level.currentData()
        if lvl == "WARNING" and it["level"] not in ("WARNING", "ERROR", "CRITICAL"):
            return False
        if lvl == "ERROR" and it["level"] not in ("ERROR", "CRITICAL"):
            return False
        q = self.search.text().strip().lower()
        return not q or q in it["message"].lower() or q in it["source"].lower()

    def _reload(self, *_):
        items = [it for it in list(logs.RING) if self._match(it)][-3000:]
        self.table.setRowCount(0)
        for it in reversed(items):
            self._add_row(it, at_end=True)
        self.hint.setText(f"{num(len(items))} مورد (آخرین ۵٬۰۰۰ مورد در حافظه؛ همه در پوشه‌ی لاگ‌ها)")

    def _append(self, it):
        if self.isVisible() and self._match(it):
            self._add_row(it, at_end=False)

    def _add_row(self, it, at_end):
        r = self.table.rowCount() if at_end else 0
        self.table.insertRow(r)
        vals = (it["ts"], LEVEL_FA.get(it["level"], it["level"]), it["source"], it["message"].splitlines()[0][:400])
        for j, val in enumerate(vals):
            cell = QTableWidgetItem(val)
            if j == 3:
                cell.setToolTip(it["message"][:3000])
            if it["level"] in LEVEL_COLOR:
                cell.setForeground(QColor(LEVEL_COLOR[it["level"]]))
            self.table.setItem(r, j, cell)
        if self.table.rowCount() > 3000:
            self.table.removeRow(self.table.rowCount() - 1)

    def _copy(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        text = "\n".join("\t".join(self.table.item(r, c).text() for c in range(4)) for r in rows)
        if text:
            QGuiApplication.clipboard().setText(text)
            toast(self.window(), "کپی شد")
