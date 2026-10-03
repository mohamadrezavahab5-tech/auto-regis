"""The reference data the old 'Main' sheet held - now in the person's own database.

What Main did, and where it lives now:
  NBO Data / CRM Data tabs (Update EMIO / Update CRM scripts)  -> ref_nbo / ref_crm tables (NBO: full export each time;
                                                                   CRM: full once, then only what changed since last time)
  EMIO Approved / CRM Approved                                  -> approved_sets()
  Daily Pending (tick + matched names)                          -> the duplicate check in the runner (uses approved_sets)
  Dashboard (count per status)                                  -> status_counts()
  'Has this website had a request before?' (NBO cannot search   -> search()
   by website)
Every load records when and from where, so the app always shows how fresh the data is."""
import re
from datetime import datetime, timezone

from .duplicates import site_key
from .imports import status_key
from .normalize import normalize_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS ref_nbo (
  smr TEXT PRIMARY KEY, status TEXT, site TEXT, site_key TEXT, category TEXT, ownership TEXT, has_online TEXT, has_instore TEXT,
  account_holder TEXT, owner_name TEXT, owner_family TEXT, created_at TEXT, brand_fa TEXT, edit_reason TEXT, cancel_reason TEXT,
  owner_key TEXT, iban_key TEXT
);
CREATE INDEX IF NOT EXISTS ref_nbo_site ON ref_nbo (site_key);
CREATE TABLE IF NOT EXISTS ref_crm (
  caseid TEXT PRIMARY KEY, status TEXT, site TEXT, site_key TEXT, brand TEXT, person_company TEXT, store_type TEXT,
  created_on TEXT, modified_on TEXT
);
CREATE INDEX IF NOT EXISTS ref_crm_site ON ref_crm (site_key);
CREATE TABLE IF NOT EXISTS ref_meta (source TEXT PRIMARY KEY, loaded_at TEXT, origin TEXT, rows INTEGER, watermark TEXT);
"""
NBO_FIELDS = ("smr", "status", "site", "site_key", "category", "ownership", "has_online", "has_instore", "account_holder", "owner_name",
              "owner_family", "created_at", "brand_fa", "edit_reason", "cancel_reason", "owner_key", "iban_key")
# Identity fingerprints: the owner's national ID and the IBAN are NEVER stored - only a keyed hash, enough to say "same owner /
# same bank account as request X" and useless for anything else.
_FP_SALT = b"AutoReview/identity-fingerprint/v1"
_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
CRM_FIELDS = ("caseid", "status", "site", "site_key", "brand", "person_company", "store_type", "created_on", "modified_on")
STALE_HOURS = 12


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure(db):
    db.executescript(SCHEMA)
    have = {r[1] for r in db.execute("PRAGMA table_info(ref_nbo)")}
    for col in ("owner_key", "iban_key"):                       # databases from before 2026-10-02
        if col not in have:
            db.execute(f"ALTER TABLE ref_nbo ADD COLUMN {col} TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS ref_nbo_owner ON ref_nbo (owner_key)")
    db.execute("CREATE INDEX IF NOT EXISTS ref_nbo_iban ON ref_nbo (iban_key)")
    db.commit()


def fingerprint(value) -> str:
    """'۰۰۱۲۳۴۵۶۷۸' / 'IR12 0170 ...' -> a stable 24-hex fingerprint ('' when empty)."""
    import hashlib
    import hmac
    s = "".join(ch for ch in str(value or "").translate(_LATIN).upper() if ch.isalnum())
    return hmac.new(_FP_SALT, s.encode(), hashlib.sha256).hexdigest()[:24] if s else ""


def _set_meta(db, source, origin, rows, watermark=None):
    db.execute("INSERT OR REPLACE INTO ref_meta (source, loaded_at, origin, rows, watermark) VALUES (?,?,?,?,?)",
               (source, now_iso(), origin, rows, watermark))


def meta(db, source):
    ensure(db)
    r = db.execute("SELECT loaded_at, origin, rows, watermark FROM ref_meta WHERE source = ?", (source,)).fetchone()
    return dict(zip(("loaded_at", "origin", "rows", "watermark"), r)) if r else None


def is_stale(m, hours=STALE_HOURS) -> bool:
    if not m or not m.get("loaded_at"):
        return True
    return (datetime.now(timezone.utc) - datetime.fromisoformat(m["loaded_at"])).total_seconds() > hours * 3600


# ---- loading -------------------------------------------------------------------------------------------------------------
# A partial refresh fetches only the work queue (Pending, Commercial in progress). A request that was in it and is not any
# more was decided in NBO - approved, sent for editing or cancelled - but which one is only known after the next full
# export. Until then it keeps its row with this status instead of vanishing: deleting it hid a just-approved site from the
# duplicate check for hours (bug hunt 2026-10-02).
LEFT_QUEUE = "LEFT_QUEUE"


def import_nbo(db, rows, origin, only_statuses=None):
    """A full NBO export REPLACES the NBO reference (same as the old 'clear + rewrite' of the NBO tab).
    only_statuses: a partial refresh (some statuses could not be exported this time) - only rows that HAD one of these
    statuses are replaced; every other row keeps its last known value."""
    ensure(db)
    def value(r, f):
        if f == "site_key":
            return site_key(r.get("site"))
        if f == "owner_key":
            return fingerprint(r.get("owner_national_id"))
        if f == "iban_key":
            return fingerprint(r.get("iban"))
        return r.get(f) or ""
    data = [tuple(value(r, f) for f in NBO_FIELDS) for r in rows]
    with db:
        if only_statuses:
            db.executemany("UPDATE ref_nbo SET status = ? WHERE status = ?", [(LEFT_QUEUE, s) for s in only_statuses])
        else:
            db.execute("DELETE FROM ref_nbo")
        db.executemany(f"INSERT OR REPLACE INTO ref_nbo ({', '.join(NBO_FIELDS)}) VALUES ({', '.join('?' * len(NBO_FIELDS))})", data)
        total = db.execute("SELECT COUNT(*) FROM ref_nbo").fetchone()[0]
        _set_meta(db, "nbo", origin + (" (partial)" if only_statuses else ""), total)
    return len(data)


def upsert_crm(db, rows, origin, full=False):
    """CRM rows ({caseid, status, site, brand, person_company, store_type, created_on, modified_on}). full=True replaces
    everything; otherwise the rows are merged in (incremental: only what changed since the last load)."""
    ensure(db)
    data = [tuple(site_key(r.get("site")) if f == "site_key" else (r.get(f) or "") for f in CRM_FIELDS) for r in rows]
    with db:
        if full:
            db.execute("DELETE FROM ref_crm")
        db.executemany(f"INSERT OR REPLACE INTO ref_crm ({', '.join(CRM_FIELDS)}) VALUES ({', '.join('?' * len(CRM_FIELDS))})", data)
        total = db.execute("SELECT COUNT(*) FROM ref_crm").fetchone()[0]
        mark = db.execute("SELECT MAX(modified_on) FROM ref_crm").fetchone()[0]
        _set_meta(db, "crm", origin, total, mark)
    return len(data)


# ---- what the review uses -----------------------------------------------------------------------------------------------
def _nbo_rows(db, where="", args=()):
    cur = db.execute(f"SELECT {', '.join(NBO_FIELDS)} FROM ref_nbo {where}", args)
    return [dict(zip(NBO_FIELDS, r)) for r in cur.fetchall()]


def all_nbo_rows(db):
    ensure(db)
    return _nbo_rows(db)


def created_dates(db) -> dict:
    """{smr: NBO registration date} - two columns instead of every field of ~70k rows (0.3 s on the UI thread)."""
    ensure(db)
    return dict(db.execute("SELECT smr, created_at FROM ref_nbo").fetchall())


def approved_sets(db, rules):
    """-> (nbo approved [{id, site}], crm approved [{id, site}]). CRM: approved statuses AND a store type that includes
    online (the team's CRM reference was 'Store Type = Online'); an empty list in the settings means 'any store type'."""
    ensure(db)
    nbo_ok = {status_key(s) for s in rules["approved_statuses"]["nbo"]}
    crm_ok = {status_key(s) for s in rules["approved_statuses"]["crm"]}
    store_types = [normalize_text(s).replace(" ", "") for s in rules.get("crm_store_types", [])]
    nbo = [{"id": smr, "site": site} for smr, status, site in db.execute("SELECT smr, status, site FROM ref_nbo") if status_key(status) in nbo_ok]
    crm = []
    for caseid, status, site, store in db.execute("SELECT caseid, status, site, store_type FROM ref_crm"):
        if status_key(status) not in crm_ok:
            continue
        if store_types and store and normalize_text(store).replace(" ", "") not in store_types:
            continue                                           # a row without a store type is kept (unknown is not 'offline')
        crm.append({"id": caseid, "site": site})
    return nbo, crm


def status_counts(db):
    ensure(db)
    nbo = dict(db.execute("SELECT COALESCE(NULLIF(status, ''), '?'), COUNT(*) FROM ref_nbo GROUP BY 1 ORDER BY 2 DESC").fetchall())
    crm = dict(db.execute("SELECT COALESCE(NULLIF(status, ''), '?'), COUNT(*) FROM ref_crm GROUP BY 1 ORDER BY 2 DESC").fetchall())
    return {"nbo": nbo, "crm": crm}


# ---- the lookup the old Main sheet was used for -------------------------------------------------------------------------
_CODE = re.compile(r"^(SMR|MRG)-\d+$", re.I)


def search(db, query: str, limit=300):
    """Website / request code / name -> every NBO and CRM record that matches, newest first."""
    ensure(db)
    q = (query or "").strip()
    if len(q) < 2:
        return []
    out = []
    if _CODE.match(q):
        code = q.upper()
        out += [dict(r, source="NBO") for r in _nbo_rows(db, "WHERE upper(smr) = ?", (code,))]
        out += [dict(zip(CRM_FIELDS, r), source="CRM") for r in db.execute(f"SELECT {', '.join(CRM_FIELDS)} FROM ref_crm WHERE upper(caseid) = ?", (code,))]
        return out
    key = site_key(q)
    if key:
        out += [dict(r, source="NBO") for r in _nbo_rows(db, "WHERE site_key = ? ORDER BY created_at DESC", (key,))]
        out += [dict(zip(CRM_FIELDS, r), source="CRM") for r in db.execute(
            f"SELECT {', '.join(CRM_FIELDS)} FROM ref_crm WHERE site_key = ? ORDER BY created_on DESC", (key,))]
        if out:
            return out[:limit]
    like = f"%{q}%"
    out += [dict(r, source="NBO") for r in _nbo_rows(
        db, "WHERE site LIKE ? OR account_holder LIKE ? OR brand_fa LIKE ? OR (owner_name || ' ' || owner_family) LIKE ? "
            "ORDER BY created_at DESC LIMIT ?", (like, like, like, like, limit))]
    out += [dict(zip(CRM_FIELDS, r), source="CRM") for r in db.execute(
        f"SELECT {', '.join(CRM_FIELDS)} FROM ref_crm WHERE site LIKE ? OR brand LIKE ? ORDER BY created_on DESC LIMIT ?", (like, like, limit))]
    return out[:limit]


def left_queue_rows(db):
    return [dict(smr=smr, site=site, status=LEFT_QUEUE) for smr, site in
            db.execute("SELECT smr, site FROM ref_nbo WHERE status = ?", (LEFT_QUEUE,))]


def legal_rows(db, rules):
    """CRM registrations of legal persons (company contracts) - every CRM status (owner 2026-10-02: "all of them"), or only
    those in rules['legal']['crm_statuses'] when that is a list. Oldest first, so the Legal tab keeps its order as new ones
    arrive."""
    cfg = rules.get("legal", {})
    if not cfg.get("enabled", True):
        return []
    statuses = cfg.get("crm_statuses", "all")
    where, args = "person_company = ?", [cfg.get("person_company", "حقوقی")]
    if statuses != "all":
        if not statuses:
            return []
        where += f" AND status IN ({','.join('?' * len(statuses))})"
        args += list(statuses)
    cur = db.execute(f"SELECT caseid, status, site, brand, created_on, modified_on FROM ref_crm WHERE {where} "
                     "ORDER BY created_on, caseid", args)
    return [dict(zip(("caseid", "status", "site", "brand", "created_on", "modified_on"), r)) for r in cur]


def backlog_rows(db, rules, include_optional=None):
    """Today's backlog from the NBO reference: online-only, individual, pending (newest first, the owner's order)."""
    from . import backlog
    cfg = rules["backlog"]
    opt = cfg.get("include_optional") if include_optional is None else include_optional
    picked, skipped = backlog.select(all_nbo_rows(db), cfg, include_optional=bool(opt))
    return picked, skipped


def both_channel_rows(db, rules, include_optional=None):
    """Pending requests that are Online AND InStore - the Online-Instore sheet flow (never acted on in NBO by this app)."""
    cfg = rules["backlog"]
    opt = cfg.get("include_optional") if include_optional is None else include_optional
    statuses = set(cfg["statuses"]) | (set(cfg.get("optional_statuses", [])) if opt else set())
    # the same ownership as the Online-only queue: NBO's legal (company) requests are not this team's work - they are
    # the Legal flow's (owner 2026-10-03: "we have nothing to do with them, in reviews and approvals too")
    owner = cfg.get("ownership")
    return [r for r in all_nbo_rows(db) if r["status"] in statuses and str(r["has_online"]).lower() == "true"
            and str(r["has_instore"]).lower() == "true" and (not owner or r.get("ownership") == owner)]


def related(db, smr, limit=8):
    """Other requests of the same owner (national ID) or paid to the same bank account (IBAN), from the NBO reference.
    -> {'same_owner': [(smr, status, site)], 'same_iban': [...], 'iban_other_owner': bool}"""
    me = db.execute("SELECT owner_key, iban_key FROM ref_nbo WHERE smr = ?", (smr,)).fetchone()
    if not me:
        return {"same_owner": [], "same_iban": [], "iban_other_owner": False}
    owner, iban = me

    def others(col, key):
        if not key:
            return []
        return db.execute(f"SELECT smr, status, site, owner_key FROM ref_nbo WHERE {col} = ? AND smr != ? ORDER BY created_at DESC LIMIT ?",
                          (key, smr, limit)).fetchall()
    same_owner, same_iban = others("owner_key", owner), others("iban_key", iban)
    return {"same_owner": [r[:3] for r in same_owner], "same_iban": [r[:3] for r in same_iban],
            "iban_other_owner": any(r[3] and owner and r[3] != owner for r in same_iban)}
