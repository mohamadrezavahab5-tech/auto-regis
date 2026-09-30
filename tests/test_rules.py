import copy

from autoreview.reasons import load_reasons
from autoreview.rules import APPROVE, CANCEL, EDIT, MANUAL, Facts, evaluate
import json
from pathlib import Path

RULES = json.loads((Path(__file__).resolve().parent.parent / "config" / "rules.json").read_text(encoding="utf-8"))
REASONS = load_reasons()


def confirmed(keys):
    r = copy.deepcopy(REASONS)
    for k in keys:
        r[k]["exact_text"] = f"<{k}>"
    return r


def good(**kw):
    base = dict(is_online=True, website_reachable=True, site_active=True, has_enamad=True, enamad_expired=False,
                owner_matches_account_holder=True, registrant_matches_account_holder=True, category_relation="match",
                enamad_shown_on_site=True, agreement_ok=True, category_group="normal", product_count=100,
                has_sitemap=True, has_contact=True, can_add_to_cart=True)
    base.update(kw)
    return Facts(**base)


ALL = list(REASONS)


def test_everything_proven_is_approved():
    d = evaluate(good(), RULES, confirmed(ALL))
    assert d.action == APPROVE and d.reason_texts == []


def test_unknown_anywhere_is_manual_never_a_guess():
    for field in ("website_reachable", "has_enamad", "enamad_expired", "owner_matches_account_holder",
                  "enamad_shown_on_site", "agreement_ok", "can_add_to_cart", "product_count", "has_contact"):
        d = evaluate(good(**{field: None}), RULES, confirmed(ALL))
        assert d.action == MANUAL, field


def test_unknown_category_mapping_is_manual():
    assert evaluate(good(category_relation="unknown"), RULES, confirmed(ALL)).action == MANUAL


def test_edit_carries_the_literal_reason_text():
    d = evaluate(good(has_contact=False), RULES, confirmed(ALL))
    assert d.action == EDIT and d.reason_keys == ["NO_CONTACT"] and d.reason_texts == ["<NO_CONTACT>"]


def test_unconfirmed_reason_text_downgrades_to_manual():
    d = evaluate(good(has_contact=False), RULES, REASONS)        # shipped registry has no confirmed text yet
    assert d.action == MANUAL and "not confirmed" in d.notes[0]


def test_site_inactive_cancels_only_with_confirmed_text():
    assert evaluate(good(site_active=False), RULES, confirmed(ALL)).action == CANCEL
    assert evaluate(good(site_active=False), RULES, REASONS).action == MANUAL


def test_unreachable_site_is_manual_by_default_not_edit():
    d = evaluate(good(website_reachable=False), RULES, confirmed(ALL))
    assert d.action == MANUAL


def test_non_online_request_is_never_acted_on():
    assert evaluate(good(is_online=False), RULES, confirmed(ALL)).action == MANUAL
    assert evaluate(good(is_online=None), RULES, confirmed(ALL)).action == MANUAL


def test_gold_and_special_categories_go_to_manual_even_when_everything_else_passes():
    assert evaluate(good(category_group="gold"), RULES, confirmed(ALL)).action == MANUAL
    assert evaluate(good(category_group="special"), RULES, confirmed(ALL)).action == MANUAL


def test_product_limit_depends_on_the_category_group():
    assert evaluate(good(category_group="normal", product_count=20), RULES, confirmed(ALL)).action == EDIT
    assert evaluate(good(category_group="services", product_count=12), RULES, confirmed(ALL)).action == APPROVE
    assert evaluate(good(category_group="education", product_count=5), RULES, confirmed(ALL)).action == EDIT


def test_missing_sitemap_uses_its_own_reason():
    d = evaluate(good(product_count=3, has_sitemap=False), RULES, confirmed(ALL))
    assert d.reason_keys == ["SITEMAP_MISSING"]
