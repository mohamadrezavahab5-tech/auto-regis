"""Reason registry: the single place that knows the literal NBO reason texts."""
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def load_reasons(path=None) -> dict:
    with open(path or CONFIG_DIR / "reasons.json", encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def exact_text(reasons: dict, key: str):
    """The literal NBO text, or None when it has not been confirmed (then nothing may be typed into NBO)."""
    entry = reasons.get(key)
    text = entry.get("exact_text") if entry else None
    return text.strip() if isinstance(text, str) and text.strip() else None
