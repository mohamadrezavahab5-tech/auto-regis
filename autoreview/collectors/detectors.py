"""Page-content detectors.

enamad_shown_on_site / can_add_to_cart answer True or None - never False: plain HTTP cannot prove that something is ABSENT
(it may be drawn by JavaScript), so the engine only ever gets "proven present" or "unknown", and unknown goes to a human.
page_problem names pages that are not the shop at all (bot wall, parked domain, suspended hosting, under construction)."""
import re
from typing import Optional

from .site import is_challenge, strip_scripts, visible_text

_ENAMAD_SEAL = re.compile(r"(?:trustseal\.)?enamad\.ir/", re.I)
_CART = re.compile(r"add[-_ ]?to[-_ ]?cart|addtocart|افزودن\s+به\s+سبد|اضافه\s+(?:کردن\s+)?به\s+سبد|/cart/add|name=[\"']add-to-cart[\"']|"
                   r"single_add_to_cart_button|ajax_add_to_cart|data-product-add", re.I)
_PLACEHOLDER = re.compile(
    r"account has been suspended|this account is suspended|website is under construction|under construction|coming soon|"
    r"domain (?:is )?for sale|this domain is parked|buy this domain|parked free|default web ?site page|apache2 (?:ubuntu|debian) default page|"
    r"welcome to nginx|iis windows server|index of /|"
    r"سایت در دست ساخت|در حال ساخت|در دست طراحی|در حال بروزرسانی|در حال به ?روز ?رسانی|به زودی|بزودی|دامنه فروشی|این دامنه به فروش|"
    r"هاست (?:شما )?مسدود|سرویس (?:شما )?مسدود|سایت موقتا غیرفعال|سایت غیر فعال",
    re.I)


def page_problem(html: str) -> Optional[str]:
    """'challenge' | 'placeholder' | None. Only SHORT pages are judged, so a real shop that says 'به زودی' about one product
    is never mistaken for a parked domain."""
    if is_challenge(html):
        return "challenge"
    text = visible_text(html or "")
    if len(text) < 1500 and _PLACEHOLDER.search(text + " " + (html or "")[:3000]):
        return "placeholder"
    return None


def enamad_shown_on_site(html: str) -> Optional[bool]:
    """True when the page links to / embeds the enamad trust seal. Not found => None (a seal injected by script is invisible here)."""
    return True if html and _ENAMAD_SEAL.search(html) else None


def can_add_to_cart(product_html: str) -> Optional[bool]:
    """True when a product page carries a real add-to-cart button/form (scripts are ignored: a cart script loaded on every page
    proves nothing). Otherwise None - never a claim that the cart is broken."""
    if not product_html:
        return None
    return True if _CART.search(strip_scripts(product_html)) else None
