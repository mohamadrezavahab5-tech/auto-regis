import asyncio

import httpx

from autoreview.collectors import enamad as en
from autoreview.collectors.enamad import EnamadInfo, category_relation, parse_profile, to_facts

# Shaped exactly like the real trustseal.enamad.ir profile (observed 2026-10-01), reduced.
PROFILE = """
<html><body><script>var x=1;</script>
<div>شناسنامه کسب و کار</div><div>shop-example.ir</div>
<div>صاحب امتیاز :</div><div>علی رضایی&nbsp;</div>
<div>تاریخ اعطا :</div><div>1401/05/10</div>
<div>تاریخ اعتبار :</div><div>معتبر تا تاریخ 1406/05/10</div>
<table><thead><tr><th>#</th><th>عنوان خدمت</th></tr></thead><tbody>
<tr><td>1</td><td>فروش پوشاک، کیف، کفش و محصولات چرمی</td><td></td><td>---</td><td>---</td><td>---</td><td>تایید شده</td></tr>
<tr><td>2</td><td>فروش لوازم ورزشی و تفریحی</td><td></td><td>---</td><td>---</td><td>---</td><td>رد شده</td></tr>
</tbody></table></body></html>
"""

MAP = {"مد و پوشاک": ["فروش پوشاک، کیف، کفش و محصولات چرمی"], "کیف و کفش": ["فروش پوشاک، کیف، کفش و محصولات چرمی"],
       "تجهیزات کمپ و ورزشی": ["فروش لوازم ورزشی و تفریحی"]}


def test_parse_profile_reads_owner_domain_dates_and_only_approved_activities():
    p = parse_profile(PROFILE)
    assert p["owner"] == "علی رضایی"
    assert p["domain"] == "shop-example.ir"
    assert p["valid_until"] == "1406/05/10"
    assert p["activities"] == ["فروش پوشاک، کیف، کفش و محصولات چرمی"]     # the 'rejected' row is ignored


def test_category_relation_is_never_guessed():
    acts = ["فروش پوشاک، کیف، کفش و محصولات چرمی"]
    assert category_relation("مد و پوشاک", acts, MAP) == "match"
    assert category_relation("آرایشی و بهداشتی", acts, MAP) == "unknown"            # mapping not yet approved by the owner => no verdict
    assert category_relation("آرایشی و بهداشتی", acts, MAP, mismatch_allowed=True) == "mismatch"   # every title known, none supports it
    assert category_relation("آرایشی و بهداشتی", acts + ["عنوان ناشناخته"], MAP, mismatch_allowed=True) == "unknown"
    assert category_relation("مد و پوشاک", acts + ["عنوان ناشناخته"], MAP) == "match"  # supported by one known title
    assert category_relation("مد و پوشاک", [], MAP) == "unknown"


def info(**kw):
    base = dict(found=True, status=1, owner="علی رضایی", domain_shown="shop-example.ir", activities=["فروش پوشاک، کیف، کفش و محصولات چرمی"])
    base.update(kw)
    return EnamadInfo(**base)


def facts(i, holder="رضایی علی"):
    return to_facts(i, "https://www.shop-example.ir/x", holder, "مد و پوشاک", MAP)


def test_facts_for_a_valid_enamad():
    f = facts(info())
    assert f == dict(has_enamad=True, enamad_expired=False, owner_matches_account_holder=True, category_relation="match", category_why="")


def test_expired_missing_and_unreachable_are_different_facts():
    assert facts(info(status=5))["enamad_expired"] is True
    assert facts(EnamadInfo(False))["has_enamad"] is False
    assert facts(EnamadInfo(None, error="ReadTimeout"))["has_enamad"] is None          # site down => unknown, never "no enamad"


def test_suspended_and_foreign_domain_profiles_are_not_decided():
    assert facts(info(status=6))["has_enamad"] is None
    assert facts(info(domain_shown="other-site.ir"))["has_enamad"] is None


def test_owner_name_uses_exact_token_equality_not_similarity():
    assert facts(info(), holder="علی رضایی")["owner_matches_account_holder"] is True
    assert facts(info(), holder="علی رضاییان")["owner_matches_account_holder"] is False
    assert facts(info(owner=None))["owner_matches_account_holder"] is None


def _transport(getdata):
    def handler(request: httpx.Request):
        if request.url.host == "enamad.ir" and request.method == "GET":
            return httpx.Response(200, text="home", headers={"set-cookie": "a=b"})
        if request.url.path == "/Home/GetData":
            return getdata(request.content.decode())
        if request.url.host == "trustseal.enamad.ir":
            return httpx.Response(200, text=PROFILE)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def _lookup(getdata, site):
    async def run():
        async with httpx.AsyncClient(transport=_transport(getdata)) as c:
            return await en.lookup(c, site, retries=0)
    return asyncio.run(run())


def test_lookup_end_to_end_with_a_fake_transport():
    def getdata(body):
        if "missing.ir" in body:
            return httpx.Response(200, content=b"null", headers={"content-type": "application/json;charset=UTF-8"})   # real 'no enamad'
        return httpx.Response(200, json={"id": 7, "code": "c", "enamad_status": 1, "expdate": "1406/05/10"})
    ok = _lookup(getdata, "https://www.shop-example.ir/")
    assert ok.found is True and ok.owner == "علی رضایی" and ok.status == 1
    assert _lookup(getdata, "missing.ir").found is False


def test_an_odd_answer_is_unknown_never_no_enamad():
    for resp in (httpx.Response(200, json={}), httpx.Response(200, json={"message": "too many requests"}),
                 httpx.Response(200, json=[]), httpx.Response(429, json={"x": 1}), httpx.Response(200, text="<html>blocked</html>")):
        info = _lookup(lambda body, r=resp: r, "shop.ir")
        assert info.found is None, resp


def test_unknown_or_suspended_status_is_never_decided():
    for status in (2, 4, 6, 9):
        f = to_facts(info(status=status), "https://www.shop-example.ir/x", "علی رضایی", "مد و پوشاک", MAP)
        assert f["has_enamad"] is None, status
    assert to_facts(info(status=3), "shop-example.ir", "علی رضایی", "مد و پوشاک", MAP)["has_enamad"] is True


def test_profile_domain_with_www_still_belongs_to_the_site():
    f = to_facts(info(domain_shown="www.shop-example.ir"), "https://shop-example.ir", "علی رضایی", "مد و پوشاک", MAP)
    assert f["has_enamad"] is True and f["owner_matches_account_holder"] is True


def test_owner_label_and_value_on_one_line_and_missing_value():
    one_line = "<div>shop-example.ir</div><div>صاحب امتیاز : مریم احمدی</div><div>تاریخ اعتبار : معتبر تا تاریخ 1406/01/01</div>"
    p = parse_profile(one_line)
    assert p["owner"] == "مریم احمدی" and p["valid_until"] == "1406/01/01"
    empty = "<div>صاحب امتیاز :</div><div>تاریخ اعطا :</div><div>1401/01/01</div>"
    assert parse_profile(empty)["owner"] is None                       # never take the next label as the owner's name


def test_action_test_4_category_rule_recognises_known_activities():
    from autoreview.facts import old_category_match
    assert old_category_match(["فروش پوشاک، کیف، کفش و محصولات چرمی"])[1] == "مد و پوشاک"
    assert old_category_match(["کیف و کفش"]) == ("کیف و کفش", "کیف و کفش")              # an NBO category itself
    assert old_category_match([]) is None and old_category_match(["", None]) is None
    assert old_category_match(["xyz-unrelated-qwerty"]) is None


def test_an_unknown_category_says_its_real_cause():
    from autoreview import texts
    from autoreview.collectors.enamad import EnamadInfo, to_facts
    assert to_facts(EnamadInfo(None, error="timeout"), "a.ir", "x", "پوشاک", {})["category_why"] == "no_answer"
    note = texts.note_fa("could not determine: enamad category mapping [status:SUSPENDED]")
    assert "SUSPENDED" in note and "جدول نگاشت" not in note
