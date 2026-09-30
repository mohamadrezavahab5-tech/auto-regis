"""Website facts over plain HTTP - no browser window, no tabs. A headless render is only a fallback (see render.py, later).

Injection point: every function takes an async `fetch(url) -> Fetched` so tests run offline and the app can share one
pooled HTTP client with rate limits."""
import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import urljoin, urlparse

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AutoReview/0.1"


@dataclass
class Fetched:
    ok: bool                       # got an HTTP answer < 400
    status: Optional[int] = None
    url: str = ""                  # final URL after redirects
    text: str = ""
    error: Optional[str] = None    # dns | timeout | ssl | connect | http_<code> | other


Fetch = Callable[[str], Awaitable[Fetched]]


def make_fetch(client: httpx.AsyncClient, retries: int = 1) -> Fetch:
    async def fetch(url: str) -> Fetched:
        last = None
        for _ in range(retries + 1):
            try:
                r = await client.get(url, headers={"User-Agent": UA}, follow_redirects=True)
                if r.status_code >= 400:
                    return Fetched(False, r.status_code, str(r.url), "", f"http_{r.status_code}")
                return Fetched(True, r.status_code, str(r.url), r.text)
            except httpx.ConnectTimeout: last = "timeout"
            except httpx.ReadTimeout: last = "timeout"
            except httpx.ConnectError as e: last = "dns" if "getaddrinfo" in str(e) or "Name or service" in str(e) else "connect"
            except httpx.HTTPError as e: last = "ssl" if "SSL" in str(e) or "CERTIFICATE" in str(e).upper() else "other"
            await asyncio.sleep(0.5)
        return Fetched(False, None, url, "", last)
    return fetch


def base_url(site: str) -> str:
    s = str(site or "").strip()
    return s if re.match(r"^[a-z]+://", s, re.I) else "https://" + s


def reachable(f: Fetched) -> Optional[bool]:
    """True = answered; False = definitely dead (DNS failure / HTTP 404/410 on the home page / refused); None = inconclusive."""
    if f.ok:
        return True
    if f.error in ("dns", "http_404", "http_410"):
        return False
    return None


# ---- contact information ----------------------------------------------------------------------------------------
_PHONE = re.compile(r"(?:\+98|0098|0)?9\d{9}|0\d{2}[\s-]?\d{7,8}")
_CONTACT_WORDS = ("تماس با ما", "تماس با ما", "ارتباط با ما", "contact", "پشتیبانی", "support")


def has_contact(html: str) -> Optional[bool]:
    """True when a phone, tel:, mailto: or a contact/support wording is present. An empty page => None (cannot tell)."""
    if not html or len(html) < 200:
        return None
    text = _digits(html).lower()
    if "tel:" in text or "mailto:" in text or "wa.me/" in text or _PHONE.search(text):
        return True
    return True if any(w in text for w in _CONTACT_WORDS) else False


_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _digits(s: str) -> str:
    return s.translate(_FA)


# ---- sitemap / product count -------------------------------------------------------------------------------------
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


async def count_products(site: str, fetch: Fetch, max_files: int = 12) -> dict:
    """-> {'has_sitemap': bool|None, 'product_count': int|None, 'basis': str, 'sample_url': first product URL|None}. Never guesses a number:
    a count is reported only from a sitemap that is recognisably a PRODUCT sitemap (or a WooCommerce/Shopify pattern)."""
    base = base_url(site).rstrip("/")
    robots = await fetch(base + "/robots.txt")
    listed = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots.text) if robots.ok else []
    for path in ("/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml", "/product-sitemap.xml"):
        listed.append(urljoin(base + "/", path.lstrip("/")))
    seen, product_files, has_any, inconclusive, sample = set(), [], False, False, None
    queue = list(dict.fromkeys(listed))
    while queue and len(seen) < max_files:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        f = await fetch(url)
        if not f.ok:
            if f.error not in ("http_404", "http_410"):
                inconclusive = True
            continue
        if "<urlset" not in f.text and "<sitemapindex" not in f.text:
            continue
        has_any = True
        locs = _LOC.findall(f.text)
        if "<sitemapindex" in f.text:
            queue.extend(l for l in locs if l not in seen)
        elif re.search(r"product|محصول|shop", url, re.I):
            product_files.append((url, len(locs)))
            sample = sample or (locs[0] if locs else None)
    if product_files:
        return {"has_sitemap": True, "product_count": sum(n for _, n in product_files), "basis": "product sitemap(s): " + ", ".join(u for u, _ in product_files), "sample_url": sample}
    if has_any:
        return {"has_sitemap": True, "product_count": None, "basis": "sitemap exists but no product sitemap recognised - count unknown", "sample_url": None}
    return {"has_sitemap": None if inconclusive else False, "product_count": None, "basis": "no sitemap found" if not inconclusive else "sitemap fetch inconclusive", "sample_url": None}
