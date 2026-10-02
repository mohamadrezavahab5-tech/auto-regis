"""Refresh sources without file picking, using each person's existing sessions."""
import time

from PySide6.QtCore import QObject, QTimer, Signal

from .. import crm_sync, logs, reference, settings, sheets
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
        QTimer.singleShot(60_000, self.tick)         # give a fresh start (and NBO) a minute

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
        self._nbo_round(s, nbo_wait)

    # ---- NBO, gently (live 2026-10-02: the whole export timed out at NBO's gateway, and rapid retries / back-to-back
    # exports were answered 429 'too many requests'). The work queue every round; every status only every few hours.
    QUEUE = ("PENDING", "COMMERCIAL_IN_PROGRESS")
    GAP_MS = 20_000                    # between two exports of one round
    FAILED_WAIT = 10 * 60              # after an error
    LIMITED_WAIT = 20 * 60             # after NBO said 429

    def _full_due(self, s):
        hours = int(settings.load_rules().get('automation', {}).get('nbo_full_hours', 6))
        db = s.db()
        try:
            last = reference.meta(db, "nbo_full")
        finally:
            db.close()
        return last is None or reference.is_stale(last, hours)

    def _nbo_round(self, s, nbo_wait):
        self.nbo_active = True
        s._busy('nbo', True)
        full = self._full_due(s)
        order = list(self.QUEUE) + ([x for x in ALL_NBO_STATUSES if x not in self.QUEUE] if full else [])
        self.update('در حال دریافت خودکار NBO…')
        started_at = time.monotonic()
        log.info("automatic NBO export started (%s: %d statuses, one at a time)", "full" if full else "queue", len(order))

        def finish(error=None, wait=None):
            self.nbo_active = False
            self.next_nbo = time.monotonic() + (wait if wait else (self.FAILED_WAIT if error else nbo_wait))
            s._busy('nbo', False)
            if error:
                self.update({'login': 'NBO: ورود لازم است (در صفحه‌ی NBO وارد شو)',
                             'limited': 'NBO گفت درخواست زیاد است؛ ۲۰ دقیقه بعد دوباره'}.get(error, 'NBO این بار جواب نداد؛ ۱۰ دقیقه بعد دوباره'))
                return
            s.sync_workflow()
            started = s.autopilot_run()
            self.update({"off": "NBO خودکار به‌روز شد (خلبان خودکار خاموش است)", "busy": "NBO به‌روز شد؛ یک بررسی در جریان است",
                         "no_crm": "NBO به‌روز شد؛ خلبان خودکار منتظر داده‌ی CRM است", 0: "NBO به‌روز شد؛ درخواست جدیدی نبود"}.get(
                started, f"NBO به‌روز شد؛ خلبان خودکار {started} درخواست جدید را بررسی می‌کند"))

        def complete(parts, done_statuses, refused, limited):
            took = time.monotonic() - started_at
            if refused:
                log.warning("NBO export: no data this round for %s", ", ".join(refused))
            if not parts:
                finish('limited' if limited else 'export', self.LIMITED_WAIT if limited else None)
                return
            whole = full and len(done_statuses) == len(order)
            log.info("automatic NBO export: %d of %d statuses in %.0fs%s", len(done_statuses), len(order), took,
                     "" if whole else " (other statuses keep their last rows)")

            def imported(_n):
                if whole:
                    db = s.db()
                    try:
                        with db:
                            reference._set_meta(db, "nbo_full", "auto", 0)
                    finally:
                        db.close()
                finish(None, self.LIMITED_WAIT if limited else None)

            def import_failed(e):
                log.warning("automatic NBO export could not be imported: %s", e)
                finish('import')
            s.import_nbo_parts(parts, imported, import_failed, only_statuses=None if whole else done_statuses)

        def step(todo, parts, done_statuses, refused, retried):
            if not self.enabled:
                return
            if not todo:
                return complete(parts, done_statuses, refused, False)
            status = todo[0]

            def got(data, error):
                if error in ("http_401", "login"):
                    log.warning("automatic NBO export: not signed in to NBO")
                    finish('login')
                    return
                if error == "http_429":                     # NBO's rate limit: stop now, keep what arrived
                    refused.append(f"{status} (http_429)")
                    return complete(parts, done_statuses, refused, True)
                if error and status not in retried and (error == "timeout" or error.startswith("http_5")):
                    retried.add(status)
                    log.info("NBO export of %s: %s, trying once more", status, error)
                    QTimer.singleShot(self.GAP_MS, lambda: self.client.export([status], got))
                    return
                if error:
                    refused.append(f"{status} ({error})")
                else:
                    parts.append(data)
                    done_statuses.append(status)
                if todo[1:]:
                    QTimer.singleShot(self.GAP_MS, lambda: step(todo[1:], parts, done_statuses, refused, retried))
                else:
                    step([], parts, done_statuses, refused, retried)
            self.client.export([status], got)
        step(order, [], [], [], set())

    def stop(self):
        self.enabled = False
        self.timer.stop()
