from autoreview.normalize import names_equal, normalize_site, normalize_text


def test_site_normalisation():
    assert normalize_site("https://www.Shop.ir/products?a=1#x") == "shop.ir"
    assert normalize_site("http://shop.ir:8080/") == "shop.ir"
    assert normalize_site("  shop.ir ") == "shop.ir"
    assert normalize_site(None) == ""
    assert normalize_site("https://shop.ir.") == "shop.ir"
    assert normalize_site("http:/shop.ir") == "shop.ir"                 # one-slash typo
    assert normalize_site("https://www2.shop.ir/x") == "shop.ir"
    assert normalize_site("shop.ir‏") == "shop.ir"                 # direction mark pasted with the URL


def test_text_unifies_arabic_letters_zwnj_madda_and_diacritics():
    assert normalize_text("علي‌ك") == normalize_text("علی ک")
    assert normalize_text("آرش") == normalize_text("ارش")
    assert normalize_text("مُحَمَّد") == normalize_text("محمد")


def test_names_equal_is_order_insensitive_but_never_fuzzy():
    assert names_equal("محمد رضا", "رضا محمد") is True
    assert names_equal("محمد رضا", "محمد رضایی") is False   # similar is NOT equal
    assert names_equal("", "علی") is None                    # missing name = unknown


def test_names_tolerate_zwnj_and_treat_partial_names_as_unknown():
    assert names_equal("فریبا ر‌ا‌عی‌", "فریبا راعی") is True
    assert names_equal("نیلوفر مهر‌ابی‌ دلجو", "نیلوفر مهرابی دلجو") is True
    assert names_equal("بهزاد گرجی", "بهزاد گرجی ازندریانی") is None
    assert names_equal("بهزاد گرجی", "علی احمدی") is False


def test_compound_names_written_joined_or_apart_are_the_same_person():
    assert names_equal("محمد رضا احمدی", "احمدی محمدرضا") is True
    assert names_equal("علیرضا حسین زاده", "علی رضا حسین‌زاده") is True
    assert names_equal("امیرحسین کریمی", "امیر حسین کریمی") is True


def test_letter_variants_and_titles_do_not_create_a_mismatch():
    assert names_equal("آرش رئیسی", "ارش رییسی") is True
    assert names_equal("آقای علی رضایی", "علی رضایی") is True
    assert names_equal("علي رضائي", "علی رضایی") is True


def test_spelling_variants_and_one_typo_are_unknown_never_a_mismatch():
    assert names_equal("زهرا رحمن زاده", "زهرا رحمان زاده") is None      # seen live 2026-10-01 (was a wrong EDIT)
    assert names_equal("اسمعیل کریمی", "اسماعیل کریمی") is None
    assert names_equal("نگار حاجیانی", "نگار حاجانی") is None             # one letter
    assert names_equal("زهرا رحمانی", "مریم رحمانی") is False             # a different first name is still a mismatch
    assert names_equal("علی احمدی", "علی محمدی") is False


def test_different_scripts_are_unknown_not_a_mismatch():
    assert names_equal("Ali Rezaei", "علی رضایی") is None
    assert names_equal("Ali Rezaei", "rezaei ali") is True
