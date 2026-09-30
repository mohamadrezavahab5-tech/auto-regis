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


def names_equal(a, b) -> "bool | None":
    """True/False when both names are usable, None when a name is missing.

    Order-insensitive token equality after normalisation. There is deliberately NO similarity score:
    two names that are merely alike are 'not equal' (the caller decides what a mismatch means)."""
    ta, tb = normalize_text(a).split(), normalize_text(b).split()
    if not ta or not tb:
        return None
    return sorted(ta) == sorted(tb)
