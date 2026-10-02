"""App state shared by every page: who is signed in, the reference data, the current run, and background jobs.

Pages never block the window: every slow step (CRM, NBO, Google, the review itself) runs on a worker thread and reports
back through Qt signals."""
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer

from .. import crm_sync, execution, imports, logs, reference, settings, sheets, store, workboard, workflow, workflow_sync
from ..paths import data_dir, exports_dir
from ..pipeline import Runner

log = logs.get("app")


class _Job(QObject):
    done = Signal(object)
    failed = Signal(object)
    progress = Signal(object)


_alive = set()


def run_bg(fn, on_done=None, on_fail=None, on_progress=None):
    """fn(progress) runs on a worker thread; on_done/on_fail/on_progress run on the window thread."""
    job = _Job()
    _alive.add(job)
    if on_done:
        job.done.connect(on_done)
    if on_fail:
        job.failed.connect(on_fail)
    if on_progress:
        job.progress.connect(on_progress)
    job.done.connect(lambda _=None: _alive.discard(job))
    job.failed.connect(lambda _=None: _alive.discard(job))

    def work():
        try:
            res = fn(job.progress.emit)
        except Exception as e:                     # reported to the page, logged with its traceback
            log.exception("background job failed")
            job.failed.emit(e)
        else:
            job.done.emit(res)
    threading.Thread(target=work, daemon=True).start()
    return job


class Session(QObject):
    data_changed = Signal()              # reference data or results changed
    run_changed = Signal()               # progress of the current run
    run_finished = Signal(str)           # run_id
    busy_changed = Signal(str, bool)     # ("crm" | "nbo" | "sheet" | "workflow", active)
    workflow_sync_changed = Signal()

    def __init__(self, profile, crm_password=None):
        super().__init__()
        self.profile = profile
        self.crm_password = crm_password  # RAM only: lets the embedded CRM page sign in without asking again
        self.db_path = data_dir() / "autoreview.db"
        self.runner = None
        self.run_kind = None
        self.renderer = None              # set by the window (hidden browser for second looks)
        self.busy = set()
        db = self.db()
        reference.ensure(db)
        workflow.ensure(db)
        store.mark_interrupted_runs(db)
        db.close()
        self.workflow_sync_status = 'همگام‌سازی هنوز انجام نشده'
        self._sync_failures = 0
        self._sync_next = 0.0
        self.refresh_workflow()
        self._workflow_timer = QTimer(self)
        self._workflow_timer.setInterval(30_000)
        self._workflow_timer.timeout.connect(self.sync_workflow)
        self._workflow_timer.start()

    # ---- basics
    def db(self):
        return store.connect(self.db_path)

    def rules(self):
        return settings.load_rules()

    def user_label(self):
        return self.profile.get("display_name") or self.profile.get("username") or ""

    def _busy(self, key, on):
        (self.busy.add if on else self.busy.discard)(key)
        self.busy_changed.emit(key, on)

    # ---- the board: how much is done / left
    def board(self):
        db = self.db()
        try:
            rules = self.rules()
            backlog, skipped = reference.backlog_rows(db, rules)
            both = reference.both_channel_rows(db, rules)
            latest, manual_done = store.latest_states(db), store.manual_done_set(db)
            return {"backlog": workboard.summarize(backlog, latest, manual_done),
                    "both": workboard.summarize(both, latest, manual_done),
                    "nbo_meta": reference.meta(db, "nbo"), "crm_meta": reference.meta(db, "crm"),
                    "status_counts": reference.status_counts(db), "skipped": skipped}
        finally:
            db.close()

    def queue_rows(self, kind, only_unreviewed=True):
        """Rows for a run: kind 'online' (direct NBO backlog) or 'both' (Online-Instore sheet flow)."""
        db = self.db()
        try:
            rules = self.rules()
            rows = reference.backlog_rows(db, rules)[0] if kind == "online" else reference.both_channel_rows(db, rules)
            if only_unreviewed:
                latest = store.latest_states(db)
                rows = [r for r in rows if r["smr"] not in latest]
            return rows
        finally:
            db.close()

    # ---- reference data
    def import_nbo_file(self, path, on_done=None, on_fail=None):
        def work(progress):
            progress("در حال خواندن فایل…")
            rows = imports.read_export(path, "nbo")
            db = self.db()
            try:
                n = reference.import_nbo(db, rows, f"file:{Path(path).name}")
            finally:
                db.close()
            log.info("NBO reference loaded from %s: %d rows", Path(path).name, n)
            return n
        self._busy("nbo", True)
        return run_bg(work, lambda n: self._after("nbo", on_done, n), lambda e: self._failed("nbo", on_fail, e))

    def import_nbo_bytes(self, data: bytes, on_done=None, on_fail=None):
        def work(progress):
            target = exports_dir() / f"nbo_export_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
            target.write_bytes(data)
            rows = imports.read_export(target, "nbo")
            db = self.db()
            try:
                n = reference.import_nbo(db, rows, "auto")
            finally:
                db.close()
            log.info("NBO reference loaded from the NBO export: %d rows", n)
            return n
        self._busy("nbo", True)
        return run_bg(work, lambda n: self._after("nbo", on_done, n), lambda e: self._failed("nbo", on_fail, e))

    def import_nbo_parts(self, parts, on_done=None, on_fail=None, only_statuses=None):
        """Several NBO exports (one per status) -> ONE reference. All statuses fetched: replaced like a full export;
        some missing: only the fetched statuses are replaced (only_statuses), the rest keep their last rows."""
        def work(progress):
            rows = []
            for i, data in enumerate(parts):
                target = exports_dir() / f"nbo_export_{datetime.now():%Y%m%d_%H%M%S}_{i}.xlsx"
                target.write_bytes(data)
                rows += imports.read_export(target, "nbo")
            db = self.db()
            try:
                n = reference.import_nbo(db, rows, "auto (per status)", only_statuses)
            finally:
                db.close()
            log.info("NBO reference loaded from %d per-status exports: %d rows", len(parts), n)
            return n
        self._busy("nbo", True)
        return run_bg(work, lambda n: self._after("nbo", on_done, n), lambda e: self._failed("nbo", on_fail, e))

    def refresh_crm(self, full=False, on_done=None, on_fail=None, on_progress=None):
        def work(progress):
            db = self.db()
            try:
                m = reference.meta(db, "crm")
                since = None
                if not full and m and m.get("watermark"):
                    try:
                        mark = datetime.fromisoformat(m["watermark"].replace("Z", "+00:00")) - timedelta(minutes=10)
                        since = mark.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except ValueError:
                        since = None
                rows = crm_sync.fetch_reference(since=since, progress=lambda pages, n: progress((pages, n)))
                reference.upsert_crm(db, rows, "auto", full=since is None)
                return {"rows": len(rows), "incremental": since is not None}
            finally:
                db.close()
        self._busy("crm", True)
        return run_bg(work, lambda r: self._after("crm", on_done, r), lambda e: self._failed("crm", on_fail, e), on_progress)

    def _after(self, key, cb, res):
        self._busy(key, False)
        if key == 'nbo':
            self.refresh_workflow()
        self.data_changed.emit()
        if cb:
            cb(res)

    def _failed(self, key, cb, err):
        self._busy(key, False)
        if cb:
            cb(err)

    # ---- runs
    def approved_sets(self):
        db = self.db()
        try:
            return reference.approved_sets(db, self.rules())
        finally:
            db.close()

    def start_run(self, rows, kind, label=None):
        if self.runner and self.runner.is_active():
            raise RuntimeError("یک بررسی در حال اجراست")
        nbo_ok, crm_ok = self.approved_sets()
        db = self.db()
        try:
            pending_all = reference.backlog_rows(db, self.rules())[0] + reference.both_channel_rows(db, self.rules())
        finally:
            db.close()
        renderer = self.renderer

        async def second_look(url):
            return await renderer.render_async(url)
        self.runner = Runner(self.db_path, on_update=lambda p: self.run_changed.emit(), user_name=self.user_label(),
                             render=second_look if renderer is not None else None)
        self.run_kind = kind
        run_id = self.runner.start(rows, nbo_ok, crm_ok, pending_all=pending_all, label=label)
        self._watch(run_id)
        return run_id

    def autopilot_run(self):
        """Autopilot (owner 2026-10-02): review the requests nobody reviewed yet, one batch at a time, whenever fresh NBO data
        arrives; the next batch starts when one finishes. -> what happened, for the status line."""
        rules = self.rules()
        if not rules.get("automation", {}).get("autopilot", True):
            return "off"
        if self.runner and self.runner.is_active():
            return "busy"
        db = self.db()
        try:
            have_crm = reference.meta(db, "crm") is not None
        finally:
            db.close()
        if not have_crm:
            return "no_crm"                              # duplicates need BOTH approved sets; never review half-blind
        rows = (self.queue_rows("online") + self.queue_rows("both"))[: rules["backlog"]["batch_size"]]
        if not rows:
            return 0
        self.start_run(rows, "auto", label="خلبان خودکار")
        log.info("autopilot started a batch of %d new requests", len(rows))
        return len(rows)

    def _watch(self, run_id):
        runner = self.runner

        def wait(_progress):
            while runner.is_active():
                time.sleep(0.25)
            return run_id
        run_bg(wait, self._finished)

    def _finished(self, run_id):
        if self.run_kind == "auto" and self.runner and self.runner.progress.state == "finished":
            QTimer.singleShot(5000, self.autopilot_run)          # next batch, until nothing new is left
        self.refresh_workflow(run_id)
        self.data_changed.emit()
        self.run_finished.emit(run_id)
        cfg = sheets.load()
        if cfg.get("auto_send") and (cfg.get("webapp_url") or cfg.get('auth_mode') == 'service_account'):
            self.send_run_to_sheet(run_id)
        self.sync_workflow()

    def refresh_workflow(self, run_id=None):
        if sheets.load().get('auth_mode') == 'workspace' and self.profile.get('workspace_role') not in ('admin', 'online'):
            return
        rules = self.rules()
        engine_counts = bool(rules.get('workflow', {}).get('engine_verdict_counts'))
        db = self.db()
        try:
            execution.ensure(db)
            meta = reference.meta(db, 'nbo')
            if meta:
                eligible = reference.backlog_rows(db, rules)[0] + reference.both_channel_rows(db, rules)
                workflow.refresh(db, reference.all_nbo_rows(db), {r['smr'] for r in eligible}, meta['loaded_at'],
                                 rules['approved_statuses']['nbo'], complete="(partial)" not in (meta.get('origin') or ''))
            if run_id:
                for result in store.results_of(db, run_id):
                    workflow.suggest(db, result['smr'], result, engine_counts)
            workflow.adopt_engine_verdicts(db, engine_counts)        # follows the switch in Settings both ways
            execution.note_nbo_outcomes(db)
        finally:
            db.close()

    def sync_workflow(self, force=False):
        cfg = sheets.load()
        if 'workflow' in self.busy:
            return
        if not cfg.get('workflow_sync') or not (cfg.get('webapp_url') or cfg.get('workspace_url') or cfg.get('auth_mode') == 'service_account'):
            self.workflow_sync_status = 'اتصال گردش کار خاموش است؛ از «اتصال‌ها» فعال کن'
            self.workflow_sync_changed.emit()
            return
        if not force and time.monotonic() < self._sync_next:
            return
        self._busy('workflow', True)
        self.workflow_sync_status = 'در حال همگام‌سازی با شیت اختصاصی…'
        self.workflow_sync_changed.emit()
        def work(_p):
            db = self.db()
            try: return workflow_sync.sync(db, cfg)
            finally: db.close()
        def done(result):
            self._busy('workflow', False)
            self._sync_failures = 0
            self._sync_next = time.monotonic() + 30
            self.workflow_sync_status = ('آخرین اتصال موفق: ' + datetime.now().strftime('%H:%M:%S') +
                (f" — {result['rejected']} تصمیم رد شد؛ تب Decisions را بررسی کن" if result['rejected'] else ''))
            self.workflow_sync_changed.emit()
            self.data_changed.emit()
        def failed(_error):
            self._busy('workflow', False)
            self._sync_failures += 1
            wait = min(300, 30 * 2 ** min(self._sync_failures - 1, 4))
            self._sync_next = time.monotonic() + wait
            self.workflow_sync_status = f'همگام‌سازی ناموفق: {_error} — داده محفوظ است؛ تلاش بعدی تا {wait} ثانیه دیگر'
            self.workflow_sync_changed.emit()
        return run_bg(work, done, failed)

    def send_run_to_sheet(self, run_id, on_done=None, on_fail=None):
        def work(_p):
            db = self.db()
            try:
                res = store.results_of(db, run_id)
            finally:
                db.close()
            return sheets.send_run(run_id, res, self.user_label())
        self._busy("sheet", True)
        return run_bg(work, lambda r: self._after("sheet", on_done, r), lambda e: self._failed("sheet", on_fail, e))
