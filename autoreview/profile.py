"""Who is signed in on this PC (name only - the password lives encrypted in crm_credential.xml, see crm_sync)."""
import json
from datetime import datetime, timezone

from .paths import user_dir


def _file():
    return user_dir() / "profile.json"


def load():
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(username, display_name, remember):
    data = {"username": username, "display_name": display_name or username, "remember": bool(remember),
            "last_login": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def clear():
    try:
        _file().unlink()
    except FileNotFoundError:
        pass
