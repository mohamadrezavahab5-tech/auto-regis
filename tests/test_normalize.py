from autoreview.normalize import names_equal, normalize_site, normalize_text


def test_site_normalisation():
    assert normalize_site("https://www.Shop.ir/products?a=1#x") == "shop.ir"
    assert normalize_site("http://shop.ir:8080/") == "shop.ir"
    assert normalize_site("  shop.ir ") == "shop.ir"
    assert normalize_site(None) == ""


def test_text_unifies_arabic_letters_and_zwnj():
    assert normalize_text("علي\u200cك") == normalize_text("علی ک")


def test_names_equal_is_order_insensitive_but_never_fuzzy():
    assert names_equal("محمد رضا", "رضا محمد") is True
    assert names_equal("محمد رضا", "محمد رضایی") is False   # similar is NOT equal
    assert names_equal("", "علی") is None                    # missing name = unknown
