"""Build the rule engine's Facts for ONE request from the export row + live checks (site, sitemap, enamad).

Everything here is read-only HTTP. Whatever cannot be proven stays None, and the engine turns None into MANUAL.
A 'blocker' stops the checks early when the site cannot be judged at all (a social-media page, a bot wall, a parked
domain, a redirect to another domain) - the person reviewing gets that exact reason instead of a vague 'unknown'."""
import asyncio
import json
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


DEAD = {"dns", "dns_temp", "timeout", "connect", "ssl", "invalid_url", "redirects"}
INACTIVE_HTTP = {"http_404", "http_410", "http_500", "http_502", "http_503", "http_504"}


def _apply_enamad(facts, ev, info, row, website, category_map):
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
    if facts.category_relation == "unknown" and category_map.get("match_mode", "action_test_4") == "action_test_4":
        hit = old_category_match(info.activities)
        if hit:
            facts.category_relation = "match"
            ev["category_rule"] = f"Action Test 4: '{hit[0]}' -> {hit[1]}"


_KEYWORDS = None


def old_category_match(activities):
    """Action Test 4's category rule (owner 2026-10-02: the old rules): an enamad activity counts when it IS an NBO category
    or a keyword of any category is inside it (or it inside a keyword). -> (activity, category) or None."""
    global _KEYWORDS
    if _KEYWORDS is None:
        from .paths import config_dir
        _KEYWORDS = json.loads((config_dir() / "category_keywords.json").read_text(encoding="utf-8"))["keywords"]
    for act in activities or []:
        act = str(act or "").strip()
        if not act:
            continue
        if act in _KEYWORDS:
            return act, act
        for cat, words in _KEYWORDS.items():
            if any(w in act or act in w for w in words):
                return act, cat
    return None


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

    async def enamad_part():
        async with enamad_gate:
            info = await enamad.lookup(http, website)
            if pause:
                await asyncio.sleep(pause)
        return info

    # Action Test 4 order: the enamad verdict stands even when the site itself does not open.
    enamad_task = asyncio.ensure_future(enamad_part())
    home, tried = await sitec.fetch_home(website, fetch)
    ev["home"] = {"ok": home.ok, "status": home.status, "error": home.error, "url": home.url, "tried": tried}
    if home.ok:
        facts.website_reachable = True
    elif home.error in DEAD:                            # never opened (old engine: 'URL wrong or inactive' -> EDIT)
        facts.website_reachable = False
    elif home.error in INACTIVE_HTTP:                   # opened with an error page (old engine: 'site inactive' -> CANCEL)
        facts.website_reachable, facts.site_active = True, False
    if facts.website_reachable:
        facts.ssl_ok = str(home.url).lower().startswith("https://")

    stop = facts.website_reachable is not True or facts.site_active is False
    if not stop:
        final_host = normalize_site(home.url)
        problem = detectors.page_problem(home.text)
        if final_host != declared:
            facts.blocker = f"the website redirects to another domain ({declared} -> {final_host})"
        elif problem == "challenge":
            facts.blocker = "the website answers with a bot-protection page; its content could not be read"
        elif problem == "placeholder":
            facts.site_active = False                   # parked / suspended / under construction = inactive site
        else:
            facts.site_active = True
        stop = facts.blocker is not None or facts.site_active is False
    if stop:
        info = await enamad_task
        _apply_enamad(facts, ev, info, row, website, category_map)
        return facts, ev

    origin = sitec.origin_of(home.url)
    info, prod, api, contact = await asyncio.gather(enamad_task, sitec.count_products(origin, fetch),
                                                    sitec.count_products_api(origin, fetch), sitec.has_contact(home, fetch))
    _apply_enamad(facts, ev, info, row, website, category_map)

    # products: the shop's own catalogue total (exact) or the sitemap count, whichever proves more
    facts.has_sitemap = prod["has_sitemap"]
    counts = [(prod["product_count"], prod["complete"], prod["basis"])]
    if api["product_count"] is not None:
        counts.append((api["product_count"], True, api["basis"]))
    best = max((c for c in counts if c[0] is not None), key=lambda c: c[0], default=(None, True, ""))
    facts.product_count, facts.product_count_complete = best[0], best[1]
    prod = dict(prod, samples=list(dict.fromkeys(list(prod.get("samples", [])) + api["samples"])))
    ev["products"] = {"has_sitemap": prod["has_sitemap"], "product_count": best[0], "complete": best[1], "basis": best[2],
                      "sitemap_count": counts[0][0], "api_count": api["product_count"]}
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
    ev["home_url"] = home.url
    ev["samples"] = prod.get("samples", [])[:3]
    ev["facts"] = asdict(facts)
    return facts, ev


# ---- second look with a real (hidden) browser ------------------------------------------------------------------------------
# Some shops draw the enamad seal, the cart button or the footer with JavaScript, which plain HTTP cannot see. When - and only
# when - the engine stops on one of these unknowns, the page is rendered once in a hidden browser and checked again.
RENDERABLE = {"enamad displayed on site", "add to cart", "contact info"}


async def render_fill(what: str, facts: Facts, ev: dict, render) -> bool:
    """Fill ONE unknown fact from a rendered page. Returns True when the fact changed. Rendering can only ever prove that
    something IS there; it never turns an unknown into a failure."""
    rendered = ev.setdefault("rendered", [])
    if what in ("enamad displayed on site", "contact info"):
        url = ev.get("home_url")
        if not url:
            return False
        html = await render(url)
        rendered.append({"url": url, "ok": bool(html), "for": what})
        if not html:
            return False
        changed = False
        if facts.enamad_shown_on_site is None and detectors.enamad_shown_on_site(html):
            facts.enamad_shown_on_site, changed = True, True
        if facts.has_contact is None and sitec.contact_on_page(html):
            facts.has_contact, changed = True, True
        return changed
    if what == "add to cart":
        for url in ev.get("samples", [])[:2]:
            html = await render(url)
            rendered.append({"url": url, "ok": bool(html), "for": what})
            if html and detectors.can_add_to_cart(html):
                facts.can_add_to_cart = True
                return True
    return False
