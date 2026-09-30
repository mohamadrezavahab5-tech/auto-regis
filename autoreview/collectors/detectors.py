"""Page-content detectors. Each answers True or None - never False.

Plain HTTP cannot prove that something is ABSENT (it may be rendered by JavaScript, hidden in a widget, ...), so the engine only
receives "proven present" or "unknown". Unknown sends the request to MANUAL; a human looks. That is the safe direction."""
import re
from typing import Optional

_ENAMAD_LINK = re.compile(r"(?:trustseal\.)?enamad\.ir/", re.I)
_CART_WORDS = (
    "افزودن به سبد", "افزودن به سبد خرید", "اضافه به سبد", "اضافه کردن به سبد", "add to cart", "add-to-cart", "add_to_cart",
    "addtocart", "ajax_add_to_cart", "data-add-to-cart",
)


def enamad_shown_on_site(html: str) -> Optional[bool]:
    """True when the page links to / embeds the enamad trust seal. Not found => None (a seal injected by script is invisible here)."""
    return True if html and _ENAMAD_LINK.search(html) else None


def can_add_to_cart(product_html: str) -> Optional[bool]:
    """True when a product page carries add-to-cart markup or wording. Otherwise None - never a claim that the cart is broken."""
    if not product_html:
        return None
    low = product_html.lower()
    return True if any(w in low for w in _CART_WORDS) else None
