"""SQLite store: imported requests, decisions per run, and an append-only audit log (who/what/when for every request)."""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  smr TEXT NOT NULL, source TEXT NOT NULL, status TEXT, site TEXT, imported_at TEXT NOT NULL,
  PRIMARY KEY (smr, source)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, smr TEXT, stage TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
  run_id TEXT NOT NULL, smr TEXT NOT NULL, site TEXT, category TEXT, created_at TEXT,
  action TEXT NOT NULL, reason_keys TEXT, reason_codes TEXT, notes TEXT, trace TEXT, evidence TEXT, decided_at TEXT NOT NULL,
  PRIMARY KEY (run_id, smr)
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, source_file TEXT, total INTEGER, state TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=":memory:"):
    db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    if str(path) != ":memory:":
        db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def log(db, smr, stage, detail: dict):
    db.execute("INSERT INTO audit (ts, smr, stage, detail) VALUES (?,?,?,?)", (now(), smr, stage, json.dumps(detail, ensure_ascii=False)))


def replace_source(db, source, rows):
    """A fresh export REPLACES the whole source snapshot (same as the old 'clear + rewrite'), atomically."""
    with db:
        db.execute("DELETE FROM requests WHERE source = ?", (source,))
        db.executemany("INSERT OR REPLACE INTO requests (smr, source, status, site, imported_at) VALUES (?,?,?,?,?)",
                       [(r["smr"], source, r.get("status"), r.get("site"), now()) for r in rows])
        log(db, None, "IMPORT", {"source": source, "rows": len(rows)})


def rows_of(db, source):
    cur = db.execute("SELECT smr, status, site FROM requests WHERE source = ? ORDER BY smr", (source,))
    return [{"id": s, "smr": s, "status": st, "site": si} for s, st, si in cur.fetchall()]


# ---- decisions -------------------------------------------------------------------------------------------------------
def save_result(db, run_id, row, decision, evidence):
    with db:
        db.execute(
            "INSERT OR REPLACE INTO results (run_id, smr, site, category, created_at, action, reason_keys, reason_codes, notes, trace, evidence, decided_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, row["smr"], row.get("site"), row.get("category"), row.get("created_at"), decision.action,
             json.dumps(decision.reason_keys, ensure_ascii=False), json.dumps(decision.reason_codes, ensure_ascii=False),
             json.dumps(decision.notes, ensure_ascii=False), json.dumps(decision.trace, ensure_ascii=False),
             json.dumps(evidence, ensure_ascii=False, default=str), now()))
        log(db, row["smr"], "DECISION", {"run": run_id, "action": decision.action, "reasons": decision.reason_codes})


def done_ids(db, run_id):
    return {r[0] for r in db.execute("SELECT smr FROM results WHERE run_id = ?", (run_id,))}


def results_of(db, run_id):
    cur = db.execute("SELECT smr, site, category, created_at, action, reason_keys, reason_codes, notes, decided_at "
                     "FROM results WHERE run_id = ? ORDER BY rowid", (run_id,))
    keys = ("smr", "site", "category", "created_at", "action", "reason_keys", "reason_codes", "notes", "decided_at")
    out = []
    for r in cur.fetchall():
        d = dict(zip(keys, r))
        for k in ("reason_keys", "reason_codes", "notes"):
            d[k] = json.loads(d[k] or "[]")
        out.append(d)
    return out


def latest_run(db):
    row = db.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def run_sources(db, run_id):
    """-> {'nbo_approved': n, 'crm_approved': m} recorded when the run started, or None."""
    for (detail,) in db.execute("SELECT detail FROM audit WHERE stage = 'RUN_SOURCES' ORDER BY id DESC"):
        d = json.loads(detail)
        if d.get("run") == run_id:
            return d
    return None
