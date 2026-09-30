"""The background runner: backlog rows -> duplicate check -> live checks -> rule engine -> stored result.

DRY-RUN ONLY. Nothing here touches NBO or any sheet: the decision is stored in our own database and exported to a file.
The executor (which would change statuses in NBO) is a separate step that must pass guard.require() and is not part of this module."""
import asyncio
import json
import threading
import uuid
from dataclasses import dataclass, field

import httpx

from . import duplicates, facts as factsmod, rules as rulesmod, store
from .collectors import site as sitec
from .paths import config_dir
from .reasons import load_reasons, reason_code


def load_json(name):
    return json.loads((config_dir() / name).read_text(encoding="utf-8"))


@dataclass
class Progress:
    run_id: str = ""
    total: int = 0
    done: int = 0
    counts: dict = field(default_factory=lambda: {"APPROVE": 0, "EDIT": 0, "CANCEL": 0, "MANUAL": 0})
    state: str = "idle"          # idle | running | paused | stopping | finished | stopped | error
    last: str = ""
    error: str = ""


def duplicate_decision(reasons):
    code = reason_code(reasons, "DUPLICATE_REQUEST")
    if code is None:
        return rulesmod.Decision(rulesmod.MANUAL, notes=["duplicate found but the NBO cancel reason is not unambiguous"], trace=["DUPLICATE"])
    return rulesmod.Decision(rulesmod.CANCEL, ["DUPLICATE_REQUEST"], [code], ["a request with the same website is already approved"],
                             ["FAIL DUPLICATE_REQUEST -> CANCEL"])


class Runner:
    """Runs one batch on a background thread with its own event loop. pause/resume/stop are safe to call from the UI thread."""

    def __init__(self, db_path, rules=None, reasons=None, category_map=None, on_update=None, client_factory=None):
        self.db_path = str(db_path)
        self.rules = rules or load_json("rules.json")
        self.reasons = reasons or load_reasons()
        self.category_map = category_map or load_json("category_map.json")
        self.progress = Progress()
        self.on_update = on_update or (lambda p: None)
        self.client_factory = client_factory          # tests inject an offline transport
        self._thread = None
        self._resume = threading.Event(); self._resume.set()
        self._stop = threading.Event()

    # ---- controls
    def start(self, rows, approved_nbo=(), approved_crm=(), run_id=None):
        if self._thread and self._thread.is_alive():
            raise RuntimeError("a run is already in progress")
        self._stop.clear(); self._resume.set()
        run_id = run_id or uuid.uuid4().hex[:10]
        self.progress = Progress(run_id=run_id, total=len(rows), state="running")
        self._thread = threading.Thread(target=self._main, args=(rows, list(approved_nbo), list(approved_crm), run_id), daemon=True)
        self._thread.start()
        return run_id

    def pause(self):
        if self.progress.state == "running":
            self._resume.clear(); self.progress.state = "paused"; self.on_update(self.progress)

    def resume(self):
        if self.progress.state == "paused":
            self.progress.state = "running"; self._resume.set(); self.on_update(self.progress)

    def stop(self):
        if self.progress.state in ("running", "paused"):
            self.progress.state = "stopping"; self._stop.set(); self._resume.set(); self.on_update(self.progress)

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    # ---- worker
    def _main(self, rows, approved_nbo, approved_crm, run_id):
        try:
            asyncio.run(self._run(rows, approved_nbo, approved_crm, run_id))
        except Exception as e:                                          # never leave the UI waiting forever
            self.progress.state, self.progress.error = "error", f"{type(e).__name__}: {e}"
            self.on_update(self.progress)

    def _clients(self, timeout, limits):
        if self.client_factory:
            return self.client_factory(), self.client_factory()
        return httpx.AsyncClient(timeout=timeout, limits=limits), httpx.AsyncClient(timeout=timeout, limits=limits)

    async def _run(self, rows, approved_nbo, approved_crm, run_id):
        db = store.connect(self.db_path)
        with db:
            db.execute("INSERT OR REPLACE INTO runs (run_id, started_at, total, state) VALUES (?,?,?,?)", (run_id, store.now(), len(rows), "running"))
        already = store.done_ids(db, run_id)
        dupes = {d["id"]: d for d in duplicates.find_duplicates(
            [{"id": r["smr"], "site": r.get("site")} for r in rows], approved_nbo, approved_crm)}
        rt = self.rules.get("runtime", {})
        gate = asyncio.Semaphore(rt.get("concurrency", 6))
        enamad_gate = asyncio.Semaphore(rt.get("enamad_concurrency", 2))
        timeout = httpx.Timeout(rt.get("http_timeout_seconds", 20))
        limits = httpx.Limits(max_connections=rt.get("concurrency", 6) * 3)
        site_client, enamad_client = self._clients(timeout, limits)

        async with site_client, enamad_client:
            fetch = sitec.make_fetch(site_client)

            async def one(row):
                if self._stop.is_set():
                    return
                while not self._resume.is_set():
                    await asyncio.sleep(0.2)
                    if self._stop.is_set():
                        return
                if row["smr"] in already:
                    self._tick(None, row)
                    return
                async with gate:
                    if self._stop.is_set():
                        return
                    try:
                        if dupes.get(row["smr"], {}).get("is_duplicate"):
                            decision, ev = duplicate_decision(self.reasons), {"duplicate_of": dupes[row["smr"]]["related"]}
                        else:
                            f, ev = await factsmod.collect(row, self.rules, fetch, enamad_client, enamad_gate, self.category_map,
                                                           rt.get("pause_between_enamad_seconds", 0))
                            decision = rulesmod.evaluate(f, self.rules, self.reasons)
                    except Exception as e:                              # one broken request must not stop the batch
                        decision = rulesmod.Decision(rulesmod.MANUAL, notes=[f"internal error: {type(e).__name__}: {e}"], trace=["ERROR"])
                        ev = {}
                    store.save_result(db, run_id, row, decision, ev)
                self._tick(decision, row)

            await asyncio.gather(*(one(r) for r in rows))

        stopped = self._stop.is_set()
        with db:
            db.execute("UPDATE runs SET finished_at = ?, state = ? WHERE run_id = ?", (store.now(), "stopped" if stopped else "finished", run_id))
        self.progress.state = "stopped" if stopped else "finished"
        self.on_update(self.progress)

    def _tick(self, decision, row):
        p = self.progress
        p.done += 1
        if decision is not None:
            p.counts[decision.action] = p.counts.get(decision.action, 0) + 1
        p.last = row["smr"]
        self.on_update(p)
