"""Read an NBO / CRM export (xlsx or csv) into normalised dict rows.

NBO: every column is found by its EXACT header name (config/columns.json, verified on the real export 2026-10-01), so a
     reordered export still works and a renamed or missing column stops the import with a clear message - it is never
     silently read from the wrong column.
CRM: the manual CRM export is only a fallback (the app reads CRM directly); its columns are recognised by their content:
     request codes 'MRG-...', CRM status labels, and website addresses."""
import csv
import json
import re
from pathlib import Path

from .duplicates import site_key
from .normalize import normalize_text
from .paths import config_dir


class ImportLayoutError(ValueError):
    """The file is not shaped like the expected export (missing / renamed columns, wrong file)."""


def load_columns(path=None) -> dict:
    with open(path or config_dir() / "columns.json", encoding="utf-8") as f:
        return json.load(f)


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def read_table(file):
    """-> list of rows (lists of strings) from the first sheet of an .xlsx, or from a .csv (UTF-8, with or without BOM)."""
    p = Path(file)
    if not p.exists():
        raise ImportLayoutError(f"فایل پیدا نشد: {p}")
    if p.suffix.lower() == ".csv":
        with open(p, newline="", encoding="utf-8-sig") as f:
            return [[_cell(c) for c in r] for r in csv.reader(f)]
    if p.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ImportLayoutError(f"{p.name}: فقط فایل xlsx یا csv پشتیبانی می‌شود")
    import openpyxl
    try:
        wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    except Exception as e:
        raise ImportLayoutError(f"{p.name}: فایل Excel خوانده نشد ({type(e).__name__})") from e
    try:
        ws = wb.worksheets[0]
        return [[_cell(c) for c in r] for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _hkey(h) -> str:
    return re.sub(r"\s+", " ", str(h or "")).strip().lower()


def read_nbo_table(table, headers: dict = None) -> list:
    spec = headers or load_columns()["nbo_headers"]
    required, optional = spec["required"], spec.get("optional", {})
    head_row = None
    for i, r in enumerate(table[:10]):                              # the header is the first row; tolerate a title row above it
        keys = {_hkey(c) for c in r}
        if _hkey(required["smr"]) in keys:
            head_row = i
            break
    if head_row is None:
        raise ImportLayoutError("این فایل خروجی NBO نیست: ستون «%s» پیدا نشد." % required["smr"])
    index = {}
    for j, h in enumerate(table[head_row]):
        index.setdefault(_hkey(h), j)
    missing = [name for name in required.values() if _hkey(name) not in index]
    if missing:
        raise ImportLayoutError("ساختار خروجی NBO عوض شده؛ این ستون‌ها پیدا نشد: " + "، ".join(missing) +
                                ". برای جلوگیری از خواندن ستون اشتباه، فایل وارد نشد.")
    cols = {field: index[_hkey(name)] for field, name in required.items()}
    cols.update({field: index[_hkey(name)] for field, name in optional.items() if _hkey(name) in index})
    rows = []
    for r in table[head_row + 1:]:
        row = {field: (r[j] if j < len(r) else "") for field, j in cols.items()}
        if row.get("smr"):
            rows.append(row)
    return rows


# ---- CRM file: columns recognised by content -------------------------------------------------------------------------
_MRG = re.compile(r"^MRG-\d+$", re.I)


def _status_keys() -> set:
    try:
        labels = json.loads((config_dir() / "crm_api.json").read_text(encoding="utf-8")).get("status_labels", [])
    except (OSError, ValueError):
        labels = []
    return {status_key(x) for x in labels}


def read_crm_table(table) -> list:
    if len(table) < 2:
        raise ImportLayoutError("فایل CRM خالی است.")
    width = max(len(r) for r in table)
    body = [r + [""] * (width - len(r)) for r in table[1:]]
    labels = _status_keys()

    def best(test, exclude=()):
        scores = []
        for j in range(width):
            if j in exclude:
                continue
            vals = [r[j] for r in body if r[j]]
            hits = sum(1 for v in vals if test(v))
            scores.append((hits / max(len(vals), 1) if len(vals) >= 3 or len(body) < 3 else 0, hits, j))
        scores.sort(reverse=True)
        return scores[0] if scores else (0, 0, None)

    smr_ratio, _, smr_col = best(lambda v: bool(_MRG.match(v)))
    if smr_col is None or smr_ratio < 0.6:
        raise ImportLayoutError("در فایل CRM ستونی با کدهای «MRG-...» پیدا نشد. از «دریافت خودکار از CRM» استفاده کن.")
    st_ratio, _, st_col = best(lambda v: status_key(v) in labels, exclude=(smr_col,))
    if st_col is None or st_ratio < 0.6:
        raise ImportLayoutError("در فایل CRM ستون وضعیت درخواست (مثل «درخواست تایید شده است») پیدا نشد.")
    site_ratio, site_hits, site_col = best(lambda v: site_key(v) is not None, exclude=(smr_col, st_col))
    if site_col is None or site_hits == 0:
        raise ImportLayoutError("در فایل CRM ستون آدرس سایت پیدا نشد.")
    return [{"smr": r[smr_col], "status": r[st_col], "site": r[site_col]} for r in body if _MRG.match(r[smr_col] or "")]


def read_export(file, source: str):
    table = read_table(file)
    if source == "nbo":
        return read_nbo_table(table)
    if source == "crm":
        return read_crm_table(table)
    raise ValueError(source)


# ---- statuses ----------------------------------------------------------------------------------------------------------
def status_key(label) -> str:
    """Status labels differ in spacing between systems ('فعالسازی' vs 'فعال سازی'): compare without spaces/ZWNJ."""
    return normalize_text(label).replace(" ", "")


def approved_rows(rows, statuses):
    wanted = {status_key(s) for s in statuses}
    return [r for r in rows if status_key(r.get("status", "")) in wanted]
