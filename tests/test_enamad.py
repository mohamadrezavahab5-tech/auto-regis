import asyncio
import json

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
    assert f == dict(has_enamad=True, enamad_expired=False, owner_matches_account_holder=True, category_relation="match")


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


def test_lookup_end_to_end_with_a_fake_transport():
    def handler(request: httpx.Request):
        if request.url.host == "enamad.ir" and request.method == "GET":
            return httpx.Response(200, text="home", headers={"set-cookie": "a=b"})
        if request.url.path == "/Home/GetData":
            body = request.content.decode()
            if "missing.ir" in body:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"id": 7, "code": "c", "enamad_status": 1, "expdate": "1406/05/10"})
        if request.url.host == "trustseal.enamad.ir":
            return httpx.Response(200, text=PROFILE)
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await en.lookup(c, "https://www.shop-example.ir/"), await en.lookup(c, "missing.ir")
    ok, missing = asyncio.run(run())
    assert ok.found is True and ok.owner == "علی رضایی" and ok.status == 1
    assert missing.found is False
