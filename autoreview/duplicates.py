"""Duplicate detection: a pending request is a duplicate when its website already exists in an APPROVED set."""
from .normalize import normalize_site


def index_by_site(rows, site_key="site", id_key="id"):
    idx = {}
    for r in rows:
        site = normalize_site(r.get(site_key))
        if site:
            idx.setdefault(site, []).append(str(r.get(id_key) or "").strip())
    return idx


def find_duplicates(pending, nbo_approved, crm_approved):
    """-> list of dicts {id, site, in_nbo, in_crm, related, is_duplicate}; related excludes the request itself, is_duplicate = another approved request has the same site."""
    nbo, crm = index_by_site(nbo_approved), index_by_site(crm_approved)
    out = []
    for r in pending:
        rid, site = str(r.get("id") or "").strip(), normalize_site(r.get("site"))
        n, c = (nbo.get(site, []) if site else []), (crm.get(site, []) if site else [])
        related = [x for x in n + c if x and x != rid]
        out.append({"id": rid, "site": site, "in_nbo": bool(n), "in_crm": bool(c), "related": related, "is_duplicate": bool(related)})
    return out
