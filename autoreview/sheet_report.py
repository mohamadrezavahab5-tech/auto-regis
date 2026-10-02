"""The 'گزارش' tab of the owner's sheet (owner 2026-10-02: "a report page in the sheet, with numbers and charts").

Every number comes from the app's own data - the same the app shows - and follows its logic: "needs action now" is the
workflow state of the open requests; a decision is one engine review (a request reviewed twice counts twice), and an
internal error is not a decision. The layout is fixed, so the charts the sheet draws once keep reading the right cells."""
import json
from datetime import datetime, timedelta, timezone

from . import jalali, workflow
from .texts import REASON_FA, cause_fa
from .store import cause_key

ACTIONS = ("APPROVE", "EDIT", "CANCEL", "MANUAL")
NOW_TILES = ((("WAIT_INSTORE",), "نوبت تیم Instore"), (("MANUAL",), "بررسی دستی تیم Online"),
             (("READY", "EDIT", "CANCEL"), "آماده‌ی اعمال در NBO"), (("CONFLICT",), "اختلاف دو تیم"),
             (("WAIT_ONLINE",), "منتظر بررسی موتور"))
OPEN_ORDER = ("WAIT_INSTORE", "CONFLICT", "MANUAL", "READY", "EDIT", "CANCEL", "WAIT_ONLINE")
DAYS = 14
TOP = 8
_NOT_ERROR = "COALESCE(trace, '') NOT LIKE '%\"ERROR\"%'"

# fixed layout (0-based rows); the charts in google_sheet.report_charts read these blocks
ROW = dict(title=0, updated=1, now_head=3, now_labels=4, now_values=5, perf_head=7, perf_cols=8, perf_rows=9,
           states_head=13, states_cols=14, states_rows=15, daily_head=23, daily_cols=24, daily_rows=25,
           reasons_head=40, reasons_cols=41, reasons_rows=42, manual_head=51, manual_cols=52, manual_rows=53, note=62)
WIDTH = 7


def _local_day(iso):
    return datetime.fromisoformat(iso).astimezone().date()


def report(db, legal_pending=None, now=None):
    """-> the numbers of the report (see grid() for how they are laid out)."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone().date()
    cases = [c for c in workflow.cases(db) if c.get("active")]
    states = {}
    for c in cases:
        st = workflow.state(c)
        states[st] = states.get(st, 0) + 1

    since = (now - timedelta(days=31)).isoformat(timespec="seconds")
    rows = db.execute(f"SELECT action, decided_at, reason_codes, notes FROM results WHERE decided_at >= ? AND {_NOT_ERROR}",
                      (since,)).fetchall()
    periods = {"today": dict.fromkeys(ACTIONS, 0), "7": dict.fromkeys(ACTIONS, 0), "30": dict.fromkeys(ACTIONS, 0)}
    daily = {today - timedelta(days=i): dict.fromkeys(ACTIONS, 0) for i in range(DAYS)}
    reasons, causes = {}, {}
    for action, decided, codes, notes in rows:
        day = _local_day(decided)
        age = (today - day).days
        if action not in ACTIONS:
            continue
        if age == 0:
            periods["today"][action] += 1
        if age < 7:
            periods["7"][action] += 1
        if age < 30:
            periods["30"][action] += 1
            if action in ("EDIT", "CANCEL"):
                for code in json.loads(codes or "[]"):
                    reasons[code] = reasons.get(code, 0) + 1
            if action == "MANUAL":
                key = cause_key((json.loads(notes or "[]") or ["?"])[0])
                causes[key] = causes.get(key, 0) + 1
        if day in daily:
            daily[day][action] += 1

    applied = {"today": 0, "7": 0, "30": 0}
    for (updated,) in db.execute("SELECT updated_at FROM nbo_execution WHERE state = 'SENT' AND updated_at >= ?", (since,)):
        age = (today - _local_day(updated)).days
        applied["today"] += age == 0
        applied["7"] += age < 7
        applied["30"] += age < 30

    return {
        "updated": jalali.jdatetime(now, False),
        "now": [(label, sum(states.get(s, 0) for s in group)) for group, label in NOW_TILES]
               + [("Legal بررسی‌نشده", legal_pending if legal_pending is not None else "")],
        "periods": [(label, periods[k], applied[k]) for k, label in (("today", "امروز"), ("7", "۷ روز اخیر"), ("30", "۳۰ روز اخیر"))],
        "states": [(workflow.STATES[s], states.get(s, 0)) for s in OPEN_ORDER],
        "daily": [(jalali.jdate(day, False)[5:], *(daily[day][a] for a in ACTIONS)) for day in sorted(daily)],
        "reasons": [(REASON_FA.get(code, code), n) for code, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:TOP]],
        "causes": [(cause_fa(key), n) for key, n in sorted(causes.items(), key=lambda kv: -kv[1])[:TOP]],
    }


def grid(data):
    """-> rows of cell values (WIDTH columns, ROW['note'] + 1 rows) for the report tab."""
    out = [[""] * WIDTH for _ in range(ROW["note"] + 1)]

    def put(r, values):
        out[r][:len(values)] = list(values)

    put(ROW["title"], ["گزارش AutoReview"])
    put(ROW["updated"], ["آخرین به‌روزرسانی: " + data["updated"]])
    put(ROW["now_head"], ["الان نیاز به اقدام"])
    put(ROW["now_labels"], [label for label, _ in data["now"]])
    put(ROW["now_values"], [value for _, value in data["now"]])
    put(ROW["perf_head"], ["تصمیم‌های موتور"])
    put(ROW["perf_cols"], ["دوره", "همه", "تأیید", "نیاز به اصلاح", "لغو", "بررسی دستی", "اعمال‌شده در NBO"])
    for i, (label, counts, applied) in enumerate(data["periods"]):
        put(ROW["perf_rows"] + i, [label, sum(counts.values()), *(counts[a] for a in ACTIONS), applied])
    put(ROW["states_head"], ["درخواست‌های باز بر اساس وضعیت"])
    put(ROW["states_cols"], ["وضعیت", "تعداد"])
    for i, (label, n) in enumerate(data["states"]):
        put(ROW["states_rows"] + i, [label, n])
    put(ROW["daily_head"], [f"تصمیم‌های روزانه‌ی موتور ({DAYS} روز)"])
    put(ROW["daily_cols"], ["روز", "تأیید", "نیاز به اصلاح", "لغو", "بررسی دستی"])
    for i, values in enumerate(data["daily"]):
        put(ROW["daily_rows"] + i, values)
    put(ROW["reasons_head"], ["پرتکرارترین دلیل‌های اصلاح و لغو (۳۰ روز)"])
    put(ROW["reasons_cols"], ["دلیل", "تعداد"])
    for i, values in enumerate(data["reasons"]):
        put(ROW["reasons_rows"] + i, values)
    put(ROW["manual_head"], ["علت‌های بررسی دستی (۳۰ روز)"])
    put(ROW["manual_cols"], ["علت", "تعداد"])
    for i, values in enumerate(data["causes"]):
        put(ROW["manual_rows"] + i, values)
    put(ROW["note"], ["عددها از داده‌ی خود اپ است: هر بررسیِ موتور یک تصمیم است و «خطای داخلی» شمرده نمی‌شود؛ "
                      "«الان نیاز به اقدام» وضعیت درخواست‌های باز در گردش کار است."])
    return out
