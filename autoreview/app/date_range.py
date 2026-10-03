"""Compact date filter with a real Jalali calendar and inclusive local-day bounds."""
from PySide6.QtCore import QCalendar, QDate, QLocale, Signal
from PySide6.QtWidgets import QComboBox, QDateEdit, QHBoxLayout, QWidget

from .widgets import label


class DateRange(QWidget):
    changed = Signal()
    PERIODS = (('all', 'همهٔ تاریخ‌ها'), ('today', 'امروز'), ('2', 'دیروز و امروز'),
               ('7', '۷ روز اخیر'), ('30', '۳۰ روز اخیر'), ('custom', 'بازهٔ دلخواه'))

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.period = QComboBox()
        self.period.setMinimumWidth(135)
        self.period.setAccessibleName('بازهٔ تاریخ تصمیم')
        for key, text in self.PERIODS:
            self.period.addItem(text, key)
        row.addWidget(self.period)
        self.custom = QWidget()
        dates = QHBoxLayout(self.custom)
        dates.setContentsMargins(0, 0, 0, 0)
        self.start, self.end = QDateEdit(), QDateEdit()
        for field, caption, value in ((self.start, 'از', QDate.currentDate().addDays(-6)),
                                       (self.end, 'تا', QDate.currentDate())):
            field.setCalendar(QCalendar(QCalendar.System.Jalali))
            field.setLocale(QLocale(QLocale.Language.Persian, QLocale.Country.Iran))
            field.setDisplayFormat('yyyy/MM/dd')
            field.setCalendarPopup(True)
            field.setDate(value)
            field.setMinimumWidth(135)
            field.setAccessibleName(caption + ' تاریخ شمسی')
            field.setKeyboardTracking(False)
            dates.addWidget(label(caption, 'muted'))
            dates.addWidget(field)
            field.dateChanged.connect(lambda _date: self.changed.emit())
        row.addWidget(self.custom)
        row.addStretch(1)
        self.custom.hide()
        self.period.currentIndexChanged.connect(self._period_changed)

    def _period_changed(self, _index):
        self.custom.setVisible(self.period.currentData() == 'custom')
        self.changed.emit()

    def bounds(self):
        key = self.period.currentData()
        today = QDate.currentDate()
        if key == 'all':
            return None, None
        if key == 'today':
            return today.toPython(), today.toPython()
        if key in ('2', '7', '30'):
            return today.addDays(1 - int(key)).toPython(), today.toPython()
        if self.start.date() > self.end.date():
            return None
        return self.start.date().toPython(), self.end.date().toPython()

    def description(self):
        if self.period.currentData() != 'custom':
            return self.period.currentText()
        return self.start.text() + ' تا ' + self.end.text()
