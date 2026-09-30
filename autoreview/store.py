"""SQLite store: imported requests + an append-only audit log (who/what/when for every request)."""
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
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=":memory:"):
    db = sqlite3.connect(path)
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
