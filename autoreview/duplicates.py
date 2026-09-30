"""Duplicate detection: a pending request is a duplicate when its website already exists in an APPROVED set."""
from .normalize import normalize_site

# A page on a shared platform (instagram.com/shop-a vs instagram.com/shop-b) is not the same website: never a duplicate key.
SHARED_HOSTS = {"instagram.com", "t.me", "telegram.me", "telegram.org", "wa.me", "linktr.ee", "facebook.com", "twitter.com", "x.com",
                "youtube.com", "aparat.com", "digikala.com", "divar.ir", "sheypoor.com", "google.com", "sites.google.com", "eitaa.com", "rubika.ir"}


def _site_key(value):
    s = normalize_site(value)
    return None if (not s or s in SHARED_HOSTS or s.rsplit(".", 2)[-2:] == ["blogspot", "com"]) else s


def index_by_site(rows, site_key="site", id_key="id"):
    idx = {}
    for r in rows:
        site = _site_key(r.get(site_key))
        if site:
            idx.setdefault(site, []).append(str(r.get(id_key) or "").strip())
    return idx


def find_duplicates(pending, nbo_approved, crm_approved):
    """-> list of dicts {id, site, in_nbo, in_crm, related, is_duplicate}; related excludes the request itself, is_duplicate = another approved request has the same site."""
    nbo, crm = index_by_site(nbo_approved), index_by_site(crm_approved)
    out = []
    for r in pending:
        rid, site = str(r.get("id") or "").strip(), _site_key(r.get("site"))
        n, c = (nbo.get(site, []) if site else []), (crm.get(site, []) if site else [])
        related = [x for x in n + c if x and x != rid]
        out.append({"id": rid, "site": site, "in_nbo": bool(n), "in_crm": bool(c), "related": related, "is_duplicate": bool(related)})
    return out
