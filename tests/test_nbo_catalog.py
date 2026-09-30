import json
from pathlib import Path

from autoreview import nbo_catalog as nc

# Excerpt in the exact format of NBO's front-end chunk (read 2026-10-01): note \xab \xbb hex escapes and ‌ (ZWNJ).
JS = ('(0,u.Z)(s,c.Xn.INSUFFICIENT_PRODUCT_VARIETY,"\\u062a\\u0646\\u0648\\u0639"),'
      '(0,u.Z)(s,c.Xn.DUPLICATE_REQUEST,"\\u062a\\u06a9\\u0631\\u0627\\u0631\\u06cc \\u0628\\u0648\\u062f\\u0646 \\u062f\\u0631\\u062e\\u0648\\u0627\\u0633\\u062a"),'
      '(0,u.Z)(s,c.Xn.MISSING_TERMS,"\\xab\\u0634\\u0631\\u0627\\u06cc\\u0637\\xbb"),'
      '(0,u.Z)(i,c.Tx.INCOMPLETE_IDS,"\\u0645\\u062f\\u0627\\u0631\\u06a9"),'
      '(0,u.Z)(i,c.Tx.OWNER_MISMATCH,"\\u0645\\u063a\\u0627\\u06cc\\u0631\\u062a \\u062b\\u0628\\u062a\\u200c\\u0646\\u0627\\u0645"),'
      '(0,u.Z)(a,c.mf.PENDING,"Pending")')


def test_parse_and_pick_the_reason_maps():
    maps = nc.parse_label_maps(JS)
    assert maps["a"] == {"PENDING": "Pending"}
    rm = nc.find_reason_maps(maps)
    assert nc.label_for(rm, "CANCEL", "DUPLICATE_REQUEST") == "تکراری بودن درخواست"
    assert nc.label_for(rm, "EDIT", "OWNER_MISMATCH") == "مغایرت ثبت‌نام"      # ZWNJ preserved
    assert rm["cancel"]["MISSING_TERMS"] == "«شرایط»"                        # \xab \xbb decoded
    assert nc.label_for(rm, "EDIT", "NO_SUCH_CODE") is None                            # unknown => the caller refuses
    assert nc.label_for({}, "EDIT", "OWNER_MISMATCH") is None                          # maps not found => refuse


def test_shipped_registry_codes_exist_in_the_real_nbo_lists():
    reasons = json.loads((Path(nc.__file__).resolve().parent.parent / "config" / "reasons.json").read_text(encoding="utf-8"))
    edit = {"MISSING_LICENSE", "INVALID_URL", "ENAMAD_EXPIRED", "REGISTRANT_NAME_AND_BANK_ACCOUNT_OWNER_MISMATCH", "OWNER_MISMATCH",
            "MISSING_CONTACT_INFO", "PRODUCT_CANNOT_BE_ADDED_TO_CART", "SITEMAP_IS_MISSING", "INSUFFICIENT_NUMBER_OF_PRODUCTS_IN_SITEMAP",
            "ENAMAD_CATEGORY_MISMATCH"}                       # all present in NBO's Edit map (verified 2026-10-01)
    cancel = {"WEBSITE_IS_INACTIVE", "DUPLICATE_REQUEST", "ENAMAD_OWNER_NAME_MISMATCH"}      # present in NBO's Cancel map
    for key, v in reasons.items():
        if key.startswith("_") or not v.get("nbo_code"):
            continue
        assert v["nbo_code"] in (edit if v["action"] == "EDIT" else cancel), key
