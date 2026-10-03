"""Main decision engine. Pure: facts in, Decision out.

Principle (from the owner): if the system cannot PROVE a verdict it must not act - it says MANUAL.
Every fact is tri-state: True / False / None (= could not be determined). Any needed None => MANUAL.
An EDIT/CANCEL is only issued when its reason maps to ONE unambiguous NBO reason code; otherwise the request
goes to MANUAL with a note, so nothing wrong is ever typed into NBO.
"""
from dataclasses import dataclass, field
from typing import Optional

from .reasons import reason_code

APPROVE, EDIT, CANCEL, MANUAL = "APPROVE", "EDIT", "CANCEL", "MANUAL"


@dataclass
class Facts:
    blocker: Optional[str] = None                  # a reason the site cannot be judged at all (social page, bot wall, ...) => MANUAL
    is_online: Optional[bool] = None
    website_reachable: Optional[bool] = None       # None = check failed (timeout, error)
    site_active: Optional[bool] = None
    ssl_ok: Optional[bool] = None                  # the site is served over https (Action Test 4: http only => EDIT 'URL')
    has_enamad: Optional[bool] = None              # None = enamad unavailable
    enamad_expired: Optional[bool] = None
    owner_matches_account_holder: Optional[bool] = None
    registrant_matches_account_holder: Optional[bool] = None
    category_relation: str = "unknown"             # match | mismatch | unknown
    category_why: str = ""                         # why the relation stayed unknown, the real cause (shown to people)
    enamad_shown_on_site: Optional[bool] = None
    agreement_ok: Optional[bool] = None
    category_group: str = "normal"                 # normal | services | education | gold | special
    category_name: Optional[str] = None            # NBO "Category (fa)"; a per-category minimum overrides the group default
    product_count: Optional[int] = None
    product_count_complete: bool = True            # False: some product sitemap was unreadable - the count is only a lower bound
    has_sitemap: Optional[bool] = None
    has_contact: Optional[bool] = None
    can_add_to_cart: Optional[bool] = None


@dataclass
class Decision:
    action: str
    reason_keys: list = field(default_factory=list)
    reason_codes: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    trace: list = field(default_factory=list)      # every gate that was evaluated, in order


def _manual(d: Decision, note: str) -> Decision:
    d.action, d.notes = MANUAL, d.notes + [note]
    return d


def evaluate(facts: Facts, rules: dict, reasons: dict) -> Decision:
    d = Decision(action=APPROVE)

    def fail(action, key):
        code = reason_code(reasons, key)
        d.trace.append(f"FAIL {key} -> {action}")
        if code is None:
            return _manual(d, f"{key}: NBO reason code not unambiguous - not acting")
        d.action, d.reason_keys, d.reason_codes = action, [key], [code]
        return d

    def unknown(what):
        d.trace.append(f"UNKNOWN {what}")
        return _manual(d, f"could not determine: {what}")

    # Owner 2026-10-01: "all the old code's rules" - the gates and their order are Action Test 4's (evaluate_merchant);
    # what the old code guessed (a site that did not answer, an unreadable count) stays MANUAL here.
    checks = rules.get("checks", {})

    # 0. scope guard: only online requests are ever acted on by this engine
    if facts.is_online is not True:
        return unknown("is_online")
    d.trace.append("PASS is_online")
    if facts.blocker and facts.website_reachable is None:       # no shop address at all (empty, Instagram page ...): nothing to judge
        d.trace.append(f"BLOCKED {facts.blocker}")
        return _manual(d, f"blocked: {facts.blocker}")

    # 1. enamad exists
    if facts.has_enamad is None:
        return unknown("enamad availability" + (f" [{facts.category_why}]" if facts.category_why else ""))
    if facts.has_enamad is False:
        return fail(EDIT, "MISSING_ENAMAD")

    # 2. the address opens, over https
    if facts.website_reachable is None:
        return unknown("website_reachable")
    if facts.website_reachable is False:
        act = rules.get("unreachable_site_action", {}).get("value", "EDIT")
        return fail(EDIT, "BAD_OR_DEAD_URL") if act == "EDIT" else unknown("website unreachable")
    if checks.get("https", {}).get("enabled", True) and facts.ssl_ok is False:
        return fail(EDIT, "BAD_OR_DEAD_URL")
    d.trace.append("PASS website opens")
    if facts.blocker:
        d.trace.append(f"BLOCKED {facts.blocker}")
        return _manual(d, f"blocked: {facts.blocker}")

    # 3. the site is live (not an error / parked / under-construction page)
    if facts.site_active is False:
        return fail(CANCEL, "SITE_INACTIVE")

    # 4. enamad still valid
    if facts.enamad_expired is None:
        return unknown("enamad expiry")
    if facts.enamad_expired:
        return fail(EDIT, "ENAMAD_EXPIRED")
    d.trace.append("PASS enamad exists and is valid")

    # 5. names: registrant vs bank account, then enamad owner vs bank account
    if facts.registrant_matches_account_holder is None:
        return unknown("registrant vs account holder")
    if facts.registrant_matches_account_holder is False:
        return fail(EDIT, "NAME_MISMATCH_BANK")
    if facts.owner_matches_account_holder is None:
        return unknown("enamad owner vs account holder")
    if facts.owner_matches_account_holder is False:
        # NBO has two reasons for this (Edit: OWNER_MISMATCH, Cancel: OWNER_MISMATCH_CANCEL); the owner chooses in config.
        mode = rules.get("owner_mismatch_action", {}).get("value", "EDIT")
        if mode == "EDIT":
            return fail(EDIT, "OWNER_MISMATCH")
        if mode == "CANCEL":
            return fail(CANCEL, "OWNER_MISMATCH_CANCEL")
        return _manual(d, "enamad owner differs from the account holder - owner_mismatch_action is MANUAL")
    d.trace.append("PASS names")

    # 6. contact and cart
    if facts.has_contact is None:
        return unknown("contact info")
    if facts.has_contact is False:
        return fail(EDIT, "NO_CONTACT")
    d.trace.append("PASS contact")

    limits = rules["min_products"]
    per_cat = limits.get("by_category_fa", {}).get("values", {})
    if facts.category_name in per_cat:
        limit = per_cat[facts.category_name]
    elif facts.category_group in limits:
        limit = limits[facts.category_group]["value"]
    elif not facts.category_name:
        limit = limits.get("no_category", limits["default"])["value"]
    else:
        limit = limits["default"]["value"]

    def products():
        """None = enough; else the Decision for too few / no sitemap / a count that cannot prove it."""
        if facts.product_count is not None and facts.product_count >= limit:
            d.trace.append(f"PASS products {facts.product_count} >= {limit}")
            return None
        if facts.has_sitemap is False:
            if rules.get("sitemap_missing_action", {}).get("value", "EDIT") != "EDIT":
                return _manual(d, "no sitemap found - sitemap_missing_action is MANUAL")
            return fail(EDIT, "SITEMAP_MISSING")
        if facts.product_count is None:
            return unknown("product count")
        if not facts.product_count_complete:                     # a lower bound can prove "enough", never "too few"
            return unknown(f"product count (at least {facts.product_count}, sitemap only partly readable)")
        return fail(EDIT, "TOO_FEW_PRODUCTS")

    if checks.get("add_to_cart", {}).get("enabled", True):
        if facts.can_add_to_cart is False:
            return fail(EDIT, "CANNOT_ADD_TO_CART")
        if facts.can_add_to_cart is None:
            # no product page to try: when the products already fail, that is the verdict (as the old engine reached it)
            verdict = products()
            return verdict if verdict is not None else unknown("add to cart")
        d.trace.append("PASS add to cart")

    # 7. products (minimum per category; the sitemap only matters when the count is short)
    if not facts.category_name and rules.get("no_category_goes_manual", {}).get("value"):
        return _manual(d, "no category in NBO - manual review by rule")      # owner 2026-10-03: a switch, not only a minimum
    verdict = products()
    if verdict is not None:
        return verdict

    # 8. what always needs a person
    if facts.category_group in rules.get("manual_category_groups", []):
        return _manual(d, f"category group '{facts.category_group}' needs documents - human review")
    if facts.category_group == "services" and rules.get("services_go_manual", {}).get("value"):
        return _manual(d, "services go to manual review by rule")
    if facts.category_relation == "unknown":
        return unknown("enamad category mapping" + (f" [{facts.category_why}]" if facts.category_why else ""))
    if facts.category_relation == "mismatch":
        return fail(EDIT, "CATEGORY_MISMATCH")
    d.trace.append("PASS category")

    # 9. checks Action Test 4 did not have; each one has an on/off switch in Settings
    if checks.get("enamad_on_site", {}).get("enabled", False):
        if facts.enamad_shown_on_site is None:
            return unknown("enamad displayed on site")
        if facts.enamad_shown_on_site is False:
            return fail(EDIT, "ENAMAD_NOT_ON_SITE")
    if facts.agreement_ok is None:
        return unknown("agreement")
    if facts.agreement_ok is False:
        return _manual(d, "agreement check failed - rule and reason not defined yet")

    d.notes.append("all gates passed")
    return d
