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
        r[k]["nbo_code"] = f"CODE_{k}"
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
    assert d.action == APPROVE and d.reason_codes == []


def test_unknown_anywhere_is_manual_never_a_guess():
    for field in ("website_reachable", "has_enamad", "enamad_expired", "owner_matches_account_holder",
                  "enamad_shown_on_site", "agreement_ok", "can_add_to_cart", "product_count", "has_contact"):
        d = evaluate(good(**{field: None}), RULES, confirmed(ALL))
        assert d.action == MANUAL, field


def test_unknown_category_mapping_is_manual():
    assert evaluate(good(category_relation="unknown"), RULES, confirmed(ALL)).action == MANUAL


def test_edit_carries_the_nbo_reason_code():
    d = evaluate(good(has_contact=False), RULES, confirmed(ALL))
    assert d.action == EDIT and d.reason_keys == ["NO_CONTACT"] and d.reason_codes == ["CODE_NO_CONTACT"]


def test_ambiguous_reason_code_downgrades_to_manual():
    reasons = copy.deepcopy(REASONS)
    reasons["NO_CONTACT"]["nbo_code"] = None                        # a reason whose NBO code is not (yet) unambiguous
    d = evaluate(good(has_contact=False), RULES, reasons)
    assert d.action == MANUAL and "not unambiguous" in d.notes[0]


def test_enamad_not_shown_on_site_uses_the_no_enamad_reason():
    assert evaluate(good(enamad_shown_on_site=False), RULES, REASONS).reason_codes == ["MISSING_LICENSE"]


def test_owner_mismatch_follows_the_owner_setting():
    def rules_with(mode):
        r = copy.deepcopy(RULES)
        r["owner_mismatch_action"]["value"] = mode
        return r
    f = good(owner_matches_account_holder=False)
    assert evaluate(f, RULES, REASONS).action == MANUAL                                   # shipped default: undecided => manual
    assert evaluate(f, rules_with("EDIT"), REASONS).reason_codes == ["OWNER_MISMATCH"]
    d = evaluate(f, rules_with("CANCEL"), REASONS)
    assert d.action == CANCEL and d.reason_codes == ["ENAMAD_OWNER_NAME_MISMATCH"]


def test_missing_enamad_and_category_mismatch_use_the_codes_read_from_nbo():
    assert evaluate(good(has_enamad=False), RULES, REASONS).reason_codes == ["MISSING_LICENSE"]
    assert evaluate(good(category_relation="mismatch"), RULES, REASONS).reason_codes == ["ENAMAD_CATEGORY_MISMATCH"]


def test_shipped_registry_decides_where_the_code_is_unique():
    d = evaluate(good(has_contact=False), RULES, REASONS)
    assert d.action == EDIT and d.reason_codes == ["MISSING_CONTACT_INFO"]


def test_site_inactive_cancels_with_the_real_code():
    d = evaluate(good(site_active=False), RULES, REASONS)
    assert d.action == CANCEL and d.reason_codes == ["WEBSITE_IS_INACTIVE"]


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


def test_per_category_minimum_overrides_the_group_default():
    import copy as c
    rules = c.deepcopy(RULES)
    rules["min_products"]["by_category_fa"]["values"] = {"مد و پوشاک": 80}
    assert evaluate(good(category_name="مد و پوشاک", product_count=60), rules, confirmed(ALL)).action == EDIT
    assert evaluate(good(category_name="مد و پوشاک", product_count=90), rules, confirmed(ALL)).action == APPROVE
    assert evaluate(good(category_name="سایر", product_count=60), rules, confirmed(ALL)).action == APPROVE   # falls back to default 40


def test_missing_sitemap_uses_its_own_reason():
    d = evaluate(good(product_count=3, has_sitemap=False), RULES, confirmed(ALL))
    assert d.reason_keys == ["SITEMAP_MISSING"]
