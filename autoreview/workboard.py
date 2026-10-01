"""'How much is done, how much is left' for the day's backlog, and where every request stands.

A request's state is its LATEST decision (any run): not reviewed yet / APPROVE / EDIT / CANCEL waiting to be applied /
MANUAL waiting for a person (or handled, once someone ticks it done)."""
STATES = ("NOT_REVIEWED", "APPROVE", "EDIT", "CANCEL", "MANUAL_OPEN", "MANUAL_DONE")
STATE_FA = {"NOT_REVIEWED": "بررسی‌نشده", "APPROVE": "تایید (در انتظار اعمال)", "EDIT": "اصلاح (در انتظار اعمال)",
            "CANCEL": "لغو (در انتظار اعمال)", "MANUAL_OPEN": "دستی - باز", "MANUAL_DONE": "دستی - انجام‌شده"}


def state_of(smr, latest: dict, manual_done: set) -> str:
    action = latest.get(smr)
    if action is None:
        return "NOT_REVIEWED"
    if action == "MANUAL":
        return "MANUAL_DONE" if smr in manual_done else "MANUAL_OPEN"
    return action


def summarize(backlog_rows, latest: dict, manual_done: set) -> dict:
    counts = dict.fromkeys(STATES, 0)
    for r in backlog_rows:
        counts[state_of(r["smr"], latest, manual_done)] += 1
    total = len(backlog_rows)
    left = counts["NOT_REVIEWED"]
    return {"total": total, "reviewed": total - left, "left": left, "counts": counts,
            "percent": round(100 * (total - left) / total, 1) if total else 0.0,
            "needs_person": counts["MANUAL_OPEN"]}
