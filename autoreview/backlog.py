"""Daily backlog: which NBO export rows the engine should work on today, newest first, split into batches."""
import re

_FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def jalali_key(value) -> int:
    """'۱۴۰۵/۰۷/۰۸' -> 14050708 (sortable). Unparseable -> 0 (sorts as oldest, never crashes)."""
    m = re.match(r"^\s*(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(value or "").translate(_FA))
    return int(m.group(1)) * 10000 + int(m.group(2)) * 100 + int(m.group(3)) if m else 0


def select(rows, cfg: dict, include_optional=False):
    """rows: dicts with status, ownership, has_online, has_instore, created_at, smr. Returns (selected, skipped_reasons_count)."""
    statuses = set(cfg["statuses"]) | (set(cfg.get("optional_statuses", [])) if include_optional else set())
    picked, why = [], {}

    def skip(reason):
        why[reason] = why.get(reason, 0) + 1

    for r in rows:
        if r.get("status") not in statuses:
            skip("status"); continue
        if cfg.get("ownership") and r.get("ownership") != cfg["ownership"]:
            skip("ownership"); continue
        if r.get("has_online", "").lower() != "true":
            skip("not online"); continue
        if cfg.get("online_only") and r.get("has_instore", "").lower() == "true":
            skip("also instore (Online-Insta flow)"); continue
        picked.append(r)
    if cfg.get("order") == "newest_first":
        picked.sort(key=lambda r: jalali_key(r.get("created_at")), reverse=True)   # stable: export order kept inside a day
    else:
        picked.sort(key=lambda r: jalali_key(r.get("created_at")))
    return picked, why


def batches(rows, size):
    return [rows[i:i + size] for i in range(0, len(rows), size)]
