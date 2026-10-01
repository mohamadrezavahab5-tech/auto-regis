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

APPS_SCRIPT = r"""// AutoReview -> this spreadsheet. It only ADDS rows to its own three tabs; nothing else in the file is read or changed.
// Setup: Extensions > Apps Script > paste all of this > Save
//        Deploy > New deployment > type: Web app > Execute as: Me > Who has access: Anyone > Deploy
//        allow access when Google asks, then copy the "Web app URL" into AutoReview.
const SECRET = '__SECRET__';
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
  if (body.action === 'ping') return out({ ok: true, sheet: ss.getName(), url: ss.getUrl(), version: 1 });
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
"""

_WEBAPP = re.compile(r"^https://script\.google\.com/(?:a/[^/]+/)?macros/s/[A-Za-z0-9_-]{20,}/exec$")


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
    if changed:
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    config_file().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def script_code(cfg: dict = None) -> str:
    return APPS_SCRIPT.replace("__SECRET__", (cfg or load())["secret"])


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
