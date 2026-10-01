"""Visible session mode and durable approval preview, without pretending a live adapter is ready."""
from PySide6.QtCore import QObject, QTimer, Signal

from .. import execution, workflow, workspace


class ExecutionControl(QObject):
    changed = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.mode = execution.Mode()
        # A discovered URL is not a verified production adapter. Fail closed.
        self.readiness = 'اتصال اجرای NBO هنوز با خواندن وضعیت قبل و بعد و کنترل نسخه اعتبارسنجی نشده است'
        self.summary = 'آزمایشی — هیچ درخواست تغییری به NBO ارسال نمی‌شود'
        db = session.db()
        try:
            execution.ensure(db)
        finally:
            db.close()
        self.timer = QTimer(self)
        self.timer.setInterval(10_000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        session.data_changed.connect(self.refresh)

    def owner(self):
        try:
            return workspace.username(self.session.profile.get('username')) == workspace.ADMIN
        except ValueError:
            return False

    def set_live(self, enabled):
        db = self.session.db()
        try:
            self.mode.set(enabled, self.session.profile.get('username'), db, self.readiness)
        finally:
            db.close()
        self.refresh()

    def refresh(self):
        db = self.session.db()
        try:
            cases = workflow.cases(db)
            ready = [case for case in cases if workflow.state(case) == 'READY']
            prior = {(r['smr'], r['revision']) for r in execution.records(db)}
            for case in ready:
                if (case['smr'], case['revision']) not in prior:
                    execution.record(db, case, 'PREVIEW', execution.eligibility(case) or 'تأیید تیم‌های لازم تکمیل است')
            waiting = sum(workflow.state(c) == 'WAIT_INSTORE' for c in cases)
            self.summary = f'آزمایشی — {len(ready)} آمادهٔ تأیید، {waiting} منتظر Instore؛ هیچ تغییری در NBO اعمال نشده'
        finally:
            db.close()
        self.changed.emit()

    def stop(self):
        self.mode.live = False
        self.timer.stop()
