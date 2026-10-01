"""Text/site/name normalisation. Pure functions, no I/O."""
import re
from collections import Counter
from itertools import product

_ZW = re.compile(r"[‌‍‎‏‪-‮⁦-⁩﻿]")
_DIACRITICS = re.compile(r"[ً-ٰٟـ]")          # harakat, superscript alef, tatweel
_AR = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "ى": "ی", "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ؤ": "و", "ئ": "ی"})
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def to_latin_digits(value) -> str:
    return str(value or "").translate(_DIGITS)


def normalize_text(value) -> str:
    """Persian/Arabic letter unification (ي/ی, ك/ک, آ/ا, ...), no diacritics, zero-width and whitespace clean-up, lower case."""
    s = _DIACRITICS.sub("", str(value or ""))
    s = _ZW.sub(" ", s).translate(_AR)
    return " ".join(s.split()).lower()


def normalize_site(value) -> str:
    """Bare host: no scheme, www, user-info, path, query, fragment, port or trailing dot."""
    s = _ZW.sub("", to_latin_digits(value)).strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*:/*", "", s)                      # https://  http:/  (typos with one slash too)
    for sep in ("/", "?", "#", "\\"):
        s = s.split(sep)[0]
    s = s.rsplit("@", 1)[-1]
    s = s.split(":")[0]
    s = re.sub(r"\s+", "", s).strip(".")
    return re.sub(r"^www\d*\.", "", s)


# ---- person names --------------------------------------------------------------------------------------------------------
_HONORIFICS = {"اقای", "اقا", "خانم", "جناب", "سرکار", "دکتر", "مهندس", "mr", "mrs", "ms", "dr"}
_ARABIC_SCRIPT = re.compile(r"[؀-ۿ]")
_LATIN_SCRIPT = re.compile(r"[a-z]", re.I)


def _name_tokens(value) -> list:
    s = _DIACRITICS.sub("", str(value or "")).translate(_AR)
    s = re.sub(r"[‌‍]+", " ", s)                           # ZWNJ inside a name = a word gap ('حسین‌زاده')
    s = _ZW.sub("", s)
    s = re.sub(r"[^\w\s]", " ", s)                                    # dots, hyphens, brackets between name parts
    return [t for t in s.lower().split() if t not in _HONORIFICS]


def _segmentations(tokens) -> set:
    """Every way of writing the name with some neighbouring parts joined: 'محمد رضا احمدی' also reads as 'محمدرضا احمدی'."""
    n = len(tokens)
    if n == 0:
        return set()
    if n > 7:                                                         # 2^(n-1) grows fast; long strings are not names anyway
        return {tuple(sorted(tokens))}
    out = set()
    for joins in product((False, True), repeat=n - 1):
        parts, cur = [], tokens[0]
        for tok, join in zip(tokens[1:], joins):
            if join:
                cur += tok
            else:
                parts.append(cur)
                cur = tok
        parts.append(cur)
        out.add(tuple(sorted(parts)))
    return out


def _strict_sub(a: tuple, b: tuple) -> bool:
    ca, cb = Counter(a), Counter(b)
    return ca != cb and not (ca - cb)


def names_equal(a, b) -> "bool | None":
    """True  = the same name: same parts in any order, with spaces / ZWNJ / joined compound parts tolerated
               ('محمد رضا احمدی' = 'احمدی محمدرضا', 'حسین‌زاده' = 'حسینزاده' = 'حسین زاده').
    False = clearly different names.
    None  = cannot tell: a name is missing, the two are written in different scripts (Persian vs Latin), or one is a
            strict part of the other ('علی رضایی' vs 'علی رضایی نژاد' - a shortened name or a different person).
    There is deliberately NO similarity score."""
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return None
    ja, jb = " ".join(ta), " ".join(tb)
    if bool(_ARABIC_SCRIPT.search(ja)) != bool(_ARABIC_SCRIPT.search(jb)) or bool(_LATIN_SCRIPT.search(ja)) != bool(_LATIN_SCRIPT.search(jb)):
        return None
    sa, sb = _segmentations(ta), _segmentations(tb)
    if sa & sb:
        return True
    if any(_strict_sub(x, y) or _strict_sub(y, x) for x in sa for y in sb):
        return None
    return False
