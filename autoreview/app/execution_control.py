"""Applying decisions in NBO: by hand (one request, the person confirms and can watch NBO's screen) or automatically (live
mode, owner only, switched off again whenever the app restarts). Owner 2026-10-01: "both manual and automatic".

Automatic mode works one request at a time, only on cases whose verdicts are complete (approve: every needed team approved;
edit / cancel only when switched on in Execution), stops at the first problem, and after the very first real change it
stops by itself so the owner can look at NBO before letting it continue."""
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from .. import execution, settings, sheets, workflow, workspace
from ..jalali import fa_digits
from .nbo_actor import AFTER_SEND, NboActor
from .session import run_bg

ACTION_FA = {"APPROVE": "تأیید", "EDIT": "نیاز به اصلاح", "CANCEL": "لغو"}


class ExecutionControl(QObject):
    changed = Signal()
    notice = Signal(str)                  # something the owner must know (automatic run stopped, first change made ...)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.mode = execution.Mode()
        self.actor = NboActor(self)
        self.readiness = ''
        self.batch = None                     # a running "rehearse all": {todo, done, ok, assign, failed, stop}
        self.last_batch = ''
        self._refreshing = self._again = False
        self.summary = 'آزمایشی — اپ خودش چیزی در NBO تغییر نمی‌دهد'
        self.last_stop = ''
        self._first_run = False
        db = session.db()
        try:
            execution.ensure(db)
        finally:
            db.close()
        self.timer = QTimer(self)
        self.timer.setInterval(10_000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(45_000)
        self.auto_timer.timeout.connect(self._auto_tick)
        self.auto_timer.start()
        session.data_changed.connect(self.refresh)

    # ---- who may do what
    def owner(self):
        try:
            return workspace.username(self.session.profile.get('username')) == workspace.ADMIN
        except ValueError:
            return False

    def may_apply(self):
        """Applying by hand uses the person's OWN NBO account: the owner, or a colleague with the Online role / the owner's key."""
        from .. import google_credentials
        return self.owner() or self.session.profile.get('workspace_role') in ('admin', 'online') or google_credentials.available()

    @staticmethod
    def auto_actions():
        return set(settings.load_rules().get('execution', {}).get('auto_actions', ['APPROVE']))

    # ---- mode
    def set_live(self, enabled):
        db = self.session.db()
        try:
            self.mode.set(enabled, self.session.profile.get('username'), db, self.readiness)
            if enabled:
                self._first_run = not any(r['state'] == 'SENT' for r in execution.records(db))
                self.last_stop = ''
        finally:
            db.close()
        self.refresh()
        if enabled:
            QTimer.singleShot(1500, self._auto_tick)

    def _stop_live(self, why):
        self.mode.live = False
        self.last_stop = why
        self.notice.emit(why)
        self.refresh()

    # ---- one request
    def reason_label(self, case):
        tgt = execution.target(case)
        if not tgt or tgt[0] == 'APPROVE':
            return ''
        return sheets.nbo_labels().get(tgt[0].lower(), {}).get(tgt[1], '')

    def apply(self, case, on_done=None, rehearsal=False, parent=None):
        """Runs NBO's screen flow for this case. on_done(result) with result['message'] in Persian."""
        tgt = execution.target(case)
        reason = self.reason_label(case)
        done = on_done or (lambda _r: None)
        if not tgt or (tgt[0] != 'APPROVE' and not reason):
            done({'ok': False, 'message': 'برای این پرونده اقدامی در NBO تعیین نشده (یا دلیل NBO آن معلوم نیست)'})
            return
        if self.actor.busy:
            done({'ok': False, 'message': 'یک اجرای دیگر در NBO در جریان است؛ چند لحظه بعد دوباره بزن'})
            return
        if not rehearsal:
            db = self.session.db()
            try:
                now_status = db.execute("SELECT status FROM ref_nbo WHERE smr = ?", (case['smr'],)).fetchone()
                if not now_status or now_status[0] not in ('PENDING', 'COMMERCIAL_IN_PROGRESS'):
                    raise ValueError('در آخرین داده‌ی NBO این درخواست دیگر در انتظار نیست؛ کاری انجام نشد')
                problem = execution.eligibility(case)
                if problem:
                    raise ValueError(problem)
                execution.claim(db, case, require_synced=bool(sheets.load().get('workflow_sync')))
            except ValueError as e:
                done({'ok': False, 'message': str(e)})
                return
            finally:
                db.close()
        view = self._watch(parent, case, tgt[0], rehearsal) if parent is not None else None

        def finished(res):
            if view is not None:
                view.done_text(res)
            db = self.session.db()
            try:
                if rehearsal:
                    if res['ok']:
                        execution.record(db, case, 'REHEARSED', res.get('message') or 'همه‌ی مراحل تا قبل از ثبت نهایی درست بود')
                elif res['ok'] and res['sent']:
                    execution.record(db, case, 'SENT', ACTION_FA[tgt[0]] + (f" — {reason}" if reason else ''))
                else:
                    execution.record(db, case, 'UNCERTAIN' if res['error'] in AFTER_SEND else 'BLOCKED', res['message'])
            finally:
                db.close()
            self.refresh()
            if not rehearsal and res.get('sent'):
                self.session.sync_workflow(force=True)
            done(res)
        self.actor.run(case['smr'], tgt[0], reason, finished, rehearsal=rehearsal)

    def _watch(self, parent, case, action, rehearsal):
        """Shows NBO's own screen while the app works on it, so a person can see exactly what is clicked."""
        from PySide6.QtWebEngineWidgets import QWebEngineView
        dlg = QDialog(parent)
        dlg.setWindowTitle(f"{'تمرین' if rehearsal else ACTION_FA[action]} در NBO — {case['smr']}")
        dlg.resize(1100, 720)
        lay = QVBoxLayout(dlg)
        status = QLabel('در حال آماده شدن…')
        status.setObjectName('h3')
        lay.addWidget(status)
        view = QWebEngineView()
        view.setPage(self.actor.page)
        lay.addWidget(view, 1)
        self.actor.step.connect(status.setText)

        def done_text(res):
            try:
                self.actor.step.disconnect(status.setText)
            except (RuntimeError, TypeError):
                pass
            ok_text = 'تمرین درست بود؛ چیزی ثبت نشد' if res.get('rehearsed') else 'در NBO ثبت شد'
            status.setText(('✓ ' if res['ok'] else '✗ ') + (res['message'] or ok_text))
        dlg.done_text = done_text
        dlg.finished.connect(lambda _r: view.setPage(None))      # the page belongs to the actor, not to this window
        dlg.show()
        return dlg

    # ---- everything at once (owner 2026-10-02: "there is no clear place to apply all / automatically")
    def ready_cases(self, allowed=None):
        """Requests 'apply all' would take, oldest first: a verdict NBO can act on, of a kind switched on, eligible now
        (pending in fresh NBO data, synced), and not already tried in this revision."""
        allowed = self.auto_actions() if allowed is None else set(allowed)
        db = self.session.db()
        try:
            todo = []
            for case in workflow.cases(db):
                tgt = execution.target(case)
                if tgt and tgt[0] in allowed and not execution.eligibility(case):
                    todo.append(case)
            tried = {(r['smr'], r['revision']) for r in execution.records(db)
                     if r['state'] in ('SENDING', 'SENT', 'UNCERTAIN', 'BLOCKED', 'VERIFIED')}
        finally:
            db.close()
        return [c for c in sorted(todo, key=lambda c: c.get('updated_at') or '') if (c['smr'], c['revision']) not in tried]

    def rehearse_all(self):
        """Every ready request through NBO's screens up to (not including) Assign / the final click, one after another -
        nothing changes in NBO. -> how many are queued, or 'busy'."""
        if self.batch or self.actor.busy or self.mode.live:
            return 'busy'
        todo = self.ready_cases()
        if not todo:
            return 0
        self.batch = dict(todo=todo, done=0, ok=0, assign=0, failed=[], stop=False)
        self.last_batch = ''
        self.changed.emit()
        QTimer.singleShot(0, self._next_rehearsal)
        return len(todo)

    def batch_text(self):
        b = self.batch
        if not b:
            return self.last_batch
        return (f"در حال تمرین: {fa_digits(b['done'])} از {fa_digits(len(b['todo']))} — تا آخر درست: {fa_digits(b['ok'])}، "
                f"تا Assign درست: {fa_digits(b['assign'])}، خطا: {fa_digits(len(b['failed']))}")

    def _next_rehearsal(self):
        b = self.batch
        if not b:
            return
        if b['stop'] or b['done'] >= len(b['todo']):
            failed = '؛ '.join(f"{smr}: {msg}" for smr, msg in b['failed'][:5])
            self.last_batch = (f"تمرین {'متوقف شد' if b['stop'] else 'تمام شد'}: {fa_digits(b['done'])} درخواست — "
                               f"تا آخر درست: {fa_digits(b['ok'])}، تا Assign درست: {fa_digits(b['assign'])}، "
                               f"خطا: {fa_digits(len(b['failed']))}" + (f" ({failed})" if failed else ''))
            self.batch = None
            self.notice.emit(self.last_batch)
            self.changed.emit()
            return
        case = b['todo'][b['done']]

        def done(res):
            b['done'] += 1
            if res.get('ok') and res.get('error') == 'needs_assign':
                b['assign'] += 1
            elif res.get('ok'):
                b['ok'] += 1
            else:
                b['failed'].append((case['smr'], res.get('message') or res.get('error')))
            self.changed.emit()
            QTimer.singleShot(1500, self._next_rehearsal)
        self.apply(case, done, rehearsal=True)

    def apply_all(self):
        """'Apply all' = live mode: the automatic loop below takes the ready requests one by one (owner only)."""
        if self.batch:
            raise ValueError('اول «تمرین همه» تمام شود یا متوقفش کن')
        self.set_live(True)

    def stop_all(self):
        if self.batch:
            self.batch['stop'] = True
        if self.mode.live:
            self._stop_live('اعمال همه متوقف شد (با دکمه‌ی توقف)')
        self.changed.emit()

    # ---- automatic
    def _auto_tick(self):
        if not self.mode.live or self.actor.busy:
            return
        todo = self.ready_cases()
        if not todo:
            return
        case = todo[0]

        def done(res):
            if not res.get('ok'):
                self._stop_live(f"اجرای خودکار متوقف شد ({case['smr']}): {res.get('message')}")
            elif self._first_run:
                self._first_run = False
                self._stop_live(f"اولین تغییر واقعی ثبت شد ({case['smr']}). در NBO نگاهش کن؛ اگر درست بود دوباره «واقعی» را روشن کن.")
            else:
                QTimer.singleShot(3000, self._auto_tick)
        self.apply(case, done)

    # ---- summary
    def refresh(self):
        """On a worker thread: it writes preview rows, and a write on the window thread waits for the review's own writes
        (the app froze while a review was running, owner 2026-10-02)."""
        if self._refreshing:
            self._again = True
            return
        self._refreshing, self._again = True, False

        def work(_p):
            db = self.session.db()
            try:
                cases = workflow.cases(db)
                ready = [case for case in cases if workflow.state(case) == 'READY']
                prior = {(r['smr'], r['revision']) for r in execution.records(db)}
                for case in ready:
                    if (case['smr'], case['revision']) not in prior:
                        execution.record(db, case, 'PREVIEW', execution.eligibility(case) or 'تأیید تیم‌های لازم تکمیل است')
                return len(ready), sum(workflow.state(c) == 'WAIT_INSTORE' for c in cases)
            finally:
                db.close()

        def done(res):
            self._refreshing = False
            if isinstance(res, tuple):
                mode = 'واقعی — اپ خودش در NBO ثبت می‌کند' if self.mode.live else 'آزمایشی — اپ خودش چیزی در NBO تغییر نمی‌دهد'
                self.summary = f"{mode} • {fa_digits(res[0])} آماده‌ی تأیید، {fa_digits(res[1])} منتظر نظر Instore"
            self.changed.emit()
            if self._again:
                self.refresh()
        run_bg(work, done, done)

    def stop(self):
        self.mode.live = False
        self.timer.stop()
        self.auto_timer.stop()
