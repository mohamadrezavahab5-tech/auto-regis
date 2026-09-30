"""Command line: python -m autoreview <command>. Read-only towards NBO/CRM/Sheets - it only reads exports and writes its own db."""
import argparse
import json
import sys
from pathlib import Path

from . import duplicates, imports, store

ROOT = Path(__file__).resolve().parent.parent
RULES = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8"))


def cmd_import(a):
    rows = imports.read_export(a.file, a.source)
    db = store.connect(a.db)
    store.replace_source(db, a.source, rows)
    print(f"{a.source}: {len(rows)} rows imported into {a.db}")


def cmd_dupes(a):
    db = store.connect(a.db)
    nbo_ok = imports.approved_rows(store.rows_of(db, "nbo"), RULES["approved_statuses"]["nbo"])
    crm_ok = imports.approved_rows(store.rows_of(db, "crm"), RULES["approved_statuses"]["crm"])
    pending = imports.read_export(a.pending, "nbo")
    res = duplicates.find_duplicates(pending, nbo_ok, crm_ok)
    dup = [r for r in res if r["is_duplicate"]]
    for r in dup:
        store.log(db, r["id"], "DUPLICATE_FOUND", r)
    db.commit()
    print(f"{len(pending)} pending, {len(dup)} duplicates (approved: nbo={len(nbo_ok)}, crm={len(crm_ok)})")
    for r in dup[:50]:
        print(f"  {r['id']}  {r['site']}  ~ {', '.join(r['related'])}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="autoreview")
    p.add_argument("--db", default=str(ROOT / "data" / "autoreview.db"))
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("import", help="load an NBO or CRM export (replaces that source)")
    i.add_argument("source", choices=["nbo", "crm"]); i.add_argument("file"); i.set_defaults(fn=cmd_import)
    d = sub.add_parser("dupes", help="duplicate check of a pending export against the approved sets (no action taken)")
    d.add_argument("pending"); d.set_defaults(fn=cmd_dupes)
    a = p.parse_args(argv)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
