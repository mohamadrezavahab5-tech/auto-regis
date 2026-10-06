import json

from autoreview.collectors import enamad
from autoreview.paths import config_dir


def mapping():
    return json.loads((config_dir() / "category_map.json").read_text(encoding="utf-8"))


def test_shipped_category_policy_is_strict():
    cfg = mapping()
    assert cfg["match_mode"] == "strict"
    assert cfg["mismatch_allowed"] is True


def test_strict_category_mapping_matches_confirms_and_keeps_unknown_manual():
    cfg = mapping()
    titles = cfg["titles_by_nbo_category"]
    activity = "فروش پوشاک، کیف، کفش و محصولات چرمی"

    assert enamad.category_relation("مد و پوشاک", [activity], titles, True) == "match"
    assert enamad.category_relation("لوازم برقی خانه", [activity], titles, True) == "mismatch"
    assert enamad.category_relation("مد و پوشاک", ["عنوان جدید و هنوز نگاشت‌نشده"], titles, True) == "unknown"


def test_stale_user_category_policy_overrides_are_ignored(monkeypatch):
    from autoreview import settings
    monkeypatch.setattr(settings, "load_user", lambda: {
        "category_map": {
            "match_mode": "action_test_4",
            "mismatch_allowed": False,
        }
    })
    cfg = settings.load_category_map()
    assert cfg["match_mode"] == "strict"
    assert cfg["mismatch_allowed"] is True
