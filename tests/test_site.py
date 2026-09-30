import asyncio

from autoreview.collectors.site import Fetched, base_url, count_products, has_contact, reachable


def run(coro):
    return asyncio.run(coro)


def fake(pages):
    async def fetch(url):
        if url in pages:
            return Fetched(True, 200, url, pages[url])
        return Fetched(False, 404, url, "", "http_404")
    return fetch


def test_reachability_is_tri_state():
    assert reachable(Fetched(True, 200, "u", "x")) is True
    assert reachable(Fetched(False, None, "u", "", "dns")) is False
    assert reachable(Fetched(False, None, "u", "", "timeout")) is None      # inconclusive, never "dead"
    assert reachable(Fetched(False, 503, "u", "", "http_503")) is None


def test_contact_detection():
    page = "<html>" + "x" * 300 + "<a href='tel:+982112345678'>call</a></html>"
    assert has_contact(page) is True
    assert has_contact("<html>" + "x" * 300 + "شماره ۰۹۱۲۳۴۵۶۷۸۹ </html>") is True
    assert has_contact("<html>" + "y" * 300 + "</html>") is False
    assert has_contact("") is None and has_contact("<html></html>") is None        # nothing loaded => unknown


def test_product_count_from_a_product_sitemap_via_index():
    locs = "".join(f"<url><loc>https://s.ir/p/{i}</loc></url>" for i in range(45))
    pages = {
        "https://s.ir/sitemap_index.xml": "<sitemapindex><sitemap><loc>https://s.ir/product-sitemap1.xml</loc></sitemap><sitemap><loc>https://s.ir/post-sitemap.xml</loc></sitemap></sitemapindex>",
        "https://s.ir/product-sitemap1.xml": f"<urlset>{locs}</urlset>",
        "https://s.ir/post-sitemap.xml": "<urlset><url><loc>https://s.ir/blog/1</loc></url></urlset>",
    }
    r = run(count_products("s.ir", fake(pages)))
    assert r["product_count"] == 45 and r["has_sitemap"] is True


def test_sitemap_without_product_part_gives_unknown_count_not_a_guess():
    pages = {"https://s.ir/sitemap.xml": "<urlset><url><loc>https://s.ir/a</loc></url></urlset>"}
    r = run(count_products("s.ir", fake(pages)))
    assert r["has_sitemap"] is True and r["product_count"] is None


def test_no_sitemap_is_reported_as_missing():
    r = run(count_products("s.ir", fake({})))
    assert r["has_sitemap"] is False and r["product_count"] is None


def test_inconclusive_fetch_is_not_reported_as_missing():
    async def fetch(url):
        return Fetched(False, None, url, "", "timeout")
    r = run(count_products("s.ir", fetch))
    assert r["has_sitemap"] is None


def test_base_url_adds_scheme():
    assert base_url("shop.ir") == "https://shop.ir" and base_url("http://a.ir") == "http://a.ir"
