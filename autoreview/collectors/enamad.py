"""Enamad (e-Namad) facts over plain HTTP - no browser.

Public flow used by enamad.ir itself (read from its own Search.js, 2026-10-01):
  1. POST https://enamad.ir/Home/GetData  domain=<host>   -> JSON {id, code, enamad_status, expdate, approvedate, persian_name, ...}
       enamad_status: 1 valid | 3 valid, business licence pending | 5 expired | 6 suspended | null = not found
  2. GET  https://trustseal.enamad.ir/?id=<id>&code=<code> -> HTML business profile (owner "صاحب امتیاز", validity, activity table)
Everything is tri-state: whatever cannot be established (site down, odd answer, domain shown differs) becomes None => the engine says MANUAL."""
import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from ..normalize import names_equal, normalize_site, normalize_text

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fa,en;q=0.8",
    "Referer": "https://enamad.ir/",
}


@dataclass
class EnamadInfo:
    found: Optional[bool]                    # True / False (definitely not registered) / None (could not ask)
    status: Optional[int] = None
    owner: Optional[str] = None
    domain_shown: Optional[str] = None
    approve_date: Optional[str] = None
    valid_until: Optional[str] = None
    activities: list = field(default_factory=list)   # titles of APPROVED activities
    error: Optional[str] = None


# ---- profile page parsing (pure) -------------------------------------------------------------------------------------
def _text_lines(html: str):
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return [l.replace("&nbsp;", " ").strip() for l in re.sub(r"<[^>]+>", "\n", html).splitlines() if l.replace("&nbsp;", " ").strip()]


def parse_profile(html: str) -> dict:
    lines = _text_lines(html)
    out = {"domain": None, "owner": None, "approve_date": None, "valid_until": None, "activities": []}
    for i, l in enumerate(lines):
        if l.startswith("صاحب امتیاز"):
            j = i + 1
            while j < len(lines) and lines[j] in (":", "&nbsp;"):
                j += 1
            out["owner"] = lines[j] if j < len(lines) else None
            for k in range(i - 1, max(-1, i - 4), -1):                      # the domain is printed just above the owner
                if re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", lines[k]):
                    out["domain"] = lines[k].lower()
                    break
        elif l.startswith("تاریخ اعطا") and i + 1 < len(lines):
            out["approve_date"] = lines[i + 1]
        elif l.startswith("تاریخ اعتبار") and i + 1 < len(lines):
            m = re.search(r"\d{4}/\d{2}/\d{2}", lines[i + 1])
            out["valid_until"] = m.group(0) if m else lines[i + 1]
    for row in re.findall(r"<tr>(.*?)</tr>", html, flags=re.S):
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        if len(cells) >= 7 and cells[-1].startswith("تایید"):
            out["activities"].append(cells[1])
    return out


# ---- lookup ----------------------------------------------------------------------------------------------------------
async def lookup(client: httpx.AsyncClient, site: str, retries: int = 1) -> EnamadInfo:
    domain = normalize_site(site)
    if not domain:
        return EnamadInfo(None, error="no domain")
    err = None
    for _ in range(retries + 1):
        try:
            if not client.cookies:
                await client.get("https://enamad.ir/", headers=HEADERS)
            r = await client.post("https://enamad.ir/Home/GetData", data={"domain": domain}, headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"})
            if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
                err = f"http_{r.status_code}"
                await asyncio.sleep(0.7)
                continue
            data = r.json()
            if not data or data.get("enamad_status") in (None, "") or not data.get("id"):
                return EnamadInfo(False)
            status = int(data["enamad_status"])
            info = EnamadInfo(True, status, approve_date=data.get("approvedate"), valid_until=data.get("expdate"))
            p = await client.get("https://trustseal.enamad.ir/", params={"id": data["id"], "code": data["code"]}, headers=HEADERS)
            if p.status_code != 200:
                info.error = f"profile_http_{p.status_code}"
                return info
            prof = parse_profile(p.text)
            info.owner, info.domain_shown, info.activities = prof["owner"], prof["domain"], prof["activities"]
            info.valid_until = prof["valid_until"] or info.valid_until
            return info
        except httpx.HTTPError as e:
            err = type(e).__name__
            await asyncio.sleep(0.7)
    return EnamadInfo(None, error=err)


# ---- facts for the rule engine ---------------------------------------------------------------------------------------
def category_relation(nbo_category, activities, titles_by_category: dict, mismatch_allowed: bool = False) -> str:
    """match: the declared NBO category is supported by one of the approved enamad activities.
    mismatch: every activity title is KNOWN (mapped) and none supports the category.
    unknown: at least one title is not in the mapping and nothing supports the category => never guessed."""
    title_to_cats = {}                                   # one enamad title can serve several NBO categories
    for cat, ts in titles_by_category.items():
        for t in ts:
            title_to_cats.setdefault(normalize_text(t), set()).add(cat)
    if not activities:
        return "unknown"
    known, all_known = set(), True
    for a in activities:
        cats = title_to_cats.get(normalize_text(a))
        if not cats:
            all_known = False
        else:
            known |= cats
    if nbo_category in known:
        return "match"
    # A 'mismatch' (=> EDIT) is only issued when the owner has reviewed the mapping and switched it on: the mapping was derived from the old
    # code and a live check showed it misses valid pairs (e.g. the fashion/bags/shoes title for the NBO category 'کیف و کفش').
    return "mismatch" if (all_known and mismatch_allowed) else "unknown"


def to_facts(info: EnamadInfo, queried_site: str, account_holder, nbo_category, titles_by_category: dict, mismatch_allowed: bool = False) -> dict:
    """-> keyword arguments for rules.Facts (enamad part only)."""
    if info.found is None:
        return dict(has_enamad=None, enamad_expired=None, owner_matches_account_holder=None, category_relation="unknown")
    if info.found is False:
        return dict(has_enamad=False, enamad_expired=None, owner_matches_account_holder=None, category_relation="unknown")
    if info.status == 6:                                 # suspended: no rule exists for it => the engine must not decide
        return dict(has_enamad=None, enamad_expired=None, owner_matches_account_holder=None, category_relation="unknown")
    if info.domain_shown and info.domain_shown != normalize_site(queried_site):   # profile belongs to another domain
        return dict(has_enamad=None, enamad_expired=None, owner_matches_account_holder=None, category_relation="unknown")
    return dict(
        has_enamad=True,
        enamad_expired=info.status == 5,
        owner_matches_account_holder=names_equal(info.owner, account_holder) if info.owner else None,
        category_relation=category_relation(nbo_category, info.activities, titles_by_category, mismatch_allowed),
    )
