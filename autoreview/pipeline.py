"""The background runner: backlog rows -> duplicate check -> live checks -> rule engine -> stored result.

DRY-RUN ONLY. Nothing here touches NBO or any sheet: the decision is stored in our own database and exported to a file.
The executor (which would change statuses in NBO) is a separate step that must pass guard.require() and is not part of this module.

Workers pull requests from a queue, so "pause" really pauses (no request is started while paused) and "stop" lets the
requests already in flight finish cleanly. Every request has an overall deadline, so one slow site can never stall a batch."""
import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx

from . import duplicates, facts as factsmod, logs, rules as rulesmod, settings, store
from .collectors import site as sitec
from .reasons import load_reasons, reason_code

log = logs.get("run")


@dataclass
class Progress:
    run_id: str = ""
    total: int = 0
    done: int = 0
    counts: dict = field(default_factory=lambda: dict.fromkeys(store.ACTIONS, 0))
    state: str = "idle"          # idle | running | paused | stopping | finished | stopped | error
    last: str = ""
    error: str = ""
    started: float = 0.0
    busy_seconds: float = 0.0    # sum of per-request durations (for the average)

    def eta_seconds(self):
        if self.done == 0 or self.state not in ("running", "paused"):
            return None
        elapsed = time.time() - self.started
        return max(0.0, elapsed / self.done * (self.total - self.done))


def duplicate_decision(reasons, info=None):
    where = ""
    if info:
        systems = " / ".join(x for x in (("NBO" if info.get("in_nbo") else ""), ("CRM" if info.get("in_crm") else "")) if x)
        where = f" ({systems}: {', '.join(info.get('related', [])[:3])})"
    code = reason_code(reasons, "DUPLICATE_REQUEST")
    if code is None:
        return rulesmod.Decision(rulesmod.MANUAL, notes=["duplicate found but the NBO cancel reason is not unambiguous" + where], trace=["DUPLICATE"])
    return rulesmod.Decision(rulesmod.CANCEL, ["DUPLICATE_REQUEST"], [code], ["the same website is already approved" + where],
                             ["FAIL DUPLICATE_REQUEST -> CANCEL"])


def sibling_decision(siblings):
    return rulesmod.Decision(rulesmod.MANUAL, notes=[f"another pending request has the same website ({', '.join(siblings[:3])})"],
                             trace=["PENDING_SIBLING"])


class Runner:
    """Runs one batch on a background thread with its own event loop. pause/resume/stop are safe to call from the UI thread."""

    def __init__(self, db_path, rules=None, reasons=None, category_map=None, on_update=None, client_factory=None, user_name=None):
        self.db_path = str(db_path)
        self.rules = rules or settings.load_rules()
        self.reasons = reasons or load_reasons()
        self.category_map = category_map or settings.load_category_map()
        self.progress = Progress()
        self.on_update = on_update or (lambda p: None)
        self.client_factory = client_factory          # tests inject an offline transport
        self.user_name = user_name
        self._thread = None
        self._resume = threading.Event()
        self._resume.set()
        self._stop = threading.Event()

    # ---- controls
    def start(self, rows, approved_nbo=(), approved_crm=(), run_id=None, pending_all=None, source_file=None, label=None):
        if self._thread and self._thread.is_alive():
            raise RuntimeError("a run is already in progress")
        if not approved_nbo or not approved_crm:            # NBO and CRM hold different requests: a duplicate can hide in either one
            raise ValueError("both the NBO and the CRM approved sets are required for the duplicate check")
        if not rows:
            raise ValueError("nothing to review")
        self._stop.clear()
        self._resume.set()
        run_id = run_id or uuid.uuid4().hex[:10]
        self.progress = Progress(run_id=run_id, total=len(rows), state="running", started=time.time())
        pending = rows if pending_all is None else pending_all          # [] = caller explicitly has no other pending requests
        args = (list(rows), list(approved_nbo), list(approved_crm), list(pending), run_id, source_file, label)
        self._thread = threading.Thread(target=self._main, args=args, daemon=True, name=f"run-{run_id}")
        self._thread.start()
        return run_id

    def pause(self):
        if self.progress.state == "running":
            self._resume.clear()
            self.progress.state = "paused"
            log.info("run %s paused at %d/%d", self.progress.run_id, self.progress.done, self.progress.total)
            self.on_update(self.progress)

    def resume(self):
        if self.progress.state == "paused":
            self.progress.state = "running"
            self._resume.set()
            log.info("run %s resumed", self.progress.run_id)
            self.on_update(self.progress)

    def stop(self):
        if self.progress.state in ("running", "paused"):
            self.progress.state = "stopping"
            self._stop.set()
            self._resume.set()
            log.info("run %s stopping (requests in flight finish first)", self.progress.run_id)
            self.on_update(self.progress)

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def is_active(self):
        return bool(self._thread and self._thread.is_alive())

    # ---- worker
    def _main(self, *args):
        try:
            asyncio.run(self._run(*args))
        except Exception as e:                                          # never leave the UI waiting forever
            log.exception("run %s failed", self.progress.run_id)
            self.progress.state, self.progress.error = "error", f"{type(e).__name__}: {e}"
            try:
                store.finish_run(store.connect(self.db_path), self.progress.run_id, "error")
            except Exception:
                pass
            self.on_update(self.progress)

    def _clients(self, timeout, limits):
        if self.client_factory:
            return self.client_factory(), self.client_factory()
        return (httpx.AsyncClient(timeout=timeout, limits=limits, headers=sitec.HEADERS),
                httpx.AsyncClient(timeout=timeout, limits=httpx.Limits(max_connections=4)))

    async def _run(self, rows, approved_nbo, approved_crm, pending_all, run_id, source_file, label):
        db = store.connect(self.db_path)
        store.start_run(db, run_id, len(rows), source_file=source_file, user_name=self.user_name, label=label)
        with db:
            store.log(db, None, "RUN_SOURCES", {"run": run_id, "nbo_approved": len(approved_nbo), "crm_approved": len(approved_crm)})
        log.info("run %s started: %d requests (NBO approved %d, CRM approved %d)", run_id, len(rows), len(approved_nbo), len(approved_crm))
        already = store.done_ids(db, run_id)
        dupes = {d["id"]: d for d in duplicates.find_duplicates(
            [{"id": r["smr"], "site": r.get("site")} for r in rows], approved_nbo, approved_crm)}
        siblings = duplicates.pending_siblings([{"id": r["smr"], "site": r.get("site")} for r in pending_all])
        rt = self.rules.get("runtime", {})
        workers = max(1, int(rt.get("concurrency", 6)))
        enamad_gate = asyncio.Semaphore(max(1, int(rt.get("enamad_concurrency", 2))))
        deadline = float(rt.get("request_deadline_seconds", 150))
        timeout = httpx.Timeout(float(rt.get("http_timeout_seconds", 20)))
        limits = httpx.Limits(max_connections=workers * 3, max_keepalive_connections=workers)
        site_client, enamad_client = self._clients(timeout, limits)
        queue = asyncio.Queue()
        for r in rows:
            queue.put_nowait(r)

        async with site_client, enamad_client:
            fetch = sitec.make_fetch(site_client)

            async def decide(row):
                dup = dupes.get(row["smr"], {})
                if dup.get("is_duplicate"):
                    return duplicate_decision(self.reasons, dup), {"duplicate_of": dup["related"], "in_nbo": dup["in_nbo"], "in_crm": dup["in_crm"]}
                if row["smr"] in siblings:
                    return sibling_decision(siblings[row["smr"]]), {"pending_siblings": siblings[row["smr"]]}
                f, ev = await factsmod.collect(row, self.rules, fetch, enamad_client, enamad_gate, self.category_map,
                                               float(rt.get("pause_between_enamad_seconds", 0)))
                return rulesmod.evaluate(f, self.rules, self.reasons), ev

            async def worker():
                while not self._stop.is_set():
                    while not self._resume.is_set():                     # paused: start nothing new
                        await asyncio.sleep(0.2)
                        if self._stop.is_set():
                            return
                    try:
                        row = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    if row["smr"] in already:
                        self._tick(None, row, 0)
                        continue
                    t0 = time.monotonic()
                    try:
                        decision, ev = await asyncio.wait_for(decide(row), timeout=deadline)
                    except asyncio.TimeoutError:
                        decision = rulesmod.Decision(rulesmod.MANUAL, notes=[f"timed out: the checks took longer than {int(deadline)} s"], trace=["TIMEOUT"])
                        ev = {}
                        log.warning("%s: checks timed out after %ds (%s)", row["smr"], deadline, row.get("site"))
                    except Exception as e:                              # one broken request must not stop the batch
                        decision = rulesmod.Decision(rulesmod.MANUAL, notes=[f"internal error: {type(e).__name__}: {e}"], trace=["ERROR"])
                        ev = {}
                        log.exception("%s: internal error", row["smr"])
                    ms = int((time.monotonic() - t0) * 1000)
                    store.save_result(db, run_id, row, decision, ev, duration_ms=ms)
                    log.info("%s -> %s %s (%.1fs)", row["smr"], decision.action, ",".join(decision.reason_codes) or "", ms / 1000)
                    self._tick(decision, row, ms)

            await asyncio.gather(*(worker() for _ in range(workers)))

        stopped = self._stop.is_set()
        state = "stopped" if stopped else "finished"
        store.finish_run(db, run_id, state)
        db.close()
        self.progress.state = state
        c = self.progress.counts
        log.info("run %s %s: %d/%d (approve %d, edit %d, cancel %d, manual %d)", run_id, state, self.progress.done, self.progress.total,
                 c["APPROVE"], c["EDIT"], c["CANCEL"], c["MANUAL"])
        self.on_update(self.progress)

    def _tick(self, decision, row, ms):
        p = self.progress
        p.done += 1
        p.busy_seconds += ms / 1000
        if decision is not None:
            p.counts[decision.action] = p.counts.get(decision.action, 0) + 1
        p.last = row["smr"]
        self.on_update(p)
