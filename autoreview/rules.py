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
    is_online: Optional[bool] = None
    website_reachable: Optional[bool] = None       # None = check failed (timeout, error)
    site_active: Optional[bool] = None
    has_enamad: Optional[bool] = None              # None = enamad unavailable
    enamad_expired: Optional[bool] = None
    owner_matches_account_holder: Optional[bool] = None
    registrant_matches_account_holder: Optional[bool] = None
    category_relation: str = "unknown"             # match | mismatch | unknown
    enamad_shown_on_site: Optional[bool] = None
    agreement_ok: Optional[bool] = None
    category_group: str = "normal"                 # normal | services | education | gold | special
    category_name: Optional[str] = None            # NBO "Category (fa)"; a per-category minimum overrides the group default
    product_count: Optional[int] = None
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

    # 0. scope guard: only online requests are ever acted on by this engine
    if facts.is_online is not True:
        return unknown("is_online")
    d.trace.append("PASS is_online")

    # 1. reachability
    if facts.website_reachable is None:
        return unknown("website_reachable")
    if facts.website_reachable is False:
        act = rules.get("unreachable_site_action", {}).get("value", "MANUAL")
        return fail(EDIT, "BAD_OR_DEAD_URL") if act == "EDIT" else unknown("website unreachable")
    d.trace.append("PASS website_reachable")
    if facts.site_active is False:
        return fail(CANCEL, "SITE_INACTIVE")

    # 2. enamad
    if facts.has_enamad is None:
        return unknown("enamad availability")
    if facts.has_enamad is False:
        return fail(EDIT, "MISSING_ENAMAD")
    if facts.enamad_expired is None:
        return unknown("enamad expiry")
    if facts.enamad_expired:
        return fail(EDIT, "ENAMAD_EXPIRED")
    d.trace.append("PASS enamad exists and is valid")

    # 3. names
    if facts.owner_matches_account_holder is None:
        return unknown("enamad owner vs account holder")
    if facts.owner_matches_account_holder is False:
        # NBO has two reasons for this (Edit: OWNER_MISMATCH, Cancel: OWNER_MISMATCH_CANCEL); the owner chooses in config.
        mode = rules.get("owner_mismatch_action", {}).get("value", "MANUAL")
        if mode == "EDIT":
            return fail(EDIT, "OWNER_MISMATCH")
        if mode == "CANCEL":
            return fail(CANCEL, "OWNER_MISMATCH_CANCEL")
        return _manual(d, "enamad owner differs from the account holder - owner_mismatch_action is MANUAL")
    if facts.registrant_matches_account_holder is None:
        return unknown("registrant vs account holder")
    if facts.registrant_matches_account_holder is False:
        return fail(EDIT, "NAME_MISMATCH_BANK")
    d.trace.append("PASS names")

    # 4. category
    if facts.category_group in rules.get("manual_category_groups", []):
        return _manual(d, f"category group '{facts.category_group}' needs documents - human review")
    if facts.category_group == "services" and rules.get("services_go_manual", {}).get("value"):
        return _manual(d, "services go to manual review by rule")
    if facts.category_relation == "unknown":
        return unknown("enamad category mapping")
    if facts.category_relation == "mismatch":
        return fail(EDIT, "CATEGORY_MISMATCH")
    d.trace.append("PASS category")

    # 5. website content
    if facts.enamad_shown_on_site is None:
        return unknown("enamad displayed on site")
    if facts.enamad_shown_on_site is False:
        return fail(EDIT, "ENAMAD_NOT_ON_SITE")
    if facts.agreement_ok is None:
        return unknown("agreement")
    if facts.agreement_ok is False:
        return _manual(d, "agreement check failed - rule and reason not defined yet")
    if facts.can_add_to_cart is None:
        return unknown("add to cart")
    if facts.can_add_to_cart is False:
        return fail(EDIT, "CANNOT_ADD_TO_CART")

    # 6. products (threshold depends on the category group)
    limits = rules["min_products"]
    per_cat = limits.get("by_category_fa", {}).get("values", {})
    limit = per_cat.get(facts.category_name) if facts.category_name in per_cat else limits.get(facts.category_group, limits["default"])["value"]
    if facts.product_count is None:
        return unknown("product count")
    if facts.product_count < limit:
        return fail(EDIT, "SITEMAP_MISSING" if facts.has_sitemap is False else "TOO_FEW_PRODUCTS")
    d.trace.append(f"PASS products {facts.product_count} >= {limit}")

    # 7. contact
    if facts.has_contact is None:
        return unknown("contact info")
    if facts.has_contact is False:
        return fail(EDIT, "NO_CONTACT")
    d.trace.append("PASS contact")

    d.notes.append("all gates passed")
    return d
