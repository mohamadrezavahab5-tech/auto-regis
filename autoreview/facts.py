"""Build the rule engine's Facts for ONE request from the export row + live checks (site, sitemap, enamad).

Everything here is read-only HTTP. Whatever cannot be proven stays None, and the engine turns None into MANUAL."""
import asyncio
from dataclasses import asdict

from .collectors import detectors, enamad, site as sitec
from .normalize import names_equal, normalize_site
from .rules import Facts


def category_group(rules: dict, category_name) -> str:
    groups = rules.get("category_groups", {})
    for g in ("gold", "special", "services", "education"):
        if category_name in groups.get(g, []):
            return g
    return "normal"


def registrant_name(row: dict) -> str:
    return " ".join(x for x in (row.get("owner_name", ""), row.get("owner_family", "")) if x).strip()


async def collect(row: dict, rules: dict, fetch, http, enamad_gate: asyncio.Semaphore, category_map: dict, pause: float = 0.0):
    """-> (Facts, evidence dict). `fetch` = site.make_fetch(client); `http` = client used for enamad."""
    checks = rules.get("checks", {})
    cat = row.get("category") or None
    facts = Facts(
        is_online=True if str(row.get("has_online", "")).lower() == "true" else None,
        category_group=category_group(rules, cat), category_name=cat,
        agreement_ok=True if not checks.get("agreement", {}).get("enabled", False) else None,
    )
    ev = {"site": row.get("site"), "category": cat}
    website = row.get("site", "")
    if not normalize_site(website):
        ev["problem"] = "no usable website in the export"
        return facts, ev

    home = await fetch(sitec.base_url(website))
    facts.website_reachable = sitec.reachable(home)
    ev["home"] = {"ok": home.ok, "status": home.status, "error": home.error}
    if facts.website_reachable is not True:
        return facts, ev

    async def enamad_part():
        async with enamad_gate:
            info = await enamad.lookup(http, website)
            if pause:
                await asyncio.sleep(pause)
        return info

    info, prod = await asyncio.gather(enamad_part(), sitec.count_products(website, fetch))

    ev["enamad"] = {"found": info.found, "status": info.status, "owner": info.owner, "valid_until": info.valid_until,
                    "activities": info.activities, "error": info.error}
    facts_e = enamad.to_facts(info, website, row.get("account_holder"), cat, category_map["titles_by_nbo_category"],
                              bool(category_map.get("mismatch_allowed")))
    for k, v in facts_e.items():
        setattr(facts, k, v)

    reg = registrant_name(row)
    holder = row.get("account_holder", "")
    facts.registrant_matches_account_holder = names_equal(reg, holder) if reg and holder else None

    facts.has_sitemap, facts.product_count = prod["has_sitemap"], prod["product_count"]
    ev["products"] = {k: prod[k] for k in ("has_sitemap", "product_count", "basis")}
    facts.has_contact = sitec.has_contact(home.text)
    facts.enamad_shown_on_site = detectors.enamad_shown_on_site(home.text) if checks.get("enamad_on_site", {}).get("enabled", True) else True

    if checks.get("add_to_cart", {}).get("enabled", True):
        if prod.get("sample_url"):
            page = await fetch(prod["sample_url"])
            facts.can_add_to_cart = detectors.can_add_to_cart(page.text) if page.ok else None
            ev["cart_page"] = prod["sample_url"]
    else:
        facts.can_add_to_cart = True
    ev["facts"] = asdict(facts)
    return facts, ev
