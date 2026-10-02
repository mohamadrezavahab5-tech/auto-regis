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
                  "agreement_ok", "can_add_to_cart", "has_contact"):
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


def with_rule(path, value):
    r = copy.deepcopy(RULES)
    cur = r
    for k in path[:-1]:
        cur = cur[k]
    cur[path[-1]] = value
    return r


def test_enamad_not_shown_on_site_uses_the_no_enamad_reason_when_switched_on():
    assert evaluate(good(enamad_shown_on_site=False), RULES, REASONS).action == APPROVE        # not in Action Test 4
    on = with_rule(("checks", "enamad_on_site", "enabled"), True)
    assert evaluate(good(enamad_shown_on_site=False), on, REASONS).reason_codes == ["MISSING_LICENSE"]
    assert evaluate(good(enamad_shown_on_site=None), on, REASONS).action == MANUAL


def test_owner_mismatch_follows_the_owner_setting():
    def rules_with(mode):
        r = copy.deepcopy(RULES)
        r["owner_mismatch_action"]["value"] = mode
        return r
    f = good(owner_matches_account_holder=False)
    assert evaluate(f, RULES, REASONS).reason_codes == ["OWNER_MISMATCH"]                 # shipped setting (owner, 2026-10-01): EDIT
    assert evaluate(f, rules_with("MANUAL"), REASONS).action == MANUAL
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


# ---- Action Test 4 (owner 2026-10-01: "all the old code's rules") ------------------------------------------------------------
def test_a_dead_address_is_edit_like_action_test_4_unless_switched_to_manual():
    assert evaluate(good(website_reachable=False), RULES, REASONS).reason_codes == ["INVALID_URL"]
    manual = with_rule(("unreachable_site_action", "value"), "MANUAL")
    assert evaluate(good(website_reachable=False), manual, REASONS).action == MANUAL


def test_http_only_site_is_edit_url():
    assert evaluate(good(ssl_ok=False), RULES, REASONS).reason_codes == ["INVALID_URL"]
    assert evaluate(good(ssl_ok=False), with_rule(("checks", "https", "enabled"), False), REASONS).action == APPROVE


def test_inactive_site_is_cancelled():
    d = evaluate(good(site_active=False), RULES, REASONS)
    assert d.action == CANCEL and d.reason_codes == ["WEBSITE_IS_INACTIVE"]


def test_gate_order_is_action_test_4s():
    # no enamad beats everything; https beats an inactive site; bank name is checked before the enamad owner
    assert evaluate(good(has_enamad=False, website_reachable=False), RULES, REASONS).reason_codes == ["MISSING_LICENSE"]
    assert evaluate(good(ssl_ok=False, site_active=False), RULES, REASONS).reason_codes == ["INVALID_URL"]
    d = evaluate(good(registrant_matches_account_holder=False, owner_matches_account_holder=False), RULES, REASONS)
    assert d.reason_codes == ["REGISTRANT_NAME_AND_BANK_ACCOUNT_OWNER_MISMATCH"]
    assert evaluate(good(has_contact=False, product_count=1), RULES, REASONS).reason_codes == ["MISSING_CONTACT_INFO"]


def test_enough_products_need_no_sitemap_and_short_counts_follow_the_sitemap():
    assert evaluate(good(has_sitemap=False, product_count=70), RULES, REASONS).action == APPROVE
    assert evaluate(good(has_sitemap=False, product_count=5), RULES, REASONS).reason_codes == ["SITEMAP_IS_MISSING"]
    assert evaluate(good(category_name="مد و پوشاک", product_count=59), RULES, REASONS).reason_codes ==         ["INSUFFICIENT_NUMBER_OF_PRODUCTS_IN_SITEMAP"]                                                   # default 60
    assert evaluate(good(category_name=None, product_count=30), RULES, REASONS).action == APPROVE        # no category: 25


def test_cart_unknown_without_product_pages_still_reports_missing_sitemap():
    d = evaluate(good(can_add_to_cart=None, has_sitemap=False, product_count=None), RULES, REASONS)
    assert d.reason_codes == ["SITEMAP_IS_MISSING"]
    assert evaluate(good(can_add_to_cart=None), RULES, REASONS).action == MANUAL


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
    d = evaluate(good(product_count=None, has_sitemap=False, can_add_to_cart=None), RULES, confirmed(ALL))
    assert d.reason_keys == ["SITEMAP_MISSING"]                  # no sitemap => no product page to try the cart on: still decided
    rules = copy.deepcopy(RULES)
    rules["sitemap_missing_action"]["value"] = "MANUAL"
    assert evaluate(good(product_count=None, has_sitemap=False), rules, confirmed(ALL)).action == MANUAL


def test_a_blocker_always_goes_to_a_person():
    d = evaluate(good(blocker="the website is a social-media page"), RULES, confirmed(ALL))
    assert d.action == MANUAL and "social-media" in d.notes[0]


def test_a_lower_bound_count_proves_enough_but_never_too_few():
    assert evaluate(good(product_count=90, product_count_complete=False), RULES, confirmed(ALL)).action == APPROVE
    d = evaluate(good(product_count=12, product_count_complete=False), RULES, confirmed(ALL))
    assert d.action == MANUAL and "at least 12" in d.notes[0]


def test_products_are_judged_before_the_cart():
    d = evaluate(good(product_count=5, can_add_to_cart=None), RULES, confirmed(ALL))
    assert d.action == EDIT and d.reason_keys == ["TOO_FEW_PRODUCTS"]


def test_old_name_rule_only_ever_confirms_the_same_name():
    from autoreview.facts import old_names_match
    assert old_names_match("سيدداود يوسف زاده", "سید داوود یوسف زاده")
    assert old_names_match("محمد حسن", "محمد حسن فردوسی")
    assert not old_names_match("علی رضایی", "زهرا خیامی پور")
    assert not old_names_match("", "علی")
