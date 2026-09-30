"""Text/site/name normalisation. Pure functions, no I/O."""
import re

_ZW = re.compile(r"[\u200c\u200f\u200e]")
_AR = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "ى": "ی", "أ": "ا", "إ": "ا", "ؤ": "و", "ئ": "ی"})


def normalize_text(value) -> str:
    """Persian/Arabic letter unification, zero-width and whitespace clean-up, lower case."""
    s = _ZW.sub(" ", str(value or "")).translate(_AR)
    return " ".join(s.split()).lower()


def normalize_site(value) -> str:
    """Bare domain: no scheme, www, path, query, fragment or port."""
    s = str(value or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = re.sub(r"^www\.", "", s)
    for sep in ("?", "#", "/"):
        s = s.split(sep)[0]
    s = s.split(":")[0]
    return re.sub(r"\s+", "", s)


def _name_tokens(value, zwnj_as_space: bool) -> list:
    s = str(value or "").translate(_AR)
    s = re.sub(r"[‎‏]", "", s)
    s = re.sub(r"‌+", " " if zwnj_as_space else "", s)      # NBO/enamad type ZWNJ inconsistently: 'مهر‌ابی' vs 'مهرابی'
    return " ".join(s.split()).lower().split()


def names_equal(a, b) -> "bool | None":
    """True  = same person's name (order-insensitive token equality; ZWNJ typed either way is tolerated).
    False = clearly different names.
    None  = cannot tell: a name is missing, OR one name is a strict part of the other ('علی رضایی' vs 'علی رضایی نژاد') -
            that may be a shortened name or a different person, so it is never called a mismatch.
    There is deliberately NO similarity score."""
    for zw in (True, False):
        ta, tb = _name_tokens(a, zw), _name_tokens(b, zw)
        if not ta or not tb:
            return None
        if sorted(ta) == sorted(tb):
            return True
    ta, tb = set(_name_tokens(a, True)), set(_name_tokens(b, True))
    ka, kb = set(_name_tokens(a, False)), set(_name_tokens(b, False))
    if ta < tb or tb < ta or ka < kb or kb < ka:
        return None
    return False
