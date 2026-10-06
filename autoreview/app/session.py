"""App state shared by every page: who is signed in, the reference data, the current run, and background jobs.

Pages never block the window: every slow step (CRM, NBO, Google, the review itself) runs on a worker thread and reports
back through Qt signals."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer

from .. import workspace, crm_sync, execution, imports, logs, reference, settings, sheets, store, workboard, workflow, workflow_sync
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
        self._refresh_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache = {}
        db = self.db()
        reference.ensure(db)
        workflow.ensure(db)
        store.mark_interrupted_runs(db)
        db.close()
        self.workflow_sync_status = 'همگام‌سازی هنوز انجام نشده'
        self.workflow_refresh_error = ''
        self._sync_failures = 0
        self._sync_next = 0.0
        self._workspace_connected = False
        self.refresh_workflow()
        QTimer.singleShot(0, self.sync_workflow)
        self._workflow_timer = QTimer(self)
        self._workflow_timer.setInterval(10_000)
        self._workflow_timer.timeout.connect(self.sync_workflow)
        self._workflow_timer.start()

    # ---- basics
    def disconnect_workspace(self):
        self._workflow_timer.stop()
        db = self.db()
        try: workspace.audit(db, 'WORKSPACE_DISCONNECT')
        finally: db.close()

    def db(self):
        return store.connect(self.db_path)

    def read_db(self, busy_timeout_ms=150):
        """Short read-only handle for UI paints/searches; never waits seconds on a writer."""
        return store.connect_read(self.db_path, busy_timeout_ms)

    def rules(self):
        return settings.load_rules()

    def user_label(self):
        return (crm_sync.authenticated_identity() or self.profile).get("username") or ""

    def _busy(self, key, on):
        (self.busy.add if on else self.busy.discard)(key)
        self.busy_changed.emit(key, on)

    # ---- the board: how much is done / left
    # ---- the NBO reference, read once per load ------------------------------------------------------------------------------
    # Every page asked for the queues by reading the whole reference again (~68,000 rows, ~0.4 s each). The review page alone
    # did it eight times per refresh and refreshed on every data change, visible or not; the jobs piled up faster than they
    # finished and starved the window thread ('Not Responding', live 2026-10-02, seen with py-spy). The queues and the
    # approved sets are now built once per reference load (and rules), under a lock, and shared.
    def _cached(self, name, key, build):
        with self._cache_lock:
            hit = self._cache.get(name)
            if hit and hit[0] == key:
                return hit[1]
            value = build()
            self._cache[name] = (key, value)
            return value

    def _reference_key(self, db, *sources):
        metas = tuple((reference.meta(db, s) or {}).get("loaded_at") for s in sources)
        return metas + (json.dumps(self.rules(), sort_keys=True, default=str),)

    def _queues(self, db):
        """-> (online backlog, skipped, Online + Instore) for the current NBO reference."""
        def build():
            if sheets.load().get('auth_mode') == 'workspace':
                return workspace.shared_queues(db)
            rules = self.rules()
            backlog, skipped = reference.backlog_rows(db, rules)
            return backlog, skipped, reference.both_channel_rows(db, rules)
        return self._cached("queues", self._reference_key(db, "nbo"), build)

    def board(self):
        db = self.db()
        try:
            backlog, skipped, both = self._queues(db)
            latest, manual_done = (workspace.shared_reviews(db) if sheets.load().get('auth_mode') == 'workspace' else workflow.current_reviews(db, store.latest_states(db))), store.manual_done_set(db)
            return {"backlog": workboard.summarize(backlog, latest, manual_done),
                    "both": workboard.summarize(both, latest, manual_done),
                    "nbo_meta": reference.meta(db, "nbo"), "crm_meta": reference.meta(db, "crm"),
                    "status_counts": reference.status_counts(db), "skipped": skipped}
        finally:
            db.close()

    def manual_again_rows(self):
        """Open requests whose newest review is MANUAL and nobody decided by hand yet - to review again after the rules
        changed (owner 2026-10-02: old 'unknown: category' results that the old rules now decide). Same order as 'all'."""
        db = self.db()
        try:
            latest = (workspace.shared_reviews(db) if sheets.load().get('auth_mode') == 'workspace' else workflow.current_reviews(db, store.latest_states(db)))
            human = {c['smr'] for c in workflow.cases(db)
                     if (c.get('online') or {}).get('source') not in (None, 'engine') or c.get('online_hold')}
        finally:
            db.close()
        return [r for r in self.queue_rows("all", False)
                if latest.get(r["smr"], ("",))[0] == "MANUAL" and r["smr"] not in human]

    def queue_rows(self, kind, only_unreviewed=True):
        """Rows for a run: kind 'online' (direct NBO backlog), 'both' (Online-Instore sheet flow) or 'all' - both queues
        together, Online + Instore first: the Instore team cannot start on a request before its Online verdict (owner
        2026-10-02: 590 of them waited while the autopilot kept taking the Online-only queue first)."""
        db = self.db()
        try:
            backlog, _skipped, both = self._queues(db)
            if kind == "all":
                seen = {r["smr"] for r in both}
                rows = both + [r for r in backlog if r["smr"] not in seen]
            else:
                rows = list(backlog if kind == "online" else both)
            if only_unreviewed:
                latest = (workspace.shared_reviews(db) if sheets.load().get('auth_mode') == 'workspace' else workflow.current_reviews(db, store.latest_states(db)))
                rows = [r for r in rows if r["smr"] not in latest]
            return [dict(r) for r in rows]                 # callers may change a row; the shared cache stays as it is
        finally:
            db.close()

    def queue_counts(self):
        """{kind: unreviewed, kind_all: every} for online / both / all - one pass, for the review page."""
        db = self.db()
        try:
            backlog, _skipped, both = self._queues(db)
            latest = (workspace.shared_reviews(db) if sheets.load().get('auth_mode') == 'workspace' else workflow.current_reviews(db, store.latest_states(db)))
        finally:
            db.close()
        seen = {r["smr"] for r in both}
        everything = both + [r for r in backlog if r["smr"] not in seen]
        out = {}
        for kind, rows in (("online", backlog), ("both", both), ("all", everything)):
            out[kind + "_all"] = len(rows)
            out[kind] = sum(r["smr"] not in latest for r in rows)
        return out

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
        return run_bg(work, lambda n: self._after("nbo", on_done, n, on_fail), lambda e: self._failed("nbo", on_fail, e))

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
        return run_bg(work, lambda n: self._after("nbo", on_done, n, on_fail), lambda e: self._failed("nbo", on_fail, e))

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
        return run_bg(work, lambda n: self._after("nbo", on_done, n, on_fail), lambda e: self._failed("nbo", on_fail, e))

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

    def _after(self, key, cb, res, on_fail=None):
        self._busy(key, False)
        if key == 'nbo':
            self.refresh_workflow(then=lambda: cb(res) if cb else None, on_fail=on_fail)
            return
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
            return self._cached("approved", self._reference_key(db, "nbo", "crm"),
                                lambda: reference.approved_sets(db, self.rules()))
        finally:
            db.close()

    def start_run(self, rows, kind, label=None):
        if self.runner and self.runner.is_active():
            raise RuntimeError("یک بررسی در حال اجراست")
        nbo_ok, crm_ok = self.approved_sets()
        db = self.db()
        try:
            backlog, _skipped, both = self._queues(db)
            pending_all = backlog + both
            pending_all += reference.left_queue_rows(db)        # a same-site request NBO just decided: a person checks
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
        rows = self.queue_rows("all")[: rules["backlog"]["batch_size"]]
        if not rows:
            return 0
        self.start_run(rows, "auto", label="Autopilot")
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
        def then():
            self.run_finished.emit(run_id)
            cfg = sheets.load()
            if cfg.get("auto_send"):
                self.send_run_to_sheet(run_id)
            self.sync_workflow()

        def refresh_failed(_error):
            self.run_finished.emit(run_id)
        self.refresh_workflow(run_id, then, refresh_failed)

    def refresh_workflow(self, run_id=None, then=None, on_fail=None):
        """Rebuilds the workflow queue from the NBO reference (and a finished run's verdicts) on a worker thread, then
        data_changed and `then` on the window thread. It reads ~30,000 reference rows and writes the queue - on the window
        thread, while a review was writing results, that froze the app ('Not Responding', owner 2026-10-02)."""
        if sheets.load().get('auth_mode') == 'workspace' and not crm_sync.authenticated_identity():
            self.workflow_refresh_error = ''
            self.data_changed.emit()
            self.workflow_sync_changed.emit()
            if then:
                then()
            return

        def work(_p):
            with self._refresh_lock:                 # one rebuild at a time; a second one waits and then sees fresh data
                self._refresh_workflow_now(run_id)

        def done(_r=None):
            self.workflow_refresh_error = ''
            self.data_changed.emit()
            self.workflow_sync_changed.emit()
            if then:
                then()

        def failed(error):
            self.workflow_refresh_error = str(error)
            self.workflow_sync_changed.emit()
            self.data_changed.emit()
            if on_fail:
                on_fail(error)
        return run_bg(work, done, failed)

    def _refresh_workflow_now(self, run_id=None):
        rules = self.rules()
        engine_counts = bool(rules.get('workflow', {}).get('engine_verdict_counts'))
        db = self.db()
        try:
            execution.ensure(db, recover=False)
            meta = reference.meta(db, 'nbo')
            if meta:
                eligible = reference.backlog_rows(db, rules)[0] + reference.both_channel_rows(db, rules)
                workflow.refresh(db, reference.all_nbo_rows(db), {r['smr'] for r in eligible}, meta['loaded_at'],
                                 rules['approved_statuses']['nbo'], complete="(partial)" not in (meta.get('origin') or ''))
            if run_id:
                for result in store.results_of(db, run_id):
                    workflow.suggest(db, result['smr'], result, engine_counts)
            workflow.reconcile_suggestions(db, store.latest_results(db), engine_counts)
            workflow.adopt_engine_verdicts(db, engine_counts)        # follows the switch in Settings both ways
            workflow.hold_manual_categories(db, rules)                # always-manual categories never ride on the engine
            execution.note_nbo_outcomes(db)
            execution.verify_sent(db)                                 # did what the app sent really change NBO?
        finally:
            db.close()

    def sync_workflow(self, force=False):
        cfg = sheets.load()
        if 'workflow' in self.busy:
            return
        if not cfg.get('workflow_sync'):
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
            if not self._workspace_connected and cfg.get('auth_mode') == 'workspace':
                db = self.db()
                try: workspace.audit(db, 'WORKSPACE_CONNECT')
                finally: db.close()
                self._workspace_connected = True
            self._sync_failures = 0
            self._sync_next = time.monotonic() + (10 if cfg.get('auth_mode') == 'workspace' else 30)
            self.workflow_sync_status = ('آخرین اتصال موفق: ' + datetime.now().strftime('%H:%M:%S') +
                (f" — {result['rejected']} تعارض؛ همگام‌سازی دوباره تلاش می‌کند" if result['rejected'] else ''))
            self.workflow_sync_changed.emit()
            self.data_changed.emit()
        def failed(_error):
            self._busy('workflow', False)
            if cfg.get('auth_mode') == 'workspace' and (self._workspace_connected or self._sync_failures == 0):
                db = self.db()
                try: workspace.audit(db, 'WORKSPACE_FAILURE', getattr(_error, 'code', 'GOOGLE_API_ERROR'))
                finally: db.close()
            self._workspace_connected = False
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
