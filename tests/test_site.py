import asyncio

import httpx

from autoreview.collectors import detectors
from autoreview.collectors.site import (Fetched, base_url, contact_on_page, count_products, fetch_home, has_contact,
                                        is_product_sitemap, make_fetch, reachable)

TEXT = "فروشگاه اینترنتی پوشاک با ارسال سریع به سراسر کشور. " * 12          # a real page has some visible text


def run(coro):
    return asyncio.run(coro)


def fake(pages, errors=None):
    errors = errors or {}

    async def fetch(url):
        if url in errors:
            return Fetched(False, None, url, "", errors[url])
        if url in pages:
            return Fetched(True, 200, url, pages[url])
        return Fetched(False, 404, url, "", "http_404")
    return fetch


def test_reachability_is_tri_state():
    assert reachable(Fetched(True, 200, "u", "x")) is True
    assert reachable(Fetched(False, None, "u", "", "dns")) is False
    assert reachable(Fetched(False, None, "u", "", "dns_temp")) is None
    assert reachable(Fetched(False, None, "u", "", "timeout")) is None      # inconclusive, never "dead"
    assert reachable(Fetched(False, 503, "u", "", "http_503")) is None


def test_contact_detection_on_the_page():
    assert contact_on_page(f"<html><body>{TEXT}<a href='tel:+982112345678'>call</a></body></html>") is True
    assert contact_on_page(f"<html><body>{TEXT} شماره ۰۹۱۲۳۴۵۶۷۸۹ </body></html>") is True
    assert contact_on_page(f"<html><body>{TEXT} info@shop.ir</body></html>") is True
    assert contact_on_page(f"<html><body>{TEXT}</body></html>") is False
    assert contact_on_page("") is None and contact_on_page("<html></html>") is None                 # nothing loaded => unknown


def test_a_page_rendered_by_javascript_is_unknown_not_missing():
    spa = "<html><body><div id='root'></div><script>var phone='02112345678'; render()</script></body></html>"
    assert contact_on_page(spa) is None


def test_numbers_inside_scripts_are_not_a_phone():
    page = f"<html><body>{TEXT}<script>var t=09123456789; var id='02112345678';</script></body></html>"
    assert contact_on_page(page) is False


def test_contact_page_is_tried_before_calling_contact_missing():
    home = Fetched(True, 200, "https://s.ir/", f"<html><body>{TEXT}</body></html>")
    assert run(has_contact(home, fake({"https://s.ir/contact": f"<html><body>{TEXT} تلفن تماس ۰۲۱۱۲۳۴۵۶۷۸</body></html>"}))) is True
    assert run(has_contact(home, fake({}))) is False                                   # every contact page 404
    assert run(has_contact(home, fake({}, {"https://s.ir/contact-us": "timeout"}))) is None


def test_product_sitemap_is_judged_by_its_path_not_the_domain():
    assert is_product_sitemap("https://s.ir/product-sitemap.xml")
    assert is_product_sitemap("https://s.ir/product-sitemap2.xml")
    assert is_product_sitemap("https://s.ir/wp-sitemap-posts-product-1.xml")
    assert is_product_sitemap("https://s.ir/sitemap_products_1.xml?from=1&to=99")
    assert not is_product_sitemap("https://insoshop.ir/post-sitemap.xml")              # 'shop' in the domain means nothing
    assert not is_product_sitemap("https://s.ir/product_cat-sitemap.xml")
    assert not is_product_sitemap("https://s.ir/wp-sitemap-taxonomies-product_cat-1.xml")
    assert not is_product_sitemap("https://s.ir/sitemap.xml")


def test_product_count_from_a_product_sitemap_via_index():
    locs = "".join(f"<url><loc>https://s.ir/product/p{i}</loc></url>" for i in range(45))
    pages = {
        "https://s.ir/sitemap_index.xml": "<sitemapindex><sitemap><loc>https://s.ir/product-sitemap1.xml</loc></sitemap><sitemap><loc>https://s.ir/post-sitemap.xml</loc></sitemap></sitemapindex>",
        "https://s.ir/product-sitemap1.xml": f"<urlset>{locs}</urlset>",
        "https://s.ir/post-sitemap.xml": "<urlset><url><loc>https://s.ir/blog/1</loc></url></urlset>",
    }
    r = run(count_products("https://s.ir", fake(pages)))
    assert r["product_count"] == 45 and r["has_sitemap"] is True and r["complete"] is True
    assert r["samples"][0].startswith("https://s.ir/product/")


def test_shop_domain_posts_are_not_counted_as_products():
    pages = {"https://insoshop.ir/sitemap_index.xml": "<sitemapindex><sitemap><loc>https://insoshop.ir/post-sitemap.xml</loc></sitemap></sitemapindex>",
             "https://insoshop.ir/post-sitemap.xml": "<urlset>" + "<url><loc>https://insoshop.ir/blog/x</loc></url>" * 90 + "</urlset>"}
    r = run(count_products("https://insoshop.ir", fake(pages)))
    assert r["product_count"] is None


def test_cdata_and_escaped_ampersands_in_sitemaps():
    pages = {"https://s.ir/sitemap.xml": "<sitemapindex><sitemap><loc><![CDATA[https://s.ir/sitemap_products_1.xml?from=1&amp;to=9]]></loc></sitemap></sitemapindex>",
             "https://s.ir/sitemap_products_1.xml?from=1&to=9": "<urlset>" + "".join(f"<url><loc><![CDATA[https://s.ir/products/{i}]]></loc></url>" for i in range(12)) + "</urlset>"}
    r = run(count_products("https://s.ir", fake(pages)))
    assert r["product_count"] == 12 and r["complete"] is True


def test_unreadable_product_sitemap_makes_the_count_a_lower_bound():
    pages = {"https://s.ir/sitemap_index.xml": "<sitemapindex><sitemap><loc>https://s.ir/product-sitemap.xml</loc></sitemap><sitemap><loc>https://s.ir/product-sitemap2.xml</loc></sitemap></sitemapindex>",
             "https://s.ir/product-sitemap.xml": "<urlset>" + "<url><loc>https://s.ir/product/a</loc></url>" * 5 + "</urlset>"}
    r = run(count_products("https://s.ir", fake(pages, {"https://s.ir/product-sitemap2.xml": "timeout"})))
    assert r["product_count"] == 5 and r["complete"] is False


def test_general_sitemap_with_product_pages_gives_a_lower_bound():
    pages = {"https://s.ir/sitemap.xml": "<urlset>" + "".join(f"<url><loc>https://s.ir/product/{i}</loc></url>" for i in range(50)) + "<url><loc>https://s.ir/about</loc></url></urlset>"}
    r = run(count_products("https://s.ir", fake(pages)))
    assert r["product_count"] == 50 and r["complete"] is False


def test_sitemap_without_product_part_gives_unknown_count_not_a_guess():
    pages = {"https://s.ir/sitemap.xml": "<urlset><url><loc>https://s.ir/a</loc></url></urlset>"}
    r = run(count_products("https://s.ir", fake(pages)))
    assert r["has_sitemap"] is True and r["product_count"] is None


def test_no_sitemap_is_reported_as_missing_but_a_bot_wall_is_not():
    assert run(count_products("https://s.ir", fake({})))["has_sitemap"] is False
    wall = "<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>"
    r = run(count_products("https://s.ir", fake({"https://s.ir/sitemap.xml": wall})))
    assert r["has_sitemap"] is None


def test_inconclusive_fetch_is_not_reported_as_missing():
    async def fetch(url):
        return Fetched(False, None, url, "", "timeout")
    assert run(count_products("https://s.ir", fetch))["has_sitemap"] is None


def test_base_url_adds_scheme():
    assert base_url("shop.ir") == "https://shop.ir" and base_url("http://a.ir") == "http://a.ir"


def test_home_falls_back_to_http_only_for_a_scheme_less_address():
    pages = {"http://s.ir": "<html>ok</html>"}
    errs = {"https://s.ir": "ssl", "https://t.ir": "ssl"}
    home, tried = run(fetch_home("s.ir", fake(pages, errs)))
    assert home.ok and home.url == "http://s.ir" and len(tried) == 2
    home, _ = run(fetch_home("https://t.ir", fake({"http://t.ir": "<html>ok</html>"}, errs)))
    assert not home.ok                                                                   # an explicit https address is not downgraded


def test_detectors():
    assert detectors.page_problem("<html><title>Just a moment...</title><body>checking your browser</body></html>") == "challenge"
    assert detectors.page_problem("<html><body><h1>به زودی</h1> سایت در دست ساخت است</body></html>") == "placeholder"
    assert detectors.page_problem(f"<html><body>{TEXT * 3} محصول جدید به زودی</body></html>") is None
    assert detectors.can_add_to_cart("<button name='add-to-cart' class='single_add_to_cart_button'>افزودن به سبد خرید</button>") is True
    assert detectors.can_add_to_cart("<html><script src='/wc-add-to-cart.js'>add_to_cart</script><p>ناموجود</p></html>") is None
    assert detectors.enamad_shown_on_site("<a href='https://trustseal.enamad.ir/?id=1&code=x'>") is True
    assert detectors.enamad_shown_on_site("<html></html>") is None


def test_real_fetch_caps_size_and_classifies_errors():
    big = b"<urlset>" + b"x" * 5000 + b"</urlset>"

    def handler(request):
        if request.url.host == "big.ir":
            return httpx.Response(200, content=big)
        if request.url.host == "gone.ir":
            return httpx.Response(410)
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            f = make_fetch(c, retries=0, cap=1000)
            return await f("https://big.ir/"), await f("https://gone.ir/"), await f("https://nohost.ir/")
    big_f, gone, nohost = run(go())
    assert big_f.ok and big_f.truncated and len(big_f.text) == 1000
    assert gone.error == "http_410" and reachable(gone) is False
    assert nohost.error == "dns" and reachable(nohost) is False


def test_product_lists_served_by_query_are_product_sitemaps():
    index = ("<sitemapindex><sitemap><loc>https://s.ir/sitemap.xml?path=products</loc></sitemap>"
             "<sitemap><loc>https://s.ir/sitemap.xml?path=products%2Fbrands</loc></sitemap>"
             "<sitemap><loc>https://s.ir/sitemap.xml?path=posts</loc></sitemap></sitemapindex>")
    products = "<urlset>" + "".join(f"<url><loc>https://s.ir/p/{i}</loc></url>" for i in range(70)) + "</urlset>"
    brands = "<urlset>" + "".join(f"<url><loc>https://s.ir/brand/{i}</loc></url>" for i in range(9)) + "</urlset>"
    r = run(count_products("https://s.ir", fake({"https://s.ir/sitemap.xml": index, "https://s.ir/sitemap.xml?path=products": products,
                                                  "https://s.ir/sitemap.xml?path=products%2Fbrands": brands})))
    assert r["product_count"] == 70 and r["complete"] is True
    assert is_product_sitemap("https://s.ir/sitemap.xml?section=products&page=2")
    assert not is_product_sitemap("https://s.ir/sitemap.xml?path=products%2Fcategories")
    assert not is_product_sitemap("https://s.ir/sitemap.xml?page=2")


def test_old_rule_opens_only_the_address_as_linked_no_http_fallback():
    pages = {"http://s.ir": f"<html><body>{TEXT}</body></html>"}
    errors = {"https://s.ir": "connect"}
    home, tried = run(fetch_home("s.ir", fake(pages, errors), http_fallback=False))
    assert not home.ok and tried == [("https://s.ir", "connect")]
    home, _ = run(fetch_home("s.ir", fake(pages, errors)))                          # the 'served' mode still falls back
    assert home.ok and home.url == "http://s.ir"


def test_phone_only_contact_needs_a_number(tmp_path):
    """Owner 2026-10-03: support means a phone number; e-mail or a 'contact us' link alone is not accepted."""
    from autoreview.collectors.site import find_phone, phones_on_page
    assert phones_on_page(f"<html><body>{TEXT} تلفن ۰۲۱-۱۲۳۴۵۶۷۸</body></html>")
    assert phones_on_page(f"<html><body>{TEXT}<a href='tel:+982112345678'>تماس</a></body></html>")
    assert not phones_on_page(f"<html><body>{TEXT} info@shop.ir <a href='/contact'>تماس با ما</a></body></html>")
    assert contact_on_page(f"<html><body>{TEXT} info@shop.ir</body></html>", phone_only=True) is False
    home = Fetched(True, 200, "https://s.ir/", f"<html><body>{TEXT} info@shop.ir <a href='/tamas'>تماس با ما</a></body></html>")
    found = run(find_phone(home, fake({"https://s.ir/tamas": f"<html><body>{TEXT} ۰۹۱۲۳۴۵۶۷۸۹</body></html>"})))
    assert found["found"] is True and found["page"] == "https://s.ir/tamas"
    nothing = run(find_phone(home, fake({})))
    assert nothing["found"] is None and "https://s.ir/tamas" in nothing["pages"]      # unknown: the browser looks next
