"""Gregorian -> Jalali (Solar Hijri) dates and Persian formatting. Pure functions.

Display only: everything stored keeps ISO/UTC timestamps; this module never changes a stored value."""
from datetime import datetime

MONTHS = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")
WEEKDAYS = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")      # index = date.weekday()
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


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


def fa_digits(value) -> str:
    return str(value).translate(_FA_DIGITS)


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
