"""Results -> the person's OWN Google Sheet, through a small Apps Script web app that lives inside that sheet.

Why this route: no Google Cloud project, no OAuth client, nothing to install. The person pastes one script into their own
sheet (the app shows it with a Copy button), deploys it as a web app and pastes the link back into the app. Only requests
that carry the secret written into that script are accepted, and the script only ever ADDS rows to its own three tabs.
The team's shared sheets are never written from here."""
import json
import re
import secrets
import time

import httpx

from . import logs
from .paths import user_dir
from .texts import ACTION_FA, notes_fa, reasons_fa

log = logs.get("sheet")

APPS_SCRIPT = r"""// AutoReview -> this spreadsheet. It only ADDS rows to its own tabs; nothing else in this file is read or changed.
// Optional: the Online team's 4 columns in the shared Online-Instore sheet (only cells that are still empty, see oiWrite).
// Setup: Extensions > Apps Script > paste all of this > Save
//        Deploy > New deployment > type: Web app > Execute as: Me > Who has access: Anyone > Deploy
//        allow access when Google asks, then copy the "Web app URL" into AutoReview.
const SECRET = '__SECRET__';
const ONLINE_INSTORE_ID = '__OI_ID__';          // the ONLY other spreadsheet this script may write to ('' = never)
const OI_TAB = '__OI_TAB__';
const OI_LOG_TAB = 'AutoReview - ثبت در Online-Instore';
const OI_LOG_HEAD = ['زمان', 'اجرا', 'کاربر', 'کد درخواست', 'ردیف در Online-Instore', 'نتیجه', 'دلیل', 'وضعیت'];
const TABS = { results: 'AutoReview - نتایج', manual: 'AutoReview - صف دستی', runs: 'AutoReview - اجراها' };
const HEAD = ['زمان ثبت', 'اجرا', 'کاربر', 'کد درخواست', 'وب‌سایت', 'دسته‌بندی', 'تاریخ ایجاد', 'تصمیم', 'کد دلیل NBO',
              'شرح دلیل', 'توضیح', 'زمان بررسی (ثانیه)'];
const MANUAL_HEAD = HEAD.concat(['انجام شد؟', 'یادداشت']);
const RUNS_HEAD = ['زمان', 'اجرا', 'کاربر', 'تعداد', 'تایید', 'اصلاح', 'لغو', 'دستی'];

function doPost(e) {
  let body;
  try { body = JSON.parse(e.postData.contents); } catch (err) { return out({ ok: false, error: 'bad_json' }); }
  if (!body || body.secret !== SECRET) return out({ ok: false, error: 'forbidden' });
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  if (body.action === 'ping') return out({ ok: true, sheet: ss.getName(), url: ss.getUrl(), version: 2, online_instore: ONLINE_INSTORE_ID !== '' });
  if (body.action === 'oi_describe' || body.action === 'oi_write') {
    try {
      if (body.action === 'oi_describe') return out(oiDescribe(body));
      const lock = LockService.getScriptLock();
      lock.waitLock(30000);
      try { return out(oiWrite(body, ss)); } finally { lock.releaseLock(); }
    } catch (err) {
      return out({ ok: false, error: String(err.message || err) });
    }
  }
  if (body.action !== 'append') return out({ ok: false, error: 'unknown_action' });
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const runs = tab(ss, TABS.runs, RUNS_HEAD);
    const last = runs.getLastRow();
    if (last > 1) {
      const ids = runs.getRange(2, 2, last - 1, 1).getValues().map(function (r) { return String(r[0]); });
      if (ids.indexOf(String(body.run_id)) >= 0) return out({ ok: true, duplicate: true, appended: 0 });
    }
    const now = new Date();
    const src = body.rows || [];
    const rows = src.map(function (r) {
      return [now, body.run_id, body.user || '', r.smr, r.site, r.category, r.created_at, r.action_fa, r.codes, r.reasons_fa, r.notes_fa, r.seconds];
    });
    write(tab(ss, TABS.results, HEAD), rows);
    const manual = tab(ss, TABS.manual, MANUAL_HEAD);
    const manualRows = rows.filter(function (_, i) { return src[i].action === 'MANUAL'; }).map(function (r) { return r.concat([false, '']); });
    const first = write(manual, manualRows);
    if (first) manual.getRange(first, HEAD.length + 1, manualRows.length, 1).insertCheckboxes();
    const count = function (a) { return src.filter(function (r) { return r.action === a; }).length; };
    write(runs, [[now, body.run_id, body.user || '', rows.length, count('APPROVE'), count('EDIT'), count('CANCEL'), count('MANUAL')]]);
    return out({ ok: true, appended: rows.length });
  } finally {
    lock.releaseLock();
  }
}

function tab(ss, name, head) {
  let sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, head.length).setValues([head]).setFontWeight('bold');
    sh.setFrozenRows(1);
    sh.setRightToLeft(true);
  }
  return sh;
}

function write(sh, rows) {
  if (!rows.length) return 0;
  // text typed by a merchant must never become a formula
  const safe = rows.map(function (r) { return r.map(function (v) { return (typeof v === 'string' && /^[=+\-@]/.test(v)) ? "'" + v : v; }); });
  const first = sh.getLastRow() + 1;
  sh.getRange(first, 1, safe.length, safe[0].length).setValues(safe);
  return first;
}

function out(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}

// ---- shared Online-Instore sheet: the Online team's 4 columns only -------------------------------------------------------
function oiSheet() {
  if (!ONLINE_INSTORE_ID) throw new Error('online_instore_not_configured');
  const sh = SpreadsheetApp.openById(ONLINE_INSTORE_ID).getSheetByName(OI_TAB);
  if (!sh) throw new Error('tab_not_found');
  return sh;
}

function oiLayout(sh) {
  const top = sh.getRange(1, 1, Math.min(5, Math.max(1, sh.getLastRow())), sh.getLastColumn()).getDisplayValues();
  let headRow = -1, dateCol = -1, caseCol = -1;
  for (let r = 0; r < top.length; r++) {
    for (let c = 0; c < top[r].length; c++) {
      const v = String(top[r][c]).replace(/\s+/g, ' ').trim().toLowerCase();
      if (v === 'date online check') { headRow = r; dateCol = c; }
      if (v === 'case id' && caseCol < 0) caseCol = c;
    }
  }
  if (dateCol < 0) throw new Error('online_columns_not_found');
  if (caseCol < 0) throw new Error('case_id_column_not_found');
  const h = top[headRow];
  const names = [h[dateCol], h[dateCol + 1], h[dateCol + 2], h[dateCol + 3]];
  if (names[1].indexOf('بررسی') < 0 || names[2].indexOf('ادیت') < 0 || names[3].indexOf('لغو') < 0) throw new Error('online_columns_unexpected');
  return { headRow: headRow + 1, first: headRow + 2, date: dateCol + 1, result: dateCol + 2, edit: dateCol + 3, cancel: dateCol + 4,
           caseCol: caseCol + 1, names: names };
}

function allowedValues(sh, row, col) {
  const dv = sh.getRange(row, col).getDataValidation();
  if (!dv) return null;
  const t = dv.getCriteriaType();
  if (t === SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST) return dv.getCriteriaValues()[0];
  if (t === SpreadsheetApp.DataValidationCriteria.VALUE_IN_RANGE) {
    return dv.getCriteriaValues()[0].getDisplayValues().map(function (r) { return r[0]; }).filter(String);
  }
  return null;
}

function oiDescribe(body) {
  const marker = String((body && body.marker) || '');
  const sh = oiSheet();
  const L = oiLayout(sh);
  const n = Math.max(0, sh.getLastRow() - L.first + 1);
  const vals = n ? sh.getRange(L.first, 1, n, sh.getLastColumn()).getDisplayValues() : [];
  const distinct = function (col) {
    const m = {};
    vals.forEach(function (r) { const v = r[col - 1]; if (v) m[v] = (m[v] || 0) + 1; });
    return Object.keys(m).sort(function (a, b) { return m[b] - m[a]; }).slice(0, 40);
  };
  // ours to review: the 4 Online cells are empty, or the result is the automation's own marker word (it could not decide before)
  const empty = vals.filter(function (r) {
    const blank = !r[L.date - 1] && !r[L.result - 1] && !r[L.edit - 1] && !r[L.cancel - 1];
    return r[L.caseCol - 1] && (blank || (marker !== '' && r[L.result - 1] === marker));
  }).map(function (r) { return r[L.caseCol - 1]; });
  return { ok: true, layout: L, rows: n, empty_count: empty.length, empty_ids: empty.slice(0, 5000),
           allowed: { result: allowedValues(sh, L.first, L.result), edit: allowedValues(sh, L.first, L.edit), cancel: allowedValues(sh, L.first, L.cancel) },
           seen: { date: distinct(L.date).slice(0, 5), result: distinct(L.result), edit: distinct(L.edit), cancel: distinct(L.cancel) } };
}

function oiWrite(body, ss) {
  const sh = oiSheet();
  const L = oiLayout(sh);
  const n = Math.max(0, sh.getLastRow() - L.first + 1);
  const ids = n ? sh.getRange(L.first, L.caseCol, n, 1).getDisplayValues().map(function (r) { return String(r[0]).trim(); }) : [];
  const cur = n ? sh.getRange(L.first, L.date, n, 4).getDisplayValues() : [];
  const log = [];
  const now = new Date();
  const marker = String(body.marker || '');
  let written = 0;
  (body.rows || []).forEach(function (x) {
    const i = ids.indexOf(String(x.case_id).trim());
    const base = [now, body.run_id, body.user || '', x.case_id];
    if (i < 0) { log.push(base.concat(['', '', '', 'در شیت پیدا نشد'])); return; }
    const ours = cur[i].every(function (v) { return v === ''; }) || (marker !== '' && cur[i][1] === marker);
    if (!ours) { log.push(base.concat([L.first + i, '', '', 'قبلاً پر شده؛ دست نخورد'])); return; }
    sh.getRange(L.first + i, L.date, 1, 4).setValues([[x.date, x.result, x.edit_reason || '', x.cancel_reason || '']]);
    cur[i] = [x.date, x.result, x.edit_reason || '', x.cancel_reason || ''];
    written++;
    log.push(base.concat([L.first + i, x.result, x.edit_reason || x.cancel_reason || '', 'نوشته شد']));
  });
  write(tab(ss, OI_LOG_TAB, OI_LOG_HEAD), log);
  return { ok: true, written: written, skipped: (body.rows || []).length - written };
}
"""

_WEBAPP = re.compile(r"^https://script\.google\.com/(?:a/[^/]+/)?macros/s/[A-Za-z0-9_-]{20,}/exec$")

# The team's shared Online-Instore sheet (tab 'Pending', columns H-K = the Online team's own; seen on the owner's screenshot
# 2026-10-01: H 'Date Online Check' as '1405/06/17', I 'بررسی قرارداد' with these three values, J/K NBO's own reason labels).
# 'marker': the word the old automation wrote in 'بررسی قرارداد' when it could not decide (garbled in the meeting recording;
# the person picks it from the values the sheet actually holds). Empty = MANUAL writes nothing and only empty rows are ours.
DEFAULT_OI = {"sheet_id": "1FCt7WfmuQ5zy_jwafsLe2a7xkbKouS28wep1d94lF3s", "tab": "Pending", "enabled": False, "marker": "",
              "result_values": {"APPROVE": "تایید قرارداد", "EDIT": "نیاز به ادیت", "CANCEL": "لغو قرارداد"}}


class SheetError(RuntimeError):
    pass


def config_file():
    return user_dir() / "sheet.json"


def load() -> dict:
    try:
        cfg = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    changed = False
    if not cfg.get("secret"):
        cfg["secret"] = secrets.token_urlsafe(24)               # per install; it only exists here and inside the person's script
        changed = True
    cfg.setdefault("webapp_url", "")
    cfg.setdefault("auto_send", False)
    cfg.setdefault("sheet_name", "")
    cfg.setdefault("sent_runs", [])
    oi = cfg.setdefault("oi", {})
    for k, v in DEFAULT_OI.items():
        oi.setdefault(k, json.loads(json.dumps(v)))
    if changed:
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    config_file().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def script_code(cfg: dict = None) -> str:
    cfg = cfg or load()
    oi = cfg.get("oi", {})
    sheet_id = str(oi.get("sheet_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{25,80}", sheet_id):          # not a Google file id => the script may write nowhere else
        sheet_id = ""
    tab = re.sub(r"['\"\\\r\n]", "", str(oi.get("tab", "")))
    return APPS_SCRIPT.replace("__SECRET__", cfg["secret"]).replace("__OI_ID__", sheet_id).replace("__OI_TAB__", tab)


def valid_webapp_url(url: str) -> bool:
    return bool(_WEBAPP.match((url or "").strip()))


def _post(url: str, payload: dict, client=None, timeout=90.0) -> dict:
    if not valid_webapp_url(url):
        raise SheetError("لینک وب‌اپ درست نیست؛ باید شبیه https://script.google.com/macros/s/…/exec باشد.")
    own = client is None
    client = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        r = client.post(url.strip(), content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                        headers={"Content-Type": "application/json; charset=utf-8"})
    except httpx.HTTPError as e:
        raise SheetError(f"به Google وصل نشد ({type(e).__name__}). اتصال اینترنت را بررسی کن.") from e
    finally:
        if own:
            client.close()
    text = r.text.strip()
    if r.status_code != 200 or not text.startswith("{"):
        if "accounts.google.com" in str(r.url) or "<html" in text[:200].lower():
            raise SheetError("وب‌اپ ورود به Google می‌خواهد: در Deploy، گزینه‌ی «Who has access» را روی «Anyone» بگذار و دوباره Deploy کن.")
        raise SheetError(f"پاسخ نامعتبر از Google (HTTP {r.status_code}).")
    data = json.loads(text)
    if not data.get("ok"):
        if data.get("error") == "forbidden":
            raise SheetError("کلید اسکریپت با این اپ یکی نیست؛ اسکریپت را دوباره از اپ کپی و Deploy کن.")
        raise SheetError("Google خطا داد: " + str(data.get("error")))
    return data


def ping(cfg: dict = None, client=None) -> dict:
    cfg = cfg or load()
    data = _post(cfg.get("webapp_url", ""), {"secret": cfg["secret"], "action": "ping"}, client)
    cfg["sheet_name"] = data.get("sheet", "")
    save(cfg)
    return data


def _row(r: dict) -> dict:
    secs = round(r["duration_ms"] / 1000, 1) if r.get("duration_ms") is not None else ""
    return {"smr": r["smr"], "site": r.get("site") or "", "category": r.get("category") or "", "created_at": r.get("created_at") or "",
            "action": r["action"], "action_fa": ACTION_FA.get(r["action"], r["action"]), "codes": "، ".join(r.get("reason_codes") or []),
            "reasons_fa": reasons_fa(r.get("reason_codes")), "notes_fa": notes_fa(r.get("notes")), "seconds": secs}


def send_run(run_id: str, results: list, user_name: str = "", cfg: dict = None, client=None) -> dict:
    """Adds one run's results to the person's sheet. Sending the same run twice adds nothing (the script checks)."""
    cfg = cfg or load()
    t0 = time.monotonic()
    data = _post(cfg.get("webapp_url", ""), {"secret": cfg["secret"], "action": "append", "run_id": run_id, "user": user_name or "",
                                            "rows": [_row(r) for r in results]}, client)
    if run_id not in cfg["sent_runs"]:
        cfg["sent_runs"] = (cfg["sent_runs"] + [run_id])[-500:]
        save(cfg)
    log.info("run %s sent to the Google Sheet: %s rows%s (%.1fs)", run_id, data.get("appended", 0),
             " (already there)" if data.get("duplicate") else "", time.monotonic() - t0)
    return data


# ---- shared Online-Instore sheet (Pending tab, the Online team's 4 columns) ------------------------------------------------
def nbo_labels() -> dict:
    from .paths import config_dir
    data = json.loads((config_dir() / "nbo_reasons.json").read_text(encoding="utf-8"))
    return {"edit": data.get("edit", {}), "cancel": data.get("cancel", {})}


def oi_describe(cfg: dict = None, client=None) -> dict:
    """Layout, dropdown values and the Case IDs whose 4 Online cells are all still empty (read through the person's script)."""
    cfg = cfg or load()
    return _post(cfg.get("webapp_url", ""), {"secret": cfg["secret"], "action": "oi_describe", "marker": cfg["oi"].get("marker", "")}, client)


def oi_rows(results: list, cfg: dict = None, labels: dict = None, today: str = None):
    """-> (rows to write, [(smr, why skipped)]). MANUAL writes nothing - unless the team's marker word is set, then the marker
    goes into 'بررسی قرارداد' like the old code did ('looked at, a person must decide'). EDIT/CANCEL are written only with
    NBO's own label for the reason (never a guessed text)."""
    from .jalali import jdate
    cfg = cfg or load()
    labels = labels or nbo_labels()
    values = cfg["oi"]["result_values"]
    today = today or jdate(persian_digits=False)
    rows, skipped = [], []
    for r in results:
        action = r["action"]
        if action not in ("APPROVE", "EDIT", "CANCEL"):
            marker = cfg["oi"].get("marker", "")
            if marker:
                rows.append({"case_id": r["smr"], "date": today, "result": marker, "edit_reason": "", "cancel_reason": ""})
            else:
                skipped.append((r["smr"], "بررسی دستی - برای همکار خالی می‌ماند"))
            continue
        code = (r.get("reason_codes") or [None])[0]
        edit = labels["edit"].get(code, "") if action == "EDIT" else ""
        cancel = labels["cancel"].get(code, "") if action == "CANCEL" else ""
        if action != "APPROVE" and not (edit or cancel):
            skipped.append((r["smr"], f"برچسب NBO برای «{code}» نیست"))
            continue
        rows.append({"case_id": r["smr"], "date": today, "result": values[action], "edit_reason": edit, "cancel_reason": cancel})
    return rows, skipped


def oi_problems(describe: dict, cfg: dict = None, labels: dict = None) -> list:
    """Every value the app would write must exist in the sheet's own dropdown lists; otherwise nothing is written."""
    cfg = cfg or load()
    labels = labels or nbo_labels()
    allowed = describe.get("allowed") or {}
    problems = []
    res = allowed.get("result")
    if res:
        for action, v in cfg["oi"]["result_values"].items():
            if v not in res:
                problems.append(f"مقدار «{v}» ({action}) در فهرست ستون «بررسی قرارداد» نیست")
    else:
        problems.append("ستون «بررسی قرارداد» فهرست کشویی ندارد؛ مقدارها قابل تطبیق نیستند")
    for kind, col in (("edit", "دلایل نیاز به ادیت"), ("cancel", "دلایل لغو قرارداد")):
        lst = allowed.get(kind)
        if lst:
            missing = [lab for lab in labels[kind].values() if lab not in lst]
            if missing:
                problems.append(f"{len(missing)} دلیل NBO در فهرست ستون «{col}» نیست (آن درخواست‌ها نوشته نمی‌شوند)")
    return problems


def oi_write(run_id: str, rows: list, user_name: str = "", cfg: dict = None, client=None) -> dict:
    cfg = cfg or load()
    if not cfg["oi"].get("enabled"):
        raise SheetError("ثبت در شیت Online-Instore خاموش است (تنظیمات > اتصال‌ها).")
    data = _post(cfg.get("webapp_url", ""), {"secret": cfg["secret"], "action": "oi_write", "run_id": run_id, "user": user_name or "",
                                            "marker": cfg["oi"].get("marker", ""), "rows": rows}, client)
    log.info("run %s -> Online-Instore: %s written, %s skipped", run_id, data.get("written"), data.get("skipped"))
    return data
