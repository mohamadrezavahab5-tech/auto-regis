"""Website facts over plain HTTP - no browser window, no tabs.

Every function takes an async `fetch(url) -> Fetched`, so tests run offline and the app shares one pooled HTTP client.
Golden rule: a fact is True/False only when what we downloaded PROVES it. Anything we cannot see - JavaScript-rendered
pages, bot challenges, timeouts, a partial read - is None, and None sends the request to a human."""
import asyncio
import gzip
import html as htmllib
import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import unquote, urljoin, urlparse

import httpx

from ..normalize import to_latin_digits

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "fa,en;q=0.8"}
MAX_BYTES = 15 * 1024 * 1024


@dataclass
class Fetched:
    ok: bool                       # got an HTTP answer < 400
    status: Optional[int] = None
    url: str = ""                  # final URL after redirects
    text: str = ""
    error: Optional[str] = None    # dns | dns_temp | timeout | ssl | connect | redirects | invalid_url | http_<code> | other
    truncated: bool = False        # the body was larger than the cap - only the first part was read
    total: Optional[int] = None    # X-WP-Total of a WordPress/WooCommerce REST answer (how many items exist in all)


Fetch = Callable[[str], Awaitable[Fetched]]


def _classify(e: Exception) -> str:
    msg = str(e)
    up = msg.upper()
    if "SSL" in up or "CERTIFICATE" in up or "TLS" in up:
        return "ssl"
    if "11001" in msg or "NAME OR SERVICE NOT KNOWN" in up or "NODENAME NOR SERVNAME" in up or "NO ADDRESS ASSOCIATED" in up:
        return "dns"                                    # authoritative "no such host"
    if "11002" in msg or "TEMPORARY FAILURE" in up or "GETADDRINFO" in up:
        return "dns_temp"                               # resolver hiccup: inconclusive
    return "connect"


def make_fetch(client: httpx.AsyncClient, retries: int = 1, cap: int = MAX_BYTES) -> Fetch:
    async def fetch(url: str) -> Fetched:
        last = None
        for attempt in range(retries + 1):
            try:
                async with client.stream("GET", url, headers=HEADERS, follow_redirects=True) as r:
                    if r.status_code >= 400:
                        return Fetched(False, r.status_code, str(r.url), "", f"http_{r.status_code}")
                    buf, truncated = bytearray(), False
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > cap:
                            truncated = True
                            break
                    data = bytes(buf[:cap])
                    if data[:2] == b"\x1f\x8b" and not truncated:          # .xml.gz sitemaps
                        try:
                            data = gzip.decompress(data)[:cap]
                        except (OSError, EOFError):
                            pass
                    total = r.headers.get("x-wp-total")
                    return Fetched(True, r.status_code, str(r.url), data.decode(r.encoding or "utf-8", errors="replace"), None, truncated,
                                   int(total) if total and total.strip().isdigit() else None)
            except httpx.TooManyRedirects:
                return Fetched(False, None, url, "", "redirects")
            except (httpx.InvalidURL, httpx.UnsupportedProtocol):
                return Fetched(False, None, url, "", "invalid_url")
            except httpx.TimeoutException:
                last = "timeout"
            except httpx.ConnectError as e:
                last = _classify(e)
                if last == "dns":                                         # a missing host does not come back on a retry
                    break
            except httpx.HTTPError as e:
                last = "ssl" if _classify(e) == "ssl" else "other"
            except (UnicodeError, ValueError):
                return Fetched(False, None, url, "", "invalid_url")
            if attempt < retries:
                await asyncio.sleep(0.6)
        return Fetched(False, None, url, "", last)
    return fetch


def has_scheme(site: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", str(site or "").strip(), re.I))


def base_url(site: str) -> str:
    s = str(site or "").strip()
    return s if has_scheme(s) else "https://" + s


def origin_of(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"


async def fetch_home(site: str, fetch: Fetch):
    """-> (home Fetched, tried [(url, error)]). A scheme-less address is tried as https first, then as http when the
    https connection itself fails (no TLS on the server, broken certificate)."""
    s = str(site or "").strip()
    first = base_url(s)
    home = await fetch(first)
    tried = [(first, home.error)]
    if not home.ok and not has_scheme(s) and home.error in ("connect", "ssl"):
        alt = "http://" + s
        h2 = await fetch(alt)
        tried.append((alt, h2.error))
        if h2.ok:
            home = h2
    return home, tried


def reachable(f: Fetched) -> Optional[bool]:
    """True = answered; False = definitely dead (no such host / 404 / 410 on the home page); None = inconclusive."""
    if f.ok:
        return True
    if f.error in ("dns", "http_404", "http_410"):
        return False
    return None


# ---- page text ---------------------------------------------------------------------------------------------------------
_BLOCKS = re.compile(r"<(script|style|noscript|svg|template)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_COMMENTS = re.compile(r"<!--.*?-->", re.S)
_TAGS = re.compile(r"<[^>]+>")


def strip_scripts(html: str) -> str:
    return _COMMENTS.sub(" ", _BLOCKS.sub(" ", html or ""))


def visible_text(html: str) -> str:
    return " ".join(htmllib.unescape(_TAGS.sub(" ", strip_scripts(html))).split())


_CHALLENGE = re.compile(r"arvancloud|__arvan|cf-chl|cf_chl|challenge-platform|just a moment\.\.\.|checking your browser|ddos protection by|"
                        r"ddos-guard|attention required|bot verification|please enable javascript and cookies|لطفا چند لحظه صبر کنید", re.I)


def is_challenge(html: str) -> bool:
    """A bot wall / DDoS-protection page answered instead of the real content (short page carrying a known marker)."""
    if not html:
        return False
    return len(visible_text(html)) < 800 and bool(_CHALLENGE.search(html[:20000]))


# ---- contact information -----------------------------------------------------------------------------------------------
_PHONE = re.compile(r"(?<!\d)(?:(?:\+|00)98[\s-]?|0)9\d{2}[\s-]?\d{3}[\s-]?\d{4}(?!\d)|(?<!\d)0\d{2}[\s-]?\d{3,4}[\s-]?\d{4}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CONTACT_WORDS = ("تماس با ما", "ارتباط با ما", "اطلاعات تماس", "شماره تماس", "تلفن تماس", "راه های ارتباطی", "راه‌های ارتباطی",
                  "پشتیبانی", "contact us", "contact")
_CONTACT_HREF = re.compile(r"""href\s*=\s*["']?\s*(?:tel:|mailto:|(?:https?:)?//(?:wa\.me|api\.whatsapp\.com|t\.me)/|[^"'\s>]*contact)""", re.I)
CONTACT_PATHS = ("/contact-us", "/contact", "/contactus")
MIN_VISIBLE_TEXT = 300


def contact_on_page(html: str) -> Optional[bool]:
    """True: a phone, e-mail, tel:/mailto:/WhatsApp link, a link to a contact page, or contact wording is on the page.
    None: the page shows almost no text (rendered by JavaScript, or empty) - we cannot tell. False: a real page without any."""
    if not html:
        return None
    body = strip_scripts(html)
    text = to_latin_digits(visible_text(html)).lower()
    if len(text) < MIN_VISIBLE_TEXT:
        return None
    if _CONTACT_HREF.search(body) or _PHONE.search(text) or _EMAIL.search(text) or any(w in text for w in _CONTACT_WORDS):
        return True
    return False


async def has_contact(home: Fetched, fetch: Fetch) -> Optional[bool]:
    """Home page first; when it shows no contact detail, the usual contact pages are tried before calling it missing."""
    on_home = contact_on_page(home.text)
    if on_home is not False:
        return on_home
    origin = origin_of(home.url)
    for path in CONTACT_PATHS:
        page = await fetch(origin + path)
        if page.ok:
            return True if contact_on_page(page.text) is not False else None
        if page.error not in ("http_404", "http_410"):
            return None                                 # could not look: unknown, never "missing"
    return False


# ---- sitemap / product count -------------------------------------------------------------------------------------------
_LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*([^<\]]+?)\s*(?:\]\]>)?\s*</loc>", re.I)
_NOT_PRODUCT = re.compile(r"products?[_-]?(?:cat|tag|brand|categor|attribute|attr|type|feed|variation|collection)", re.I)
_PRODUCT_FILE = re.compile(r"(?:^|[/_.\-])(?:products?|محصول(?:ات)?)(?=$|[/_.\-\d])", re.I)
_PRODUCT_PAGE = re.compile(r"/(?:products?|محصول|product-page)/[^/?#]+", re.I)
STANDARD_SITEMAPS = ("/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml", "/product-sitemap.xml", "/sitemap-index.xml")


def is_product_sitemap(url: str) -> bool:
    """By the PATH of the sitemap file only (a shop domain like 'insoshop.ir' says nothing about its sitemaps)."""
    path = unquote(urlparse(url).path).lower()
    return not _NOT_PRODUCT.search(path) and bool(_PRODUCT_FILE.search(path))


def sitemap_locs(xml: str) -> list:
    return [htmllib.unescape(x).strip() for x in _LOC.findall(xml or "")]


def _looks_like(xml: str, tag: str) -> bool:
    return re.search(r"<(?:\w+:)?" + tag + r"\b", xml or "", re.I) is not None


async def count_products(origin: str, fetch: Fetch, max_files: int = 40) -> dict:
    """-> {has_sitemap: bool|None, product_count: int|None, complete: bool, basis: str, samples: [product page URLs]}.

    Never guesses: a count comes from sitemap files that are recognisably PRODUCT sitemaps. 'complete' is False when some
    product sitemap could not be read (error, cut off, too many files): the count is then only a lower bound, which can
    prove "enough products" but never "too few". A generic sitemap that lists product pages ('/product/...') gives a
    lower bound in the same way."""
    base = origin.rstrip("/")
    robots = await fetch(base + "/robots.txt")
    listed = re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)", robots.text) if robots.ok else []
    listed = [urljoin(base + "/", u) for u in listed] + [base + p for p in STANDARD_SITEMAPS]
    queue = list(dict.fromkeys(listed))
    seen, product_files, generic_product_pages, samples = set(), [], set(), []
    has_any, inconclusive, product_incomplete = False, False, False
    while queue:
        if len(seen) >= max_files:
            if any(is_product_sitemap(u) for u in queue if u not in seen):
                product_incomplete = True
            break
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        f = await fetch(url)
        if not f.ok:
            if f.error not in ("http_404", "http_410"):
                inconclusive = True
                if is_product_sitemap(url):
                    product_incomplete = True
            continue
        is_index, is_set = _looks_like(f.text, "sitemapindex"), _looks_like(f.text, "urlset")
        if not (is_index or is_set):
            if is_challenge(f.text):                    # a bot wall answered instead of the file: we did not see the sitemap
                inconclusive = True
                if is_product_sitemap(url):
                    product_incomplete = True
            continue
        has_any = True
        locs = sitemap_locs(f.text)
        if f.truncated and is_product_sitemap(url):
            product_incomplete = True
        if is_index:
            children = [urljoin(url, l) for l in locs if l not in seen]
            queue[:0] = [c for c in children if is_product_sitemap(c)]         # product files first, before the cap bites
            queue.extend(c for c in children if not is_product_sitemap(c))
        elif is_product_sitemap(url):
            pages = [l for l in locs if not l.rstrip("/").endswith(("/shop", "/products", "/product"))]
            product_files.append((url, len(pages)))
            samples.extend(l for l in pages if _PRODUCT_PAGE.search(unquote(l)))
            samples.extend(pages[:3])
        else:
            generic_product_pages.update(l for l in locs if _PRODUCT_PAGE.search(unquote(l)))
    samples = list(dict.fromkeys(samples))[:5]
    if product_files:
        files = ", ".join(u for u, _ in product_files[:5]) + (" …" if len(product_files) > 5 else "")
        return {"has_sitemap": True, "product_count": sum(n for _, n in product_files), "complete": not product_incomplete,
                "basis": "product sitemap: " + files, "samples": samples}
    if generic_product_pages:
        pages = sorted(generic_product_pages)
        return {"has_sitemap": True, "product_count": len(pages), "complete": False,
                "basis": "product pages listed in a general sitemap (lower bound)", "samples": pages[:5]}
    if has_any:
        return {"has_sitemap": True, "product_count": None, "complete": False,
                "basis": "sitemap exists but no product sitemap recognised - count unknown", "samples": []}
    return {"has_sitemap": None if inconclusive else False, "product_count": None, "complete": False,
            "basis": "sitemap fetch inconclusive" if inconclusive else "no sitemap found", "samples": []}


# The shop's own catalogue API (WooCommerce / WordPress), read like the old engine did besides the sitemap: an exact total
# straight from the shop, without guessing from page text. Public endpoints only; nothing is ever sent but a GET.
PRODUCT_APIS = ("/wp-json/wc/store/v1/products?per_page=5", "/wp-json/wc/store/products?per_page=5",
                "/wp-json/wp/v2/product?per_page=5&_fields=link")


async def count_products_api(origin: str, fetch: Fetch) -> dict:
    """-> {product_count: int|None, samples: [product page URLs], basis: str}. None = the shop has no such public API."""
    base = origin.rstrip("/")
    for path in PRODUCT_APIS:
        r = await fetch(base + path)
        if not r.ok or r.total is None:
            continue
        try:
            items = json.loads(r.text)
        except ValueError:
            continue
        if not isinstance(items, list):
            continue
        samples = [str(i.get("permalink") or i.get("link")) for i in items if isinstance(i, dict) and (i.get("permalink") or i.get("link"))]
        return {"product_count": r.total, "samples": samples, "basis": "api " + path.split("?")[0]}
    return {"product_count": None, "samples": [], "basis": ""}
