"""Refresh sources without file picking, using each person's existing sessions."""
import time

from PySide6.QtCore import QObject, QTimer, Signal

from .. import crm_sync, logs, settings, sheets
from .web import ALL_NBO_STATUSES

log = logs.get("auto")


class AutomaticSources(QObject):
    changed = Signal()

    def __init__(self, session, nbo_client, parent=None):
        super().__init__(parent)
        self.session, self.client = session, nbo_client
        self.status = 'دریافت خودکار آماده است'
        self.next_crm = self.next_nbo = 0.0
        self.crm_active = self.nbo_active = False
        self.enabled = True
        self.timer = QTimer(self)
        self.timer.setInterval(30_000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        QTimer.singleShot(2000, self.tick)

    def update(self, text):
        self.status = text
        self.changed.emit()

    def tick(self):
        if not self.enabled: return
        s = self.session
        if sheets.load().get('auth_mode') == 'workspace' and s.profile.get('workspace_role') not in ('admin', 'online'):
            return
        # Do not replace source snapshots halfway through a review.
        if s.runner and s.runner.is_active(): return
        now = time.monotonic()
        every = settings.load_rules().get('automation', {})
        crm_wait, nbo_wait = 60 * int(every.get('crm_minutes', 5)), 60 * int(every.get('nbo_minutes', 15))
        if now >= self.next_crm and not self.crm_active and 'crm' not in s.busy:
            if not crm_sync.have_credentials():
                self.update('برای دریافت خودکار CRM، یک بار وارد حساب خودت شو')
                self.next_crm = now + 60
            else:
                self.crm_active = True
                def done(_result):
                    self.crm_active = False; self.next_crm = time.monotonic() + crm_wait
                    self.update('CRM خودکار به‌روز شد')
                def failed(_error):
                    self.crm_active = False; self.next_crm = time.monotonic()+120
                    self.update('CRM به‌روز نشد؛ اتصال یا ورود را بررسی کن')
                s.refresh_crm(on_done=done, on_fail=failed)
        if now < self.next_nbo or self.nbo_active or 'nbo' in s.busy: return
        self.nbo_active = True
        s._busy('nbo', True)
        self.update('در حال دریافت خودکار NBO…')
        def finish(error=None):
            self.nbo_active = False; self.next_nbo = time.monotonic() + (120 if error else nbo_wait)
            s._busy('nbo',False)
            if error:
                self.update('NBO: ورود/OTP یا اتصال نیاز به بررسی دارد')
                return
            s.sync_workflow()
            started = s.autopilot_run()
            self.update({"off": "NBO خودکار به‌روز شد (خلبان خودکار خاموش است)", "busy": "NBO به‌روز شد؛ یک بررسی در جریان است",
                         "no_crm": "NBO به‌روز شد؛ خلبان خودکار منتظر داده‌ی CRM است", 0: "NBO به‌روز شد؛ درخواست جدیدی نبود"}.get(
                started, f"NBO به‌روز شد؛ خلبان خودکار {started} درخواست جدید را بررسی می‌کند"))
        started_at = time.monotonic()
        log.info("automatic NBO export started (statuses: all)")

        def exported(data, error):
            took = time.monotonic() - started_at
            if error:
                log.warning("automatic NBO export failed after %.0fs: %s", took, error)
                finish(error)
                return
            log.info("automatic NBO export received %d KB in %.0fs", len(data) // 1024, took)

            def import_failed(e):
                log.warning("automatic NBO export could not be imported: %s", e)
                finish('import')
            s.import_nbo_bytes(data, lambda _n: finish(), import_failed)
        self.client.export(ALL_NBO_STATUSES, exported)

    def stop(self):
        self.enabled = False
        self.timer.stop()
