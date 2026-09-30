import copy
import json

import httpx

from autoreview import store
from autoreview.pipeline import Runner, load_json
from autoreview.reasons import load_reasons

HOME = "<html>" + "x" * 300 + " tel:02112345678 <a href='https://trustseal.enamad.ir/?id=7&code=c'>e</a></html>"
PROFILE = ("<div>shop.ir</div><div>صاحب امتیاز :</div><div>علی رضایی</div><div>تاریخ اعتبار :</div><div>معتبر تا تاریخ 1410/01/01</div>"
           "<table><tbody><tr><td>1</td><td>فروش پوشاک، کیف، کفش و محصولات چرمی</td><td></td><td>-</td><td>-</td><td>-</td><td>تایید شده</td></tr></tbody></table>")
PRODUCT_SITEMAP = "<urlset>" + "".join(f"<url><loc>https://shop.ir/product/{i}</loc></url>" for i in range(80)) + "</urlset>"


def handler(request: httpx.Request):
    h, path = request.url.host, request.url.path
    if h == "enamad.ir":
        if request.method == "POST":
            return httpx.Response(200, json={"id": 7, "code": "c", "enamad_status": 1, "expdate": "1410/01/01"})
        return httpx.Response(200, text="home")
    if h == "trustseal.enamad.ir":
        return httpx.Response(200, text=PROFILE)
    if h == "shop.ir":
        if path == "/robots.txt":
            return httpx.Response(200, text="Sitemap: https://shop.ir/product-sitemap.xml")
        if path == "/product-sitemap.xml":
            return httpx.Response(200, text=PRODUCT_SITEMAP)
        if path.startswith("/product/"):
            return httpx.Response(200, text="<button class='add_to_cart'>افزودن به سبد خرید</button>" + "x" * 300)
        if path == "/":
            return httpx.Response(200, text=HOME)
    if h == "dead.ir":
        raise httpx.ConnectError("getaddrinfo failed")
    return httpx.Response(404)


def row(smr, site, holder="علی رضایی"):
    return {"smr": smr, "site": site, "category": "مد و پوشاک", "created_at": "1405/07/08", "account_holder": holder,
            "owner_name": "علی", "owner_family": "رضایی", "has_online": "true", "has_instore": "false"}


def test_batch_end_to_end_offline(tmp_path):
    reasons = copy.deepcopy(load_reasons())
    rules = load_json("rules.json")
    r = Runner(tmp_path / "t.db", rules=rules, reasons=reasons, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    rows = [row("A", "shop.ir"), row("B", "dead.ir"), row("C", "shop.ir", holder="فرد دیگر"), row("D", "dup.ir")]
    r.start(rows, approved_nbo=[{"id": "OLD", "site": "dup.ir"}], run_id="t1")
    r.join(60)
    assert r.progress.state == "finished" and r.progress.done == 4
    db = store.connect(tmp_path / "t.db")
    got = {x["smr"]: x for x in store.results_of(db, "t1")}
    assert got["A"]["action"] == "APPROVE"
    assert got["B"]["action"] == "MANUAL"                                   # unreachable site => human
    assert got["C"]["action"] == "EDIT" and got["C"]["reason_codes"] == ["OWNER_MISMATCH"]
    assert got["D"]["action"] == "CANCEL" and got["D"]["reason_codes"] == ["DUPLICATE_REQUEST"]


def test_stop_ends_the_run_cleanly(tmp_path):
    r = Runner(tmp_path / "t.db", client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    r.start([row(str(i), "shop.ir") for i in range(50)], run_id="t2")
    r.stop(); r.join(60)
    assert r.progress.state == "stopped"


def test_export_has_three_tabs(tmp_path):
    from openpyxl import load_workbook
    from autoreview.export import write_xlsx
    res = [dict(smr="A", site="a.ir", category="c", created_at="d", action="MANUAL", reason_keys=[], reason_codes=[], notes=["n"], decided_at="")]
    write_xlsx(tmp_path / "o.xlsx", res)
    wb = load_workbook(tmp_path / "o.xlsx")
    assert wb.sheetnames == ["نتایج", "صف دستی", "خلاصه"] and wb["صف دستی"].max_row == 2
