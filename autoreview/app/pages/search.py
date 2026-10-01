"""Lookup in the reference data - the question the old Main sheet answered: 'has this website / merchant had a request
before, and where does it stand?' (NBO itself cannot search by website)."""
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ... import jalali, reference
from ..theme import C
from ..widgets import Card, SearchBox, label, ltr, num, toast
from .common import nbo_status_fa

HEAD = ("منبع", "کد", "وضعیت", "وب‌سایت", "نام / برند", "تاریخ")


class SearchPage(QWidget):
    title = "جستجو در مرجع"
    subtitle = "وب‌سایت، کد SMR / MRG یا نام را بنویس — همه‌ی درخواست‌های NBO و CRM با همان سایت یا نام"

    def __init__(self, session, shell):
        super().__init__()
        self.setObjectName("page")
        self.session = session
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)
        card = Card()
        row = QHBoxLayout()
        self.box = SearchBox("مثلاً shop.ir یا SMR-12345678 یا نام صاحب حساب")
        self.box.setMinimumHeight(40)
        row.addWidget(self.box, 1)
        card.lay.addLayout(row)
        self.meta = label("", "caption")
        card.lay.addWidget(self.meta)
        v.addWidget(card)
        self.count = label("", "muted")
        v.addWidget(self.count)
        self.table = QTableWidget(0, len(HEAD))
        self.table.setHorizontalHeaderLabels(HEAD)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((70, 140, 190, 230, 230)):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.cellDoubleClicked.connect(self._copy)
        v.addWidget(self.table, 1)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._search)
        self.box.textChanged.connect(lambda _: self._debounce.start())
        self.box.returnPressed.connect(self._search)

    def on_show(self):
        db = self.session.db()
        try:
            m_nbo, m_crm = reference.meta(db, "nbo"), reference.meta(db, "crm")
        finally:
            db.close()
        part = []
        part.append(f"NBO: {num(m_nbo['rows'])} درخواست ({jalali.ago(m_nbo['loaded_at'])})" if m_nbo else "NBO: بارگذاری نشده")
        part.append(f"CRM: {num(m_crm['rows'])} ثبت‌نام ({jalali.ago(m_crm['loaded_at'])})" if m_crm else "CRM: بارگذاری نشده")
        self.meta.setText("   •   ".join(part) + "   —   روی هر ردیف دو بار بزن تا کدش کپی شود")
        self.box.setFocus()

    def _search(self):
        q = self.box.text().strip()
        db = self.session.db()
        try:
            hits = reference.search(db, q)
        finally:
            db.close()
        self.table.setRowCount(len(hits))
        for i, h in enumerate(hits):
            if h["source"] == "NBO":
                vals = ("NBO", h["smr"], nbo_status_fa(h["status"]), h["site"], h.get("account_holder") or h.get("brand_fa") or "",
                        jalali.fa_digits(h.get("created_at") or ""))
            else:
                when = jalali.jdate(h["created_on"]) if h.get("created_on") else ""
                vals = ("CRM", h["caseid"], h["status"], h["site"], h.get("brand") or "", when)
            for j, val in enumerate(vals):
                it = QTableWidgetItem(ltr(val) if j in (1, 3) else str(val or ""))
                if j == 0:
                    it.setForeground(QColor(C["accent_text"] if val == "NBO" else C["info"]))
                self.table.setItem(i, j, it)
        if not q:
            self.count.setText("")
        elif hits:
            self.count.setText(f"{num(len(hits))} نتیجه")
        else:
            self.count.setText("چیزی پیدا نشد — اگر داده‌ها قدیمی‌اند، از صفحه‌ی «بررسی» به‌روزشان کن.")

    def _copy(self, row, _col):
        it = self.table.item(row, 1)
        if it:
            code = it.text().strip("⁦⁩")
            QGuiApplication.clipboard().setText(code)
            toast(self.window(), f"{code} کپی شد")
