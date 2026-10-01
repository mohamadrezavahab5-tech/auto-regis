"""The owner controls execution mode; everyone can see actual execution receipts."""
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from ... import execution
from ..widgets import Card, label


class ExecutionPage(QWidget):
    title = 'کنترل اجرا'
    subtitle = 'حالت آزمایشی و واقعی، صف تأیید و نتیجهٔ ثبت‌شده در NBO'

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.control = session, shell.execution
        self.setObjectName('page')
        layout = QVBoxLayout(self)
        card = Card()
        card.header('حالت اجرای برنامه', 'Online: تأیید Online کافی است • مشترک: تأیید Online و Instore لازم است')
        row = QHBoxLayout()
        row.addWidget(label('حالت', 'h3'))
        self.mode = QComboBox()
        self.mode.addItem('آزمایشی — بدون تغییر NBO', False)
        self.mode.addItem('واقعی — تأیید خودکار NBO', True)
        self.mode.setEnabled(self.control.owner())
        self.mode.currentIndexChanged.connect(self.change_mode)
        row.addWidget(self.mode, 1)
        card.lay.addLayout(row)
        self.readiness = label('', 'muted', wrap=True)
        card.lay.addWidget(self.readiness)
        card.lay.addWidget(label('پس از باز کردن دوبارهٔ برنامه، حالت آزمایشی فعال است. '
            'توقف اجرا از ارسال بعدی جلوگیری می‌کند؛ درخواست ارسال‌شده باید تا دریافت نتیجه پیگیری شود.', 'caption', wrap=True))
        layout.addWidget(card)
        crm = Card(soft=True)
        crm.header('تأیید در CRM — برای مرحلهٔ بعد', 'وضعیت مقصد: درخواست تأیید شده است')
        crm.lay.addWidget(label('وضعیت مقصد ثبت شده است. اجرای خودکار CRM غیرفعال است؛ '
            'پس از دریافت دسترسی شما، مسیر رسمی تأیید و نتیجهٔ آن باید بررسی شود. '
            'تأیید NBO به معنی تأیید CRM نیست.', 'muted', wrap=True))
        layout.addWidget(crm)
        self.summary = label('', 'h3', wrap=True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['کد درخواست', 'نسخه', 'نتیجهٔ اجرا', 'زمان', 'توضیح'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, width in enumerate((135, 60, 230, 210)):
            self.table.setColumnWidth(i, width)
        layout.addWidget(self.table, 1)
        self.control.changed.connect(self.render)

    def on_show(self):
        self.control.refresh()

    def change_mode(self):
        desired = bool(self.mode.currentData())
        try:
            self.control.set_live(desired)
        except (PermissionError, ValueError) as exc:
            QMessageBox.warning(self, 'حالت واقعی فعال نشد', str(exc))
        self.render()

    def render(self):
        self.mode.blockSignals(True)
        self.mode.setCurrentIndex(1 if self.control.mode.live else 0)
        self.mode.blockSignals(False)
        self.readiness.setText('آمادگی اجرای واقعی: ' + (self.control.readiness or 'آماده'))
        self.summary.setText(self.control.summary)
        db = self.session.db()
        try:
            rows = execution.records(db)
        finally:
            db.close()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate((row['smr'], row['revision'], execution.LABELS[row['state']], row['updated_at'], row['detail'])):
                self.table.setItem(i, j, QTableWidgetItem(str(value)))
