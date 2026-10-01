"""Build the rule engine's Facts for ONE request from the export row + live checks (site, sitemap, enamad).

Everything here is read-only HTTP. Whatever cannot be proven stays None, and the engine turns None into MANUAL.
A 'blocker' stops the checks early when the site cannot be judged at all (a social-media page, a bot wall, a parked
domain, a redirect to another domain) - the person reviewing gets that exact reason instead of a vague 'unknown'."""
import asyncio
from dataclasses import asdict

from .collectors import detectors, enamad, site as sitec
from .duplicates import is_shared_platform
from .normalize import names_equal, normalize_site
from .rules import Facts


def category_group(rules: dict, category_name) -> str:
    groups = rules.get("category_groups", {})
    for g in ("gold", "special", "services", "education"):
        if category_name and category_name in groups.get(g, []):
            return g
    return "normal"


def registrant_name(row: dict) -> str:
    return " ".join(x for x in (row.get("owner_name", ""), row.get("owner_family", "")) if x).strip()


def _base_facts(row: dict, rules: dict) -> Facts:
    checks = rules.get("checks", {})
    cat = row.get("category") or None
    return Facts(
        is_online=True if str(row.get("has_online", "")).strip().lower() == "true" else None,
        category_group=category_group(rules, cat), category_name=cat,
        agreement_ok=True if not checks.get("agreement", {}).get("enabled", False) else None,
    )


async def collect(row: dict, rules: dict, fetch, http, enamad_gate: asyncio.Semaphore, category_map: dict, pause: float = 0.0):
    """-> (Facts, evidence dict). `fetch` = site.make_fetch(client); `http` = client used for enamad."""
    checks = rules.get("checks", {})
    facts = _base_facts(row, rules)
    website = str(row.get("site") or "").strip()
    ev = {"site": website, "category": facts.category_name}

    declared = normalize_site(website)
    if not declared or "." not in declared:
        facts.blocker = "no usable website address in the request"
        return facts, ev
    if is_shared_platform(website):
        facts.blocker = f"the website is a page on a shared platform ({declared}), not a shop website"
        return facts, ev

    home, tried = await sitec.fetch_home(website, fetch)
    facts.website_reachable = sitec.reachable(home)
    ev["home"] = {"ok": home.ok, "status": home.status, "error": home.error, "url": home.url, "tried": tried}
    if facts.website_reachable is not True:
        return facts, ev

    final_host = normalize_site(home.url)
    if final_host != declared:
        facts.blocker = f"the website redirects to another domain ({declared} -> {final_host})"
        return facts, ev
    problem = detectors.page_problem(home.text)
    if problem == "challenge":
        facts.blocker = "the website answers with a bot-protection page; its content could not be read"
        return facts, ev
    if problem == "placeholder":
        facts.blocker = "the website shows a placeholder / suspended / under-construction page"
        return facts, ev

    async def enamad_part():
        async with enamad_gate:
            info = await enamad.lookup(http, website)
            if pause:
                await asyncio.sleep(pause)
        return info

    origin = sitec.origin_of(home.url)
    info, prod, contact = await asyncio.gather(enamad_part(), sitec.count_products(origin, fetch), sitec.has_contact(home, fetch))

    ev["enamad"] = {"found": info.found, "status": info.status, "owner": info.owner, "valid_until": info.valid_until,
                    "approve_date": info.approve_date, "domain_shown": info.domain_shown, "business_name": info.business_name,
                    "activities": info.activities, "error": info.error}
    facts_e = enamad.to_facts(info, website, row.get("account_holder"), facts.category_name, category_map["titles_by_nbo_category"],
                              bool(category_map.get("mismatch_allowed")))
    for k, v in facts_e.items():
        setattr(facts, k, v)

    reg, holder = registrant_name(row), str(row.get("account_holder") or "").strip()
    facts.registrant_matches_account_holder = names_equal(reg, holder) if reg and holder else None
    ev["names"] = {"account_holder": holder, "registrant": reg, "enamad_owner": info.owner}

    facts.has_sitemap, facts.product_count, facts.product_count_complete = prod["has_sitemap"], prod["product_count"], prod["complete"]
    ev["products"] = {k: prod[k] for k in ("has_sitemap", "product_count", "complete", "basis")}
    facts.has_contact = contact
    facts.enamad_shown_on_site = detectors.enamad_shown_on_site(home.text) if checks.get("enamad_on_site", {}).get("enabled", True) else True

    if checks.get("add_to_cart", {}).get("enabled", True):
        ev["cart_pages"] = []
        for url in prod.get("samples", [])[:3]:                       # the first product may simply be out of stock
            page = await fetch(url)
            ev["cart_pages"].append({"url": url, "ok": page.ok, "error": page.error})
            if page.ok and detectors.can_add_to_cart(page.text):
                facts.can_add_to_cart = True
                break
    else:
        facts.can_add_to_cart = True
    ev["facts"] = asdict(facts)
    return facts, ev
