"""The data a review works on - NBO pending + approved, CRM approved - each saved with WHEN and FROM WHERE it was loaded.

Kept in the person's profile so the app can show (and reuse) the last load after a restart. The age is always shown next to
it, and a run on stale data asks first."""
import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone

from . import backlog, imports
from .paths import data_dir

STALE_HOURS = 12


def _file(name):
    d = data_dir() / "sources"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _write(name, data):
    target = _file(name)
    fd, tmp = tempfile.mkstemp(prefix=name, suffix=".tmp", dir=str(target.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, target)


def _read(name):
    try:
        return json.loads(_file(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_stale(meta, hours=STALE_HOURS) -> bool:
    if not meta or not meta.get("loaded_at"):
        return True
    return (datetime.now(timezone.utc) - datetime.fromisoformat(meta["loaded_at"])).total_seconds() > hours * 3600


def build_nbo(rows, rules, origin):
    """From the rows of an NBO export: today's backlog (online-only individual pending, newest first), the Online+InStore
    pending requests (Online-Instore sheet flow), the approved set for the duplicate check and the count of every status."""
    cfg = rules["backlog"]
    picked, skipped = backlog.select(rows, cfg, include_optional=bool(cfg.get("include_optional")))
    both = [r for r in rows if r.get("status") in set(cfg["statuses"]) | set(cfg.get("optional_statuses", []))
            and str(r.get("has_online", "")).lower() == "true" and str(r.get("has_instore", "")).lower() == "true"]
    approved = [{"id": r["smr"], "site": r.get("site", "")} for r in imports.approved_rows(rows, rules["approved_statuses"]["nbo"])]
    return {"loaded_at": now_iso(), "origin": origin, "rows": len(rows), "status_counts": dict(Counter(r.get("status") or "?" for r in rows)),
            "backlog": picked, "both_channels": both, "approved": approved, "skipped": skipped}


def save_nbo(snapshot):
    _write("nbo.json", snapshot)


def load_nbo():
    return _read("nbo.json")


def save_crm(approved_rows, origin, total=None):
    snap = {"loaded_at": now_iso(), "origin": origin, "rows": total if total is not None else len(approved_rows),
            "approved": [{"id": r["smr"], "site": r.get("site", "")} for r in approved_rows]}
    _write("crm.json", snap)
    return snap


def load_crm():
    return _read("crm.json")
