"""Persian wording for what the engine writes (the engine itself keeps stable English notes in the database).

Reason labels are NBO's OWN text (config/nbo_reasons.json, read from NBO's front-end); the short descriptions below are only
a fallback for a code NBO does not list."""
import json
import re

from .jalali import fa_digits
from .paths import config_dir

ACTION_FA = {"APPROVE": "تایید", "EDIT": "نیاز به اصلاح", "CANCEL": "لغو", "MANUAL": "بررسی دستی"}


def _nbo_labels() -> dict:
    try:
        data = json.loads((config_dir() / "nbo_reasons.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    labels = dict(data.get("cancel", {}))
    labels.update(data.get("edit", {}))                  # a code in both lists has the same text; edit wording wins
    return labels


_FALLBACK = {
    "MISSING_LICENSE": "نداشتن مجوز رسمی (اینماد)",
    "INVALID_URL": "آدرس سایت نامعتبر است",
    "ENAMAD_EXPIRED": "اینماد منقضی شده است",
    "REGISTRANT_NAME_AND_BANK_ACCOUNT_OWNER_MISMATCH": "نام ثبت‌کننده با صاحب حساب بانکی یکی نیست",
    "OWNER_MISMATCH": "نام صاحب اینماد با صاحب حساب یکی نیست",
    "ENAMAD_OWNER_NAME_MISMATCH": "نام صاحب اینماد مغایرت دارد",
    "MISSING_CONTACT_INFO": "اطلاعات تماس روی سایت نیست",
    "PRODUCT_CANNOT_BE_ADDED_TO_CART": "محصول به سبد خرید اضافه نمی‌شود",
    "SITEMAP_IS_MISSING": "نقشه‌ی سایت (sitemap) وجود ندارد",
    "INSUFFICIENT_NUMBER_OF_PRODUCTS_IN_SITEMAP": "تعداد محصول در نقشه‌ی سایت کافی نیست",
    "ENAMAD_CATEGORY_MISMATCH": "دسته‌بندی اینماد با دسته‌ی اعلام‌شده مغایرت دارد",
    "WEBSITE_IS_INACTIVE": "سایت غیرفعال است",
    "DUPLICATE_REQUEST": "درخواست تکراری",
}
REASON_FA = {**_FALLBACK, **_nbo_labels()}

_UNKNOWN = {
    "is_online": "آنلاین بودن درخواست",
    "website_reachable": "باز شدن سایت (پاسخ نامشخص بود)",
    "website unreachable": "سایت باز نشد",
    "enamad availability": "وجود اینماد (پاسخ اینماد نامشخص بود)",
    "enamad expiry": "اعتبار اینماد",
    "enamad owner vs account holder": "تطبیق نام صاحب اینماد با صاحب حساب",
    "registrant vs account holder": "تطبیق نام ثبت‌کننده با صاحب حساب",
    "enamad category mapping": "تطبیق دسته‌ی اینماد با دسته‌ی NBO (نتیجه‌ی قدیمی؛ علتش ثبت نشده — دوباره بررسی کن)",
    "enamad displayed on site": "نمایش نماد اینماد روی سایت",
    "agreement": "قرارداد (Agreement)",
    "add to cart": "امکان افزودن به سبد خرید",
    "product count": "تعداد محصول",
    "contact info": "اطلاعات تماس",
}

# the real cause, per case (results from before 2026-10-02 carry only the old fixed sentence above)
_CATEGORY_WHY = {
    "no_answer": "سایت اینماد همان لحظه جواب نداد (یعنی نه «ندارد» نه «منقضی»؛ دوباره بررسی کن)",
    "suspended": "اینماد این سایت تعلیق شده است",
    "profile_unreadable": "صفحه‌ی مشخصات اینماد همان لحظه باز نشد؛ عنوان فعالیت‌ها خوانده نشد",
    "no_enamad": "برای این سایت اینماد پیدا نشد",
    "status": "وضعیت اینماد «{}» است",
    "other_domain": "صفحه‌ی اینماد مال دامنه‌ی دیگری است ({})",
    "no_activities": "اینماد هیچ عنوان فعالیتی ندارد",
    "no_nbo_category": "دسته‌ی درخواست در NBO خالی است",
    "not_mapped": "عنوان فعالیت اینماد در جدول نگاشت نیست",
    "no_keyword": "عنوان فعالیت اینماد نه در جدول نگاشت است نه با کلیدواژه‌های قوانین Action Test 4 جور شد",
}

_BLOCKERS = [
    (r"no usable website address in the request", "آدرس سایت معتبری در درخواست نیست"),
    (r"the website is a page on a shared platform \((.*)\), not a shop website", r"آدرس، صفحه‌ای در یک پلتفرم عمومی است (\1)، نه سایت فروشگاه"),
    (r"the website redirects to another domain \((.*) -> (.*)\)", r"سایت به دامنه‌ی دیگری می‌رود (\1 ← \2)"),
    (r"the website answers with a bot-protection page; its content could not be read", "سایت صفحه‌ی ضدربات نشان می‌دهد؛ محتوایش خوانده نشد"),
    (r"the website shows a placeholder / suspended / under-construction page", "سایت صفحه‌ی موقت / مسدود / در دست ساخت نشان می‌دهد"),
]

_FIXED = [
    (r"all gates passed", "همه‌ی بررسی‌ها قبول شد"),
    (r"(\w+): NBO reason code not unambiguous - not acting", r"کد دلیل NBO برای «\1» قطعی نیست؛ اقدامی انجام نشد"),
    (r"enamad owner differs from the account holder - owner_mismatch_action is MANUAL", "نام صاحب اینماد با صاحب حساب فرق دارد (طبق تنظیم: بررسی دستی)"),
    (r"category group 'gold' needs documents - human review", "دسته‌ی طلا مدرک لازم دارد؛ بررسی دستی"),
    (r"category group 'special' needs documents - human review", "دسته‌ی خاص مدرک لازم دارد؛ بررسی دستی"),
    (r"category group '(\w+)' needs documents - human review", r"گروه «\1» مدرک لازم دارد؛ بررسی دستی"),
    (r"services go to manual review by rule", "طبق تنظیم، دسته‌ی خدمات دستی بررسی می‌شود"),
    (r"no category in NBO - manual review by rule", "طبق تنظیم، درخواستِ بدون دسته دستی بررسی می‌شود"),
    (r"agreement check failed - rule and reason not defined yet", "بررسی قرارداد رد شد؛ قاعده‌اش هنوز تعریف نشده"),
    (r"no sitemap found - sitemap_missing_action is MANUAL", "نقشه‌ی سایت پیدا نشد (طبق تنظیم: بررسی دستی)"),
    (r"the same website is already approved(.*)", r"همین سایت قبلاً تایید شده است\1"),
    (r"duplicate found but the NBO cancel reason is not unambiguous(.*)", r"تکراری است ولی کد دلیل لغو قطعی نیست\1"),
    (r"another pending request has the same website \((.*)\)", r"درخواست در انتظارِ دیگری با همین سایت هست (\1)"),
    (r"a request with the same website was just decided in NBO, its outcome is not loaded yet \((.*)\)",
     r"درخواست دیگری با همین سایت تازه در NBO تعیین تکلیف شده و نتیجه‌اش هنوز دریافت نشده (\1)"),
    (r"timed out: the checks took longer than (\d+) s", r"بررسی بیش از \1 ثانیه طول کشید و متوقف شد"),
    (r"internal error: (.*)", r"خطای داخلی: \1"),
]


def note_fa(note: str) -> str:
    n = str(note or "")
    if n.startswith("could not determine: "):
        what = n[len("could not determine: "):]
        m = re.match(r"product count \(at least (\d+), sitemap only partly readable\)", what)
        if m:
            return f"نامشخص: تعداد محصول (دست‌کم {fa_digits(m.group(1))}؛ نقشه‌ی سایت کامل خوانده نشد)"
        m = re.fullmatch(r"enamad (category mapping|availability) \[(\w+)(?::(.*))?\]", what)
        if m:
            head = "تطبیق دسته‌ی اینماد با دسته‌ی NBO" if m.group(1) == "category mapping" else "اینماد"
            return f"نامشخص: {head} — " + _CATEGORY_WHY.get(m.group(2), m.group(2)).format(m.group(3) or "")
        return "نامشخص: " + _UNKNOWN.get(what, what)
    if n.startswith("blocked: "):
        what = n[len("blocked: "):]
        for pat, rep in _BLOCKERS:
            if re.fullmatch(pat, what):
                return "مانع: " + re.sub(pat, rep, what)
        return "مانع: " + what
    m = re.fullmatch(r"timed out: the checks took longer than (\d+) s", n)
    if m:
        return f"بررسی بیش از {fa_digits(m.group(1))} ثانیه طول کشید و متوقف شد"
    for pat, rep in _FIXED:
        if re.fullmatch(pat, n):
            return re.sub(pat, rep, n)
    return n


def notes_fa(notes) -> str:
    return " | ".join(note_fa(n) for n in (notes or []))


def reasons_fa(codes) -> str:
    return "، ".join(REASON_FA.get(c, c) for c in (codes or []))


CAUSE_FA = {
    "could not determine: product count": "تعداد محصول نامشخص",
    "blocked: redirect to another domain": "انتقال به دامنه‌ی دیگر",
    "blocked: social-media page": "صفحه‌ی شبکه‌ی اجتماعی",
    "internal error": "خطای داخلی",
    "another pending request for the same website": "درخواست دیگر با همین سایت",
}


def cause_fa(key: str) -> str:
    return CAUSE_FA.get(key) or note_fa(key)


# ---- the rule trace in plain Persian (owner 2026-10-03: "click a request and it must say what was checked and why") ----
_STEP_PASS = {
    "is_online": "درخواست آنلاین است",
    "website opens": "سایت باز می‌شود",
    "enamad exists and is valid": "اینماد دارد و معتبر است",
    "names": "نام‌ها با هم می‌خوانند (ثبت‌کننده، صاحب حساب، صاحب اینماد)",
    "contact": "اطلاعات تماس روی سایت هست",
    "add to cart": "افزودن به سبد خرید کار می‌کند",
    "category": "دسته‌ی اینماد با دسته‌ی NBO می‌خواند",
}
_STEP_FAIL = {
    "TOO_FEW_PRODUCTS": "تعداد محصول کمتر از حداقل است",
    "MISSING_ENAMAD": "اینماد ندارد",
    "ENAMAD_EXPIRED": "اینماد منقضی شده",
    "DUPLICATE_REQUEST": "همین سایت قبلاً تأیید شده (درخواست تکراری)",
    "OWNER_MISMATCH": "صاحب اینماد با صاحب حساب یکی نیست",
    "NAME_MISMATCH_BANK": "نام ثبت‌کننده با صاحب حساب بانکی یکی نیست",
    "SITEMAP_MISSING": "نقشه‌ی سایت ندارد",
    "BAD_OR_DEAD_URL": "آدرس سایت اشتباه است یا باز نمی‌شود",
    "SITE_INACTIVE": "سایت غیرفعال یا در دست ساخت است",
    "NO_CONTACT": "اطلاعات تماس روی سایت نیست",
    "NO_ADD_TO_CART": "افزودن به سبد خرید کار نمی‌کند",
    "MISSING_LICENSE": "مجوز لازم را ندارد",
    "CATEGORY_MISMATCH": "دسته‌ی اینماد با دسته‌ی NBO نمی‌خواند",
}
_STEP_ACTION = {"EDIT": "نیاز به اصلاح", "CANCEL": "لغو", "APPROVE": "تأیید", "MANUAL": "بررسی دستی"}


def step_fa(step: str):
    """One line of a review's rule trace -> (kind, Persian sentence). kind: PASS / FAIL / UNKNOWN / BLOCKED / other."""
    kind, _, rest = str(step or "").partition(" ")
    rest = rest.strip()
    if kind == "PASS":
        m = re.fullmatch(r"products (\d+) >= (\d+)", rest)
        if m:
            return kind, f"تعداد محصول کافی است: {m.group(1)} (حداقل {m.group(2)})"
        return kind, _STEP_PASS.get(rest, rest)
    if kind == "FAIL":
        code, _, action = rest.partition(" -> ")
        text = _STEP_FAIL.get(code.strip()) or REASON_FA.get(code.strip(), code.strip())
        return kind, text + (f" ← نتیجه: {_STEP_ACTION.get(action.strip(), action.strip())}" if action else "")
    if kind == "UNKNOWN":
        return kind, note_fa("could not determine: " + rest) + " ← بررسی دستی"
    if kind == "BLOCKED":
        short = {"the website is a page on a shared platform": "مانع: سایت یک صفحه در پلتفرم مشترک است (اینستاگرام، باسلام و …)",
                 "the website redirects to another domain": "مانع: سایت به دامنه‌ی دیگری منتقل می‌شود"}
        return kind, (short.get(rest) or note_fa("blocked: " + rest)) + " ← بررسی دستی"
    if kind == "PENDING_SIBLING":
        return "UNKNOWN", "درخواست باز دیگری برای همین سایت هست ← بررسی دستی"
    if kind == "TIMEOUT":
        return "UNKNOWN", "بررسی بیش از حد طول کشید و متوقف شد ← بررسی دستی"
    if kind == "ERROR":
        return "UNKNOWN", "خطای داخلی هنگام بررسی؛ دوباره بررسی می‌شود"
    return kind, rest or str(step)
