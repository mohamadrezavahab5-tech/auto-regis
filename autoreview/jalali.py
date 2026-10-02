"""Gregorian -> Jalali (Solar Hijri) dates and Persian formatting. Pure functions.

Display only: everything stored keeps ISO/UTC timestamps; this module never changes a stored value."""
from datetime import datetime

MONTHS = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")
WEEKDAYS = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")      # index = date.weekday()
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def g2j(gy: int, gm: int, gd: int):
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def j2g(jy: int, jm: int, jd: int):
    """Jalali -> Gregorian (the inverse of g2j; same arithmetic as the widely used jalaali algorithm)."""
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186)
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    for gm, length in enumerate((31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31), 1):
        if gd <= length:
            return gy, gm, gd
        gd -= length
    return gy, 12, 31


def parse_jdate(text):
    """'۱۴۰۵/۰۷/۰۹' or '1405-7-9' -> datetime.date, or None when it is not a Jalali date."""
    import re
    from datetime import date
    m = re.match(r"^\s*(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(text or "").translate(_LATIN))
    if not m:
        return None
    jy, jm, jd = (int(x) for x in m.groups())
    if not (1 <= jm <= 12 and 1 <= jd <= 31):
        return None
    try:
        return date(*j2g(jy, jm, jd))
    except ValueError:
        return None


# Owner 2026-10-02: "where Persian is not needed, English" - numbers are shown with English digits (cleaner in tables and
# big numbers, and the Persian zero '۰' read like a bullet). One switch for the whole app: 'latin' or 'persian'.
DIGITS = "latin"


def fa_digits(value) -> str:
    """Digits for display (the name is historical): English digits by default, Persian when DIGITS = 'persian'."""
    s = str(value)
    return s.translate(_FA_DIGITS) if DIGITS == "persian" else s.translate(_LATIN)


def _local(dt) -> datetime:
    """Any datetime / ISO string / date -> local aware datetime. A naive value is taken as local time; an aware one
    (the database stores UTC) is converted."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if isinstance(dt, datetime):
        return dt.astimezone()
    return datetime(dt.year, dt.month, dt.day).astimezone()


def jdate(dt=None, persian_digits=True) -> str:
    """'1405/07/09' (in Persian digits by default)."""
    d = _local(dt or datetime.now())
    jy, jm, jd = g2j(d.year, d.month, d.day)
    s = f"{jy:04d}/{jm:02d}/{jd:02d}"
    return fa_digits(s) if persian_digits else s


def long_date(dt=None) -> str:
    """'چهارشنبه ۹ مهر ۱۴۰۵'"""
    d = _local(dt or datetime.now())
    jy, jm, jd = g2j(d.year, d.month, d.day)
    return f"{WEEKDAYS[d.weekday()]} {fa_digits(jd)} {MONTHS[jm - 1]} {fa_digits(jy)}"


def jdatetime(dt, persian_digits=True) -> str:
    """'1405/07/09 14:05' in local time."""
    d = _local(dt)
    s = f"{jdate(d, False)} {d:%H:%M}"
    return fa_digits(s) if persian_digits else s


def ago(dt, now=None) -> str:
    """Relative age in Persian: 'همین حالا' / '۵ دقیقه پیش' / '۳ ساعت پیش' / 'دیروز' / '۴ روز پیش'."""
    if not dt:
        return "—"
    d = _local(dt)
    now = (now or datetime.now()).astimezone()
    secs = (now - d).total_seconds()
    if secs < 60:
        return "همین حالا"
    if secs < 3600:
        return f"{fa_digits(int(secs // 60))} دقیقه پیش"
    if secs < 86400 and now.date() == d.date():
        return f"{fa_digits(int(secs // 3600))} ساعت پیش"
    days = (now.date() - d.date()).days
    return "دیروز" if days == 1 else f"{fa_digits(days)} روز پیش"


def ago_en(dt, now=None) -> str:
    """Short relative age for status chips: 'just now' / '5m ago' / '3h ago' / '2d ago'."""
    if not dt:
        return "—"
    secs = ((now or datetime.now()).astimezone() - _local(dt)).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"
