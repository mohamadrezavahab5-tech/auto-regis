"""SQLite store: imported requests, decisions per run, an append-only audit log, and the numbers the dashboard draws.

One database per person (in their profile). Every connection is short-lived and WAL-mode, so the background runner and the
window can read/write at the same time."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

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
CREATE INDEX IF NOT EXISTS results_decided ON results (decided_at);
CREATE INDEX IF NOT EXISTS results_smr ON results (smr, decided_at);
CREATE INDEX IF NOT EXISTS audit_stage ON audit (stage);
CREATE TABLE IF NOT EXISTS manual_done (
  smr TEXT PRIMARY KEY, done_at TEXT NOT NULL, user_name TEXT, note TEXT
);
"""
# columns added after the first release; created on open so old databases keep working
_ADDED = {"results": [("duration_ms", "INTEGER")], "runs": [("user_name", "TEXT"), ("label", "TEXT")]}
ACTIONS = ("APPROVE", "EDIT", "CANCEL", "MANUAL")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=":memory:"):
    db = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    if str(path) != ":memory:":
        db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=10000")
    db.executescript(SCHEMA)
    for table, cols in _ADDED.items():
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, kind in cols:
            if name not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    db.commit()
    return db


def log(db, smr, stage, detail: dict):
    db.execute("INSERT INTO audit (ts, smr, stage, detail) VALUES (?,?,?,?)", (now(), smr, stage, json.dumps(detail, ensure_ascii=False, default=str)))


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


# ---- runs ----------------------------------------------------------------------------------------------------------------
def start_run(db, run_id, total, source_file=None, user_name=None, label=None):
    with db:
        db.execute("INSERT OR REPLACE INTO runs (run_id, started_at, total, state, source_file, user_name, label) VALUES (?,?,?,?,?,?,?)",
                   (run_id, now(), total, "running", source_file, user_name, label))


def finish_run(db, run_id, state):
    with db:
        db.execute("UPDATE runs SET finished_at = ?, state = ? WHERE run_id = ?", (now(), state, run_id))


def mark_interrupted_runs(db):
    """Runs left 'running' by a crash or a closed window are marked so the history never shows a ghost run."""
    with db:
        db.execute("UPDATE runs SET state = 'interrupted', finished_at = COALESCE(finished_at, ?) WHERE state IN ('running', 'paused')", (now(),))


def list_runs(db, limit=200):
    runs = []
    for run_id, started, finished, total, state, user_name, label in db.execute(
            "SELECT run_id, started_at, finished_at, total, state, user_name, label FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)):
        counts = dict.fromkeys(ACTIONS, 0)
        for action, n in db.execute("SELECT action, COUNT(*) FROM results WHERE run_id = ? GROUP BY action", (run_id,)):
            counts[action] = n
        runs.append({"run_id": run_id, "started_at": started, "finished_at": finished, "total": total or 0, "state": state,
                     "user_name": user_name, "label": label, "counts": counts, "done": sum(counts.values())})
    return runs


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


# ---- decisions -------------------------------------------------------------------------------------------------------------
def save_result(db, run_id, row, decision, evidence, duration_ms=None):
    with db:
        db.execute(
            "INSERT OR REPLACE INTO results (run_id, smr, site, category, created_at, action, reason_keys, reason_codes, notes, trace, "
            "evidence, decided_at, duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, row["smr"], row.get("site"), row.get("category"), row.get("created_at"), decision.action,
             json.dumps(decision.reason_keys, ensure_ascii=False), json.dumps(decision.reason_codes, ensure_ascii=False),
             json.dumps(decision.notes, ensure_ascii=False), json.dumps(decision.trace, ensure_ascii=False),
             json.dumps(evidence, ensure_ascii=False, default=str), now(), duration_ms))
        log(db, row["smr"], "DECISION", {"run": run_id, "action": decision.action, "reasons": decision.reason_codes})


def done_ids(db, run_id):
    return {r[0] for r in db.execute("SELECT smr FROM results WHERE run_id = ?", (run_id,))}


_RESULT_COLS = ("smr", "site", "category", "created_at", "action", "reason_keys", "reason_codes", "notes", "decided_at", "duration_ms")


def _decode(d: dict) -> dict:
    for k in ("reason_keys", "reason_codes", "notes", "trace"):
        if k in d:
            d[k] = json.loads(d[k] or "[]")
    if "evidence" in d:
        d["evidence"] = json.loads(d["evidence"] or "{}")
    return d


def results_of(db, run_id):
    cur = db.execute(f"SELECT {', '.join(_RESULT_COLS)} FROM results WHERE run_id = ? ORDER BY rowid", (run_id,))
    return [_decode(dict(zip(_RESULT_COLS, r))) for r in cur.fetchall()]


def result_detail(db, run_id, smr):
    cols = _RESULT_COLS + ("trace", "evidence")
    r = db.execute(f"SELECT {', '.join(cols)} FROM results WHERE run_id = ? AND smr = ?", (run_id, smr)).fetchone()
    return _decode(dict(zip(cols, r))) if r else None


def latest_result(db, smr):
    """The most recent review of one request, with its evidence and rule trace (None if it was never reviewed)."""
    r = db.execute("SELECT run_id FROM results WHERE smr = ? ORDER BY decided_at DESC LIMIT 1", (smr,)).fetchone()
    return result_detail(db, r[0], smr) if r else None


def latest_decisions(db) -> dict:
    """{smr: action} - the most recent decision of every request ever reviewed."""
    cur = db.execute("SELECT r.smr, r.action FROM results r JOIN (SELECT smr, MAX(decided_at) m FROM results GROUP BY smr) x "
                     "ON r.smr = x.smr AND r.decided_at = x.m")
    return dict(cur.fetchall())


def latest_states(db) -> dict:
    """{smr: (action, [reason codes], decided_at)} - the most recent decision of every request ever reviewed."""
    cur = db.execute("SELECT r.smr, r.action, r.reason_codes, r.decided_at FROM results r JOIN "
                     "(SELECT smr, MAX(decided_at) m FROM results GROUP BY smr) x ON r.smr = x.smr AND r.decided_at = x.m")
    return {smr: (action, json.loads(codes or "[]"), at) for smr, action, codes, at in cur.fetchall()}


def history(db, smr) -> list:
    """Everything that happened to one request, oldest first: decisions (with run) and audit events."""
    events = [{"ts": at, "kind": "DECISION", "run_id": run, "action": action, "codes": json.loads(codes or "[]"), "notes": json.loads(notes or "[]")}
              for run, action, codes, notes, at in db.execute(
                  "SELECT run_id, action, reason_codes, notes, decided_at FROM results WHERE smr = ? ORDER BY decided_at", (smr,))]
    events += [{"ts": ts, "kind": stage, "detail": json.loads(detail or "{}")}
               for ts, stage, detail in db.execute("SELECT ts, stage, detail FROM audit WHERE smr = ? AND stage != 'DECISION' ORDER BY id", (smr,))]
    return sorted(events, key=lambda e: e["ts"])


def set_manual_done(db, smr, done: bool, user_name=None, note=None):
    with db:
        if done:
            db.execute("INSERT OR REPLACE INTO manual_done (smr, done_at, user_name, note) VALUES (?,?,?,?)", (smr, now(), user_name, note))
        else:
            db.execute("DELETE FROM manual_done WHERE smr = ?", (smr,))
        log(db, smr, "MANUAL_DONE" if done else "MANUAL_REOPENED", {"user": user_name, "note": note})


def manual_done_set(db) -> set:
    return {r[0] for r in db.execute("SELECT smr FROM manual_done")}


# ---- dashboard numbers -----------------------------------------------------------------------------------------------------
def _since(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def daily_counts(db, days=14):
    """-> [(YYYY-MM-DD local, {action: n})] for the last `days` days (days without decisions included as zeros)."""
    out = {}
    for decided, action in db.execute("SELECT decided_at, action FROM results WHERE decided_at >= ?", (_since(days + 1),)):
        day = datetime.fromisoformat(decided).astimezone().strftime("%Y-%m-%d")
        bucket = out.setdefault(day, dict.fromkeys(ACTIONS, 0))
        bucket[action] = bucket.get(action, 0) + 1
    today = datetime.now().astimezone().date()
    series = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        series.append((day, out.get(day, dict.fromkeys(ACTIONS, 0))))
    return series


def totals(db, run_id=None, days=None):
    q, args = "SELECT action, COUNT(*), AVG(duration_ms) FROM results", []
    if run_id:
        q, args = q + " WHERE run_id = ?", [run_id]
    elif days:
        q, args = q + " WHERE decided_at >= ?", [_since(days)]
    counts, avg_ms, weighted = dict.fromkeys(ACTIONS, 0), 0.0, 0
    for action, n, avg in db.execute(q + " GROUP BY action", args):
        counts[action] = n
        if avg:
            avg_ms += avg * n
            weighted += n
    return {"counts": counts, "total": sum(counts.values()), "avg_seconds": (avg_ms / weighted / 1000) if weighted else None}


def reason_counts(db, run_id=None, days=None, limit=10):
    q, args = "SELECT reason_codes FROM results WHERE action IN ('EDIT','CANCEL')", []
    if run_id:
        q, args = q + " AND run_id = ?", [run_id]
    elif days:
        q, args = q + " AND decided_at >= ?", [_since(days)]
    counts = {}
    for (codes,) in db.execute(q, args):
        for c in json.loads(codes or "[]"):
            counts[c] = counts.get(c, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


def manual_causes(db, run_id=None, days=None, limit=10):
    """Why requests went to a person, grouped by the first note's kind ('could not determine: X' -> X)."""
    q, args = "SELECT notes FROM results WHERE action = 'MANUAL'", []
    if run_id:
        q, args = q + " AND run_id = ?", [run_id]
    elif days:
        q, args = q + " AND decided_at >= ?", [_since(days)]
    counts = {}
    for (notes,) in db.execute(q, args):
        n = (json.loads(notes or "[]") or ["?"])[0]
        key = cause_key(n)
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])[:limit]


def cause_key(note: str) -> str:
    n = str(note or "")
    if n.startswith("could not determine: product count"):
        return "could not determine: product count"
    if n.startswith("blocked: the website redirects"):
        return "blocked: redirect to another domain"
    if n.startswith("blocked: the website is a page on a shared platform"):
        return "blocked: social-media page"
    if n.startswith("internal error"):
        return "internal error"
    if n.startswith("another pending request"):
        return "another pending request for the same website"
    return n.split(" (")[0]
