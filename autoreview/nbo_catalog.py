"""NBO's own code -> Persian label maps, parsed from NBO's front-end JavaScript (read-only).

NBO ships the reason dropdown labels inside a lazy JS chunk as entries like
    (0,u.Z)(s,c.Xn.INVALID_URL,"آدرس ...")
Two maps matter: Edit reasons ("Required Editing") and Cancel reasons. Reading the labels from there (instead of typing them
by hand) means the executor always selects EXACTLY the text NBO shows. If a code has no label the executor must refuse."""
import re

_ENTRY = re.compile(r'\(0,\w+\.\w+\)\((\w+),(\w+)\.(\w+)\.([A-Z][A-Z_]+),"((?:[^"\\]|\\.)*)"\)')
_ESC = re.compile(r"\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)")
_SIMPLE = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'", "/": "/"}


def decode_js_string(s: str) -> str:
    def rep(m):
        e = m.group(1)
        if e[0] in "ux" and len(e) > 1:
            return chr(int(e[1:], 16))
        return _SIMPLE.get(e, e)
    return _ESC.sub(rep, s)


def parse_label_maps(js_text: str) -> dict:
    """-> {'<map var>': {'CODE': 'label', ...}}. Map variable names are minified, so callers identify a map by its codes."""
    maps = {}
    for var, _enum_a, _enum_b, code, raw in _ENTRY.findall(js_text):
        maps.setdefault(var, {})[code] = decode_js_string(raw)
    return maps


def find_reason_maps(maps: dict) -> dict:
    """Pick the Edit and Cancel maps by their signature codes. Missing => {} (the caller must refuse to act)."""
    out = {}
    for m in maps.values():
        if "INSUFFICIENT_PRODUCT_VARIETY" in m and "DUPLICATE_REQUEST" in m:
            out["cancel"] = m
        elif "INCOMPLETE_IDS" in m and "OWNER_MISMATCH" in m:
            out["edit"] = m
    return out


def label_for(reason_maps: dict, action: str, code: str):
    kind = {"EDIT": "edit", "CANCEL": "cancel"}.get(action)
    return (reason_maps.get(kind) or {}).get(code)
