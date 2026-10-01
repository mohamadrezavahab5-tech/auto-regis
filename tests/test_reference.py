from datetime import datetime, timedelta, timezone

from autoreview import crm_sync, reference, settings, store

RULES = settings.load_rules()


def nbo(smr, status, site, online="true", instore="false", owner="INDIVIDUAL", created="1405/07/08"):
    return {"smr": smr, "status": status, "site": site, "category": "مد و پوشاک", "ownership": owner, "has_online": online,
            "has_instore": instore, "account_holder": "علی رضایی", "owner_name": "علی", "owner_family": "رضایی", "created_at": created,
            "brand_fa": "فروشگاه " + smr}


def crm(caseid, status, site, store="آنلاین", modified="2026-09-30T10:00:00Z"):
    return {"caseid": caseid, "status": status, "site": site, "brand": "برند " + caseid, "person_company": "حقیقی", "store_type": store,
            "created_on": "2026-01-01T00:00:00Z", "modified_on": modified}


def test_nbo_import_replaces_and_gives_backlog_both_channels_and_approved(isolated_profile):
    db = store.connect(isolated_profile / "r.db")
    reference.import_nbo(db, [nbo("A", "PENDING", "a.ir", created="1405/07/01"), nbo("B", "PENDING", "b.ir", created="1405/07/08"),
                              nbo("C", "PENDING", "c.ir", instore="true"), nbo("D", "COMPLETED", "https://www.d.ir/"),
                              nbo("E", "PENDING", "e.ir", owner="LEGAL")], "file:x.xlsx")
    picked, skipped = reference.backlog_rows(db, RULES)
    assert [r["smr"] for r in picked] == ["B", "A"]                          # newest first
    assert [r["smr"] for r in reference.both_channel_rows(db, RULES)] == ["C"]
    nbo_ok, _ = reference.approved_sets(db, RULES)
    assert nbo_ok == [{"id": "D", "site": "https://www.d.ir/"}]
    reference.import_nbo(db, [nbo("Z", "PENDING", "z.ir")], "auto")
    assert [r["smr"] for r in reference.all_nbo_rows(db)] == ["Z"] and reference.meta(db, "nbo")["origin"] == "auto"


def test_crm_incremental_merge_and_store_type_filter(isolated_profile):
    db = store.connect(isolated_profile / "r.db")
    reference.upsert_crm(db, [crm("MRG-1", "درخواست تایید شده است", "a.ir"), crm("MRG-2", "اتمام فعال سازی فنی", "b.ir", store="آفلاین"),
                              crm("MRG-3", "لغو درخواست", "c.ir"), crm("MRG-4", "درحال فعال سازی فنی", "d.ir", store="")], "auto", full=True)
    _, crm_ok = reference.approved_sets(db, RULES)
    assert sorted(x["id"] for x in crm_ok) == ["MRG-1", "MRG-4"]            # offline excluded, unknown store type kept
    reference.upsert_crm(db, [crm("MRG-3", "درخواست تایید شده است", "c.ir", modified="2026-10-01T09:00:00Z")], "auto")
    assert reference.meta(db, "crm")["rows"] == 4 and reference.meta(db, "crm")["watermark"] == "2026-10-01T09:00:00Z"
    _, crm_ok = reference.approved_sets(db, RULES)
    assert sorted(x["id"] for x in crm_ok) == ["MRG-1", "MRG-3", "MRG-4"]


def test_search_by_website_code_and_name(isolated_profile):
    db = store.connect(isolated_profile / "r.db")
    reference.import_nbo(db, [nbo("SMR-1", "PENDING", "https://www.shop.ir/x"), nbo("SMR-2", "CANCELLED", "other.ir")], "auto")
    reference.upsert_crm(db, [crm("MRG-9", "لغو درخواست", "http://shop.ir")], "auto", full=True)
    hits = reference.search(db, "shop.ir")
    assert {(h["source"], h.get("smr") or h.get("caseid")) for h in hits} == {("NBO", "SMR-1"), ("CRM", "MRG-9")}
    assert [h["smr"] for h in reference.search(db, "smr-2")] == ["SMR-2"]
    assert reference.search(db, "رضایی")[0]["source"] == "NBO"
    assert reference.search(db, "x") == []
    counts = reference.status_counts(db)
    assert counts["nbo"] == {"PENDING": 1, "CANCELLED": 1} and counts["crm"] == {"لغو درخواست": 1}


def test_staleness():
    old = {"loaded_at": (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()}
    assert reference.is_stale(old) and reference.is_stale(None) and not reference.is_stale({"loaded_at": reference.now_iso()})


def test_crm_reference_fetch_maps_fields_and_filters_by_modified_time():
    seen = []
    page = {"value": [{"new_caseid": "MRG-1", "new_merchantstatus": 5, "new_merchantstatus" + crm_sync.FORMATTED: "درخواست تایید شده است",
                       "new_urlsite": "a.ir", "new_brandname": "الف", "new_personcompany" + crm_sync.FORMATTED: "حقیقی",
                       "new_new_store_type" + crm_sync.FORMATTED: "آنلاین", "createdon": "2026-01-01T00:00:00Z", "modifiedon": "2026-09-30T10:00:00Z"}]}
    rows = crm_sync.fetch_reference(since="2026-09-30T00:00:00Z", runner=lambda p: seen.append(p) or page)
    assert rows[0] == {"caseid": "MRG-1", "status": "درخواست تایید شده است", "site": "a.ir", "brand": "الف", "person_company": "حقیقی",
                       "store_type": "آنلاین", "created_on": "2026-01-01T00:00:00Z", "modified_on": "2026-09-30T10:00:00Z"}
    assert "modifiedon%20gt%202026-09-30T00%3A00%3A00Z" in seen[0]


def test_identity_is_stored_only_as_a_fingerprint_and_links_related_requests():
    from autoreview import store
    db = store.connect()
    reference.ensure(db)
    base = dict(status="PENDING", has_online="true", has_instore="false", created_at="1405/07/09")
    reference.import_nbo(db, [
        dict(base, smr="SMR-1", site="a.ir", owner_national_id="0012345678", iban="IR12 0170 0000 0000 0000 0001"),
        dict(base, smr="SMR-2", site="b.ir", status="COMMERCIAL_APPROVED", owner_national_id="۰۰۱۲۳۴۵۶۷۸", iban="IR990170000000000000000002"),
        dict(base, smr="SMR-3", site="c.ir", owner_national_id="9999999999", iban="ir12-0170-0000-0000-0000-0001"),
    ], "test")
    dump = "\n".join(str(r) for r in db.execute("SELECT * FROM ref_nbo"))
    assert "0012345678" not in dump and "IR12" not in dump and "0170" not in dump
    rel = reference.related(db, "SMR-1")
    assert [r[0] for r in rel["same_owner"]] == ["SMR-2"]                 # Persian digits = same national ID
    assert [r[0] for r in rel["same_iban"]] == ["SMR-3"] and rel["iban_other_owner"]
    assert reference.related(db, "SMR-404") == {"same_owner": [], "same_iban": [], "iban_other_owner": False}


def test_an_old_database_gets_the_new_columns():
    from autoreview import store
    db = store.connect()
    db.executescript("CREATE TABLE ref_nbo (smr TEXT PRIMARY KEY, status TEXT, site TEXT, site_key TEXT, category TEXT, ownership TEXT, "
                     "has_online TEXT, has_instore TEXT, account_holder TEXT, owner_name TEXT, owner_family TEXT, created_at TEXT, "
                     "brand_fa TEXT, edit_reason TEXT, cancel_reason TEXT)")
    reference.ensure(db)
    assert {"owner_key", "iban_key"} <= {r[1] for r in db.execute("PRAGMA table_info(ref_nbo)")}
