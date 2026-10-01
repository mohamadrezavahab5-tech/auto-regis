"""Enamad (e-Namad) facts over plain HTTP - no browser.

Public flow used by enamad.ir itself (read from its own Search.js, 2026-10-01):
  1. POST https://enamad.ir/Home/GetData  domain=<host>   -> JSON object {id, code, enamad_status, expdate, approvedate, ...}
       or JSON null when the domain has no enamad (observed live 2026-10-01: '{}' never comes back, an object always has 'id').
       enamad_status: 1 valid | 3 valid, business licence pending | 5 expired | 6 suspended
  2. GET  https://trustseal.enamad.ir/?id=<id>&code=<code> -> HTML business profile (owner "صاحب امتیاز", validity, activities)
Everything is tri-state: whatever cannot be established (site down, an unexpected answer, a profile for another domain, an
unknown status) becomes None => the engine says MANUAL. "No enamad" is only concluded from enamad's own explicit null."""
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
VALID, VALID_LICENCE_PENDING, EXPIRED, SUSPENDED = 1, 3, 5, 6


@dataclass
class EnamadInfo:
    found: Optional[bool]                    # True / False (enamad answered "none") / None (could not ask, or odd answer)
    status: Optional[int] = None
    owner: Optional[str] = None
    domain_shown: Optional[str] = None
    approve_date: Optional[str] = None
    valid_until: Optional[str] = None
    activities: list = field(default_factory=list)   # titles of APPROVED activities
    business_name: Optional[str] = None
    error: Optional[str] = None


# ---- profile page parsing (pure) -----------------------------------------------------------------------------------------
_LABELS = ("صاحب امتیاز", "تاریخ اعطا", "تاریخ اعتبار", "شناسنامه", "نام کسب", "آدرس", "تلفن", "ایمیل", "ساعت")


def _text_lines(html: str):
    html = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    out = []
    for line in re.sub(r"<[^>]+>", "\n", html).splitlines():
        line = line.replace("&nbsp;", " ").replace("\xa0", " ").strip()
        if line:
            out.append(line)
    return out


def _value_after(lines, i):
    """The value of a 'label :' line: on the same line after the colon, or on the next non-colon line."""
    same = lines[i].split(":", 1)[1].strip() if ":" in lines[i] else ""
    if same:
        return same
    j = i + 1
    while j < len(lines) and lines[j] in (":", "："):
        j += 1
    return lines[j] if j < len(lines) else None


def _is_label(value: str) -> bool:
    v = (value or "").strip()
    return not v or v.endswith(":") or any(v.startswith(l) for l in _LABELS)


def parse_profile(html: str) -> dict:
    lines = _text_lines(html)
    out = {"domain": None, "owner": None, "approve_date": None, "valid_until": None, "activities": []}
    for i, l in enumerate(lines):
        if l.startswith("صاحب امتیاز"):
            v = _value_after(lines, i)
            out["owner"] = None if _is_label(v) else v
            for k in range(i - 1, max(-1, i - 5), -1):                      # the domain is printed just above the owner
                cand = lines[k].strip().lower()
                if re.fullmatch(r"(?:https?://)?[a-z0-9.-]+\.[a-z]{2,}/?", cand):
                    out["domain"] = cand
                    break
        elif l.startswith("تاریخ اعطا"):
            v = _value_after(lines, i)
            out["approve_date"] = None if _is_label(v) else v
        elif l.startswith("تاریخ اعتبار"):
            v = _value_after(lines, i)
            if v and not _is_label(v):
                m = re.search(r"\d{4}/\d{2}/\d{2}", v)
                out["valid_until"] = m.group(0) if m else v
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S | re.I)]
        if len(cells) >= 7 and cells[-1].startswith("تایید") and cells[1]:
            out["activities"].append(cells[1])
    return out


# ---- lookup ----------------------------------------------------------------------------------------------------------------
async def lookup(client: httpx.AsyncClient, site: str, retries: int = 1) -> EnamadInfo:
    domain = normalize_site(site)
    if not domain:
        return EnamadInfo(None, error="no domain")
    err = None
    for attempt in range(retries + 1):
        try:
            if not client.cookies:
                await client.get("https://enamad.ir/", headers=HEADERS)
            r = await client.post("https://enamad.ir/Home/GetData", data={"domain": domain},
                                  headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"})
            if r.status_code != 200 or "json" not in r.headers.get("content-type", "").lower():
                err = f"http_{r.status_code}"
            else:
                try:
                    data = r.json()
                except ValueError:
                    data, err = Ellipsis, "bad_json"
                if data is None:                                             # enamad's explicit "no record for this domain"
                    return EnamadInfo(False)
                if isinstance(data, dict) and data.get("id") and data.get("code") and data.get("enamad_status") not in (None, ""):
                    try:
                        status = int(data["enamad_status"])
                    except (TypeError, ValueError):
                        return EnamadInfo(None, error="odd_status")
                    info = EnamadInfo(True, status, approve_date=data.get("approvedate"), valid_until=data.get("expdate"),
                                      business_name=data.get("persian_name"))
                    p = await client.get("https://trustseal.enamad.ir/", params={"id": data["id"], "code": data["code"]}, headers=HEADERS)
                    if p.status_code != 200:
                        info.error = f"profile_http_{p.status_code}"
                        return info
                    prof = parse_profile(p.text)
                    info.owner, info.domain_shown, info.activities = prof["owner"], prof["domain"], prof["activities"]
                    info.valid_until = prof["valid_until"] or info.valid_until
                    return info
                if err is None:
                    return EnamadInfo(None, error="unexpected_answer")              # never read an odd answer as "no enamad"
        except httpx.HTTPError as e:
            err = type(e).__name__
        if attempt < retries:
            await asyncio.sleep(0.8)
    return EnamadInfo(None, error=err)


# ---- facts for the rule engine ---------------------------------------------------------------------------------------------
def category_relation(nbo_category, activities, titles_by_category: dict, mismatch_allowed: bool = False) -> str:
    """match: the declared NBO category is supported by one of the approved enamad activities.
    mismatch: every activity title is KNOWN (mapped) and none supports the category.
    unknown: at least one title is not in the mapping and nothing supports the category => never guessed."""
    title_to_cats = {}                                   # one enamad title can serve several NBO categories
    for cat, ts in titles_by_category.items():
        for t in ts:
            title_to_cats.setdefault(normalize_text(t), set()).add(normalize_text(cat))
    if not activities or not nbo_category:
        return "unknown"
    want = normalize_text(nbo_category)
    known, all_known = set(), True
    for a in activities:
        cats = title_to_cats.get(normalize_text(a))
        if not cats:
            all_known = False
        else:
            known |= cats
    if want in known:
        return "match"
    # A 'mismatch' (=> EDIT) is only issued when the owner has reviewed the mapping and switched it on: the mapping was derived from the old
    # code and a live check showed it misses valid pairs (e.g. the fashion/bags/shoes title for the NBO category 'کیف و کفش').
    return "mismatch" if (all_known and mismatch_allowed) else "unknown"


_UNKNOWN = dict(has_enamad=None, enamad_expired=None, owner_matches_account_holder=None, category_relation="unknown")


def to_facts(info: EnamadInfo, queried_site: str, account_holder, nbo_category, titles_by_category: dict, mismatch_allowed: bool = False) -> dict:
    """-> keyword arguments for rules.Facts (enamad part only)."""
    if info.found is None:
        return dict(_UNKNOWN)
    if info.found is False:
        return dict(_UNKNOWN, has_enamad=False)
    if info.status not in (VALID, VALID_LICENCE_PENDING, EXPIRED):     # suspended or a status no rule exists for: never decided
        return dict(_UNKNOWN)
    if info.domain_shown and normalize_site(info.domain_shown) != normalize_site(queried_site):   # profile of another domain
        return dict(_UNKNOWN)
    return dict(
        has_enamad=True,
        enamad_expired=info.status == EXPIRED,
        owner_matches_account_holder=names_equal(info.owner, account_holder) if info.owner else None,
        category_relation=category_relation(nbo_category, info.activities, titles_by_category, mismatch_allowed),
    )
