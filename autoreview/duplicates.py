"""Duplicate detection: a pending request is a duplicate when its website already exists in an APPROVED set (NBO or CRM).

Only a real website is a duplicate key: a page on a shared platform (instagram.com/shop-a vs instagram.com/shop-b) is not
"the same website", and junk values ('0', '-', 'ندارد') are not websites at all."""
import re

from .normalize import normalize_site

SHARED_HOSTS = {
    "instagram.com", "t.me", "telegram.me", "telegram.org", "wa.me", "whatsapp.com", "linktr.ee", "facebook.com", "fb.com",
    "twitter.com", "x.com", "youtube.com", "aparat.com", "digikala.com", "divar.ir", "sheypoor.com", "google.com",
    "sites.google.com", "eitaa.com", "rubika.ir", "bale.ai", "virasty.com", "basalam.com", "torob.com", "emalls.ir",
}
_HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9؀-ۿ](?:[a-z0-9؀-ۿ-]{0,61}[a-z0-9؀-ۿ])?\.)+[a-z؀-ۿ]{2,63}$")


def site_key(value):
    """The normalised host when it can identify ONE website, else None."""
    s = normalize_site(value)
    if not s or not _HOST.match(s):
        return None
    if s in SHARED_HOSTS or any(s.endswith("." + h) for h in SHARED_HOSTS) or s.endswith(".blogspot.com"):
        return None
    return s


def is_shared_platform(value) -> bool:
    s = normalize_site(value)
    return bool(s) and (s in SHARED_HOSTS or any(s.endswith("." + h) for h in SHARED_HOSTS) or s.endswith(".blogspot.com"))


def index_by_site(rows, site_field="site", id_field="id"):
    idx = {}
    for r in rows:
        key = site_key(r.get(site_field))
        if key:
            idx.setdefault(key, []).append(str(r.get(id_field) or "").strip())
    return idx


def find_duplicates(pending, nbo_approved, crm_approved):
    """-> list of {id, site, in_nbo, in_crm, related, is_duplicate}. related never contains the request itself."""
    nbo, crm = index_by_site(nbo_approved), index_by_site(crm_approved)
    out = []
    for r in pending:
        rid, key = str(r.get("id") or "").strip(), site_key(r.get("site"))
        n = [x for x in nbo.get(key, []) if x and x != rid] if key else []
        c = [x for x in crm.get(key, []) if x and x != rid] if key else []
        related = n + c
        out.append({"id": rid, "site": key, "in_nbo": bool(n), "in_crm": bool(c), "related": related, "is_duplicate": bool(related)})
    return out


def pending_siblings(pending):
    """{request id: [other PENDING request ids with the same website]} - two open requests for one site need a human."""
    idx = index_by_site(pending)
    out = {}
    for ids in idx.values():
        if len(ids) > 1:
            for rid in ids:
                out[rid] = [x for x in ids if x != rid]
    return out
