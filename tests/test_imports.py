import csv

import pytest

from autoreview import imports, store
from autoreview.cli import main

HEADERS = ["Request ID", "Created At", "Updated At", "Status", "Required Editing Reason", "Cancel Reason", "Registration Assignee",
           "Brand Name (fa)", "Brand Name (en)", "Category (fa)", "Ownership Type", "Instagram URL", "IBAN",
           "Account Holder Name (IBAN Owner)", "Has Online", "Online Payment Term", "Online Commission", "Has InStore",
           "InStore Payment Term", "InStore Commission", "Owner National Id", "Owner Name", "Owner Family", "Owner Mobile No.",
           "Owner Email", "Owner Postal Code", "Owner Province", "Owner City", "Owner Address", "Owner Bank",
           "Requester Mobile No.", "Representative Mobile No.", "Website URL", "Website Infra", "Province", "City", "Branch Count",
           "Branch Address", "Transaction Platform (M Panel or Cashier)", "Referral Code"]


def nbo_csv(path, rows, headers=HEADERS):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for smr, status, site in rows:
            r = dict.fromkeys(headers, "")
            r.update({"Request ID": smr, "Status": status, "Website URL": site, "Has Online": "true", "Has InStore": "false",
                      "Ownership Type": "INDIVIDUAL", "Category (fa)": "مد و پوشاک"})
            w.writerow([r[h] for h in headers])


def test_read_export_finds_columns_by_header_name(tmp_path):
    f = tmp_path / "nbo.csv"
    nbo_csv(f, [("SMR-1", "COMPLETED", "https://a.ir"), ("", "X", "y")])
    rows = imports.read_export(f, "nbo")
    assert len(rows) == 1
    assert {k: rows[0][k] for k in ("smr", "status", "site", "category", "has_online")} == \
        {"smr": "SMR-1", "status": "COMPLETED", "site": "https://a.ir", "category": "مد و پوشاک", "has_online": "true"}


def test_reordered_columns_still_read_correctly(tmp_path):
    f = tmp_path / "nbo.csv"
    nbo_csv(f, [("SMR-1", "PENDING", "b.ir")], headers=list(reversed(HEADERS)))
    assert imports.read_export(f, "nbo")[0]["site"] == "b.ir"


def test_renamed_or_missing_column_fails_loudly(tmp_path):
    f = tmp_path / "nbo.csv"
    nbo_csv(f, [("SMR-1", "PENDING", "b.ir")], headers=[h if h != "Website URL" else "Site" for h in HEADERS])
    with pytest.raises(imports.ImportLayoutError, match="Website URL"):
        imports.read_export(f, "nbo")
    other = tmp_path / "other.csv"
    other.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    with pytest.raises(imports.ImportLayoutError):
        imports.read_export(other, "nbo")


def test_crm_file_columns_are_recognised_by_content(tmp_path):
    f = tmp_path / "crm.csv"
    with open(f, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["نام تجاری", "وضعیت درخواست پذیرنده", "کد درخواست", "آدرس سایت"])
        w.writerow(["الف", "درخواست تایید شده است", "MRG-1", "https://a.ir"])
        w.writerow(["ب", "لغو درخواست", "MRG-2", "b.ir"])
        w.writerow(["ج", "اتمام فعال سازی فنی", "MRG-3", ""])
    rows = imports.read_export(f, "crm")
    assert rows[0] == {"smr": "MRG-1", "status": "درخواست تایید شده است", "site": "https://a.ir"} and len(rows) == 3


def test_import_then_dupes_end_to_end(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    nbo, crm, pend = tmp_path / "nbo.csv", tmp_path / "crm.csv", tmp_path / "pend.csv"
    nbo_csv(nbo, [("SMR-9", "COMPLETED", "a.ir"), ("SMR-8", "PENDING", "c.ir")])
    with open(crm, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "state", "web"])
        for i, (st, site) in enumerate([("درخواست تایید شده است", "http://b.ir"), ("لغو درخواست", "x.ir"), ("ثبت نام اولیه", "y.ir")]):
            w.writerow([f"MRG-{i + 1}", st, site])
    nbo_csv(pend, [("SMR-1", "PENDING", "www.a.ir"), ("SMR-2", "PENDING", "b.ir"), ("SMR-3", "PENDING", "c.ir")])
    main(["--db", db, "import", "nbo", str(nbo)])
    main(["--db", db, "import", "crm", str(crm)])
    main(["--db", db, "dupes", str(pend)])
    out = capsys.readouterr().out
    assert "3 pending, 2 duplicates" in out          # c.ir is only PENDING in nbo, not approved
    d = store.connect(db)
    assert d.execute("SELECT COUNT(*) FROM audit WHERE stage='DUPLICATE_FOUND'").fetchone()[0] == 2
