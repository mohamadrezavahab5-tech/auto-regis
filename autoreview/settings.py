"""Shipped defaults (config/*.json) + this person's own changes (settings.json in their profile).

Only the paths listed in EDITABLE can be changed from the app, each with a type check. Everything else always comes from the
shipped config, so an update can still fix a default without being masked by an old personal copy. A settings file shared
by a colleague goes through the same filter (import_from), so a hand-edited file can never inject an unknown key."""
import copy
import json
import os
import tempfile
from pathlib import Path

from .paths import config_dir, user_dir

ACTIONS_OWNER = ("EDIT", "CANCEL", "MANUAL")
ACTIONS_UNREACHABLE = ("MANUAL", "EDIT")
GROUPS = ("gold", "special", "services", "education")


def _int_range(lo, hi):
    def check(v):
        return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi
    return check


def _is_bool(v):
    return isinstance(v, bool)


def _str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)


def _cat_limits(v):
    return isinstance(v, dict) and all(isinstance(k, str) and k.strip() and _int_range(0, 100000)(n) for k, n in v.items())


def _one_of(*options):
    return lambda v: v in options


# (file, dotted path) -> validator
EDITABLE = {
    ("rules", "min_products.default.value"): _int_range(0, 100000),
    ("rules", "min_products.services.value"): _int_range(0, 100000),
    ("rules", "min_products.education.value"): _int_range(0, 100000),
    ("rules", "min_products.by_category_fa.values"): _cat_limits,
    ("rules", "category_groups.gold"): _str_list,
    ("rules", "category_groups.special"): _str_list,
    ("rules", "category_groups.services"): _str_list,
    ("rules", "category_groups.education"): _str_list,
    ("rules", "services_go_manual.value"): _is_bool,
    ("rules", "unreachable_site_action.value"): _one_of(*ACTIONS_UNREACHABLE),
    ("rules", "owner_mismatch_action.value"): _one_of(*ACTIONS_OWNER),
    ("rules", "sitemap_missing_action.value"): _one_of("EDIT", "MANUAL"),
    ("rules", "checks.agreement.enabled"): _is_bool,
    ("rules", "checks.add_to_cart.enabled"): _is_bool,
    ("rules", "checks.enamad_on_site.enabled"): _is_bool,
    ("rules", "runtime.concurrency"): _int_range(1, 16),
    ("rules", "runtime.enamad_concurrency"): _int_range(1, 4),
    ("rules", "runtime.http_timeout_seconds"): _int_range(5, 120),
    ("rules", "runtime.request_deadline_seconds"): _int_range(30, 900),
    ("rules", "backlog.batch_size"): _int_range(10, 2000),
    ("rules", "backlog.include_optional"): _is_bool,
    ("rules", "approved_statuses.nbo"): _str_list,
    ("rules", "approved_statuses.crm"): _str_list,
    ("category_map", "mismatch_allowed"): _is_bool,
}


def settings_file() -> Path:
    return user_dir() / "settings.json"


def _shipped(name: str) -> dict:
    return json.loads((config_dir() / f"{name}.json").read_text(encoding="utf-8"))


def _get(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


def _set(d: dict, dotted: str, value):
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def sanitize(user: dict):
    """-> (clean overrides {file: {dotted: value}}, rejected [dotted paths]). Unknown keys and wrong types are dropped."""
    clean, rejected = {}, []
    if not isinstance(user, dict):
        return clean, ["<root>"]
    for file, values in user.items():
        if file in ("version", "_note"):
            continue
        if not isinstance(values, dict):
            rejected.append(str(file))
            continue
        for dotted, value in values.items():
            check = EDITABLE.get((file, dotted))
            if check and check(value):
                clean.setdefault(file, {})[dotted] = value
            else:
                rejected.append(f"{file}:{dotted}")
    return clean, rejected


def load_user() -> dict:
    """This person's overrides, already filtered. A broken file is ignored (defaults are used), never fatal."""
    try:
        raw = json.loads(settings_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return sanitize(raw)[0]


def save_user(overrides: dict) -> list:
    """Atomic write. Returns the rejected paths (if any) so the caller can say what was not saved."""
    clean, rejected = sanitize(overrides)
    data = {"version": 1, **clean}
    target = settings_file()
    fd, tmp = tempfile.mkstemp(prefix="settings-", suffix=".json", dir=str(target.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target)
    return rejected


def merged(name: str, overrides: dict = None) -> dict:
    base = copy.deepcopy(_shipped(name))
    for dotted, value in (load_user() if overrides is None else overrides).get(name, {}).items():
        _set(base, dotted, copy.deepcopy(value))
    return base


def load_rules(overrides: dict = None) -> dict:
    return merged("rules", overrides)


def load_category_map(overrides: dict = None) -> dict:
    return merged("category_map", overrides)


def current_value(file: str, dotted: str, overrides: dict = None):
    """The effective value of one editable setting (personal override, else shipped default)."""
    over = (load_user() if overrides is None else overrides).get(file, {})
    if dotted in over:
        return over[dotted]
    try:
        return _get(_shipped(file), dotted)
    except KeyError:
        return None


def export_to(path) -> None:
    Path(path).write_text(json.dumps({"version": 1, **load_user()}, ensure_ascii=False, indent=2), encoding="utf-8")


def import_from(path) -> list:
    """Replaces this person's overrides with the file's (filtered). Returns rejected paths."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    clean, rejected = sanitize(raw)
    save_user(clean)
    return rejected


def reset() -> None:
    try:
        settings_file().unlink()
    except FileNotFoundError:
        pass
