"""App state shared by every page: who is signed in, the reference data, the current run, and background jobs.

Pages never block the window: every slow step (CRM, NBO, Google, the review itself) runs on a worker thread and reports
back through Qt signals."""
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import crm_sync, imports, logs, reference, settings, sheets, store, workboard
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
    busy_changed = Signal(str, bool)     # ("crm" | "nbo" | "sheet" | "oi", active)

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
        store.mark_interrupted_runs(db)
        db.close()

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

    def _watch(self, run_id):
        runner = self.runner

        def wait(_progress):
            while runner.is_active():
                time.sleep(0.25)
            return run_id
        run_bg(wait, self._finished)

    def _finished(self, run_id):
        self.data_changed.emit()
        self.run_finished.emit(run_id)
        cfg = sheets.load()
        if cfg.get("auto_send") and cfg.get("webapp_url"):
            self.send_run_to_sheet(run_id)

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
