from autoreview import settings


def test_mojibake_string_list_override_is_rejected():
    clean, rejected = settings.sanitize({
        "rules": {
            "category_groups.gold": ["Ø·Ù„Ø§"],
            "crm_store_types": ["Ø¢Ù†Ù„Ø§ÛŒÙ†"],
            "approved_statuses.nbo": ["COMMERCIAL_APPROVED"],
        }
    })
    assert "category_groups.gold" not in clean.get("rules", {})
    assert "crm_store_types" not in clean.get("rules", {})
    assert clean["rules"]["approved_statuses.nbo"] == ["COMMERCIAL_APPROVED"]
    assert "rules:category_groups.gold" in rejected
    assert "rules:crm_store_types" in rejected


def test_valid_persian_string_list_override_is_kept():
    clean, rejected = settings.sanitize({
        "rules": {
            "category_groups.gold": ["طلا"],
            "crm_store_types": ["آنلاین", "آنلاین - آفلاین"],
        }
    })
    assert clean["rules"]["category_groups.gold"] == ["طلا"]
    assert clean["rules"]["crm_store_types"] == ["آنلاین", "آنلاین - آفلاین"]
    assert not rejected
