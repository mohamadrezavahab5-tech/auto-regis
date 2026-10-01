"""Visible session mode and durable approval preview, without pretending a live adapter is ready."""
from PySide6.QtCore import QObject, QTimer, Signal

from .. import execution, workflow, workspace
from ..jalali import fa_digits


class ExecutionControl(QObject):
    changed = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.mode = execution.Mode()
        # No official NBO approval API is connected: live mode stays locked (fail closed) until one is.
        self.readiness = ('تأیید خودکار در NBO هنوز وصل نیست: برای وصل شدنش مستند رسمی API تأیید/تغییر وضعیت NBO لازم است. '
                          'تا آن موقع موارد «آماده» را خودت در NBO (داخل همین اپ) تأیید کن؛ اپ تأیید را از دریافت بعدی NBO '
                          'تشخیص می‌دهد و در شیتت ثبت می‌کند.')
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
            self.summary = (f"{'واقعی' if self.mode.live else 'آزمایشی'} — {fa_digits(len(ready))} آماده‌ی تأیید در NBO، "
                            f"{fa_digits(waiting)} منتظر نظر Instore؛ اپ خودش چیزی در NBO تغییر نمی‌دهد")
        finally:
            db.close()
        self.changed.emit()

    def stop(self):
        self.mode.live = False
        self.timer.stop()
