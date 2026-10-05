import json
from pathlib import Path

R = Path(r"C:\AutoReview")


def sub(rel, old, new):
    p = R / rel
    t = p.read_text(encoding="utf-8")
    assert old in t, (rel, old)
    p.write_text(t.replace(old, new, 1), encoding="utf-8")


# 1) CRM mapping discovered from the live Dynamics metadata (read-only, 2026-10-01)
cfg = {
    "_note": "Discovered read-only from the live CRM metadata. Entity 'new_merchantregistration' (list: Merchant Registrations). smr = new_caseid (code MRG-...), "
             "status = new_merchantstatus (picklist, label read from the formatted value), site = new_urlsite. The OData filter keeps only the approved statuses: "
             "100000005 request approved, 100000001 technical activation in progress, 100000003 technical activation finished.",
    "entity_set": "new_merchantregistrations",
    "fields": {"smr": "new_caseid", "status": "new_merchantstatus", "site": "new_urlsite"},
    "status_labels_are_formatted_values": True,
    "filter": "new_merchantstatus eq 100000005 or new_merchantstatus eq 100000001 or new_merchantstatus eq 100000003",
}
(R / "config" / "crm_api.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

rules_p = R / "config" / "rules.json"
rules = json.loads(rules_p.read_text(encoding="utf-8"))
rules["approved_statuses"]["crm"] = ["درخواست تایید شده است", "درحال فعال سازی فنی", "اتمام فعال سازی فنی"]
rules["approved_statuses"]["_crm_note"] = ("Labels of CRM picklist new_merchantstatus. The old script's 'تایید قرارداد' does not exist there; "
                                            "'درخواست تایید شده است' is the approved state. 'ثبت نام انجام شده' (20k rows) is NOT counted as approved - owner to confirm.")
rules_p.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")

# 2) formatted values need the annotations header
sub("scripts/crm-get.ps1", "'Prefer' = 'odata.maxpagesize=5000'", "'Prefer' = 'odata.include-annotations=\"*\",odata.maxpagesize=5000'")

# 3) encode spaces in the filter
sub("autoreview/crm_sync.py", "quote(m['filter'], safe='(),' + chr(39) + ' ')", "quote(m['filter'], safe='(),' + chr(39))")

# 4) status labels compare ignoring spacing / Arabic letter variants
sub("autoreview/imports.py", '''    wanted = {s.strip() for s in statuses}
    return [r for r in rows if r.get("status", "").strip() in wanted]''',
    '''    wanted = {_key(s) for s in statuses}
    return [r for r in rows if _key(r.get("status", "")) in wanted]''')
sub("autoreview/imports.py", "def approved_rows(rows, statuses):",
    '''def _key(label) -> str:
    """Status labels differ in spacing between systems ('فعالسازی' vs 'فعال سازی'): compare without spaces/ZWNJ."""
    return normalize_text(label).replace(" ", "")


def approved_rows(rows, statuses):''')
sub("autoreview/imports.py", "from .paths import config_dir", "from .normalize import normalize_text\nfrom .paths import config_dir")

# 5) never treat a shared platform as 'the same website'
sub("autoreview/duplicates.py", "from .normalize import normalize_site",
    '''from .normalize import normalize_site

# A page on a shared platform (instagram.com/shop-a vs instagram.com/shop-b) is not the same website: never a duplicate key.
SHARED_HOSTS = {"instagram.com", "t.me", "telegram.me", "telegram.org", "wa.me", "linktr.ee", "facebook.com", "twitter.com", "x.com",
                "youtube.com", "aparat.com", "digikala.com", "divar.ir", "sheypoor.com", "google.com", "sites.google.com", "eitaa.com", "rubika.ir"}


def _site_key(value):
    s = normalize_site(value)
    return None if (not s or s in SHARED_HOSTS or s.rsplit(".", 2)[-2:] == ["blogspot", "com"]) else s''')
sub("autoreview/duplicates.py", "        site = normalize_site(r.get(site_key))\n        if site:", "        site = _site_key(r.get(site_key))\n        if site:")
sub("autoreview/duplicates.py", 'rid, site = str(r.get("id") or "").strip(), normalize_site(r.get("site"))', 'rid, site = str(r.get("id") or "").strip(), _site_key(r.get("site"))')
print("ok")
