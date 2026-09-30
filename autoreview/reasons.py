"""Reason registry: maps our internal rule keys to NBO reason codes and dropdown labels."""
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_reasons(path=None) -> dict:
    with open(path or CONFIG_DIR / "reasons.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def reason_code(reasons: dict, key: str):
    """The NBO reason code, or None when it is not unambiguous (then nothing may be decided with it)."""
    entry = reasons.get(key) or {}
    code = entry.get("nbo_code")
    return code.strip() if isinstance(code, str) and code.strip() else None


def dropdown_text(reasons: dict, key: str):
    """The Persian label to pick in NBO's dropdown, or None when it has not been read from NBO itself."""
    entry = reasons.get(key) or {}
    text = entry.get("dropdown_text")
    return text.strip() if isinstance(text, str) and text.strip() else None
