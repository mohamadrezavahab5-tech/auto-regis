import csv

import pytest

from autoreview import imports, store
from autoreview.cli import main


def write_csv(path, rows, width=40):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for r in rows:
            row = [""] * width
            row[0], row[3], row[32] = r
            w.writerow(row)


def test_read_export_uses_positions_and_skips_header(tmp_path):
    f = tmp_path / "nbo.csv"
    write_csv(f, [("SMR", "Status", "Site"), ("SMR-1", "COMPLETED", "https://a.ir"), ("", "X", "y")])
    rows = imports.read_export(f, "nbo")
    assert len(rows) == 1
    assert {k: rows[0][k] for k in ("smr", "status", "site")} == {"smr": "SMR-1", "status": "COMPLETED", "site": "https://a.ir"}


def test_too_narrow_file_fails_loudly(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="layout changed"):
        imports.read_export(f, "nbo")


def test_import_then_dupes_end_to_end(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    nbo, crm, pend = tmp_path / "nbo.csv", tmp_path / "crm.csv", tmp_path / "pend.csv"
    write_csv(nbo, [("h", "h", "h"), ("SMR-9", "COMPLETED", "a.ir"), ("SMR-8", "PENDING", "c.ir")])
    crm_rows = [[""] * 18 for _ in range(2)]
    crm_rows[1][0], crm_rows[1][8], crm_rows[1][17] = "MRG-1", "درخواست تایید شده است", "http://b.ir"
    with open(crm, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(crm_rows)
    write_csv(pend, [("h", "h", "h"), ("SMR-1", "PENDING", "www.a.ir"), ("SMR-2", "PENDING", "b.ir"), ("SMR-3", "PENDING", "c.ir")])
    main(["--db", db, "import", "nbo", str(nbo)])
    main(["--db", db, "import", "crm", str(crm)])
    main(["--db", db, "dupes", str(pend)])
    out = capsys.readouterr().out
    assert "3 pending, 2 duplicates" in out          # c.ir is only PENDING in nbo, not approved
    d = store.connect(db)
    assert d.execute("SELECT COUNT(*) FROM audit WHERE stage='DUPLICATE_FOUND'").fetchone()[0] == 2
