"""Results and the review workflow -> the owner's OWN Google Sheet, and nothing else.

Owner rule (2026-10-01): "don't touch their sheets, only the sheet I made". The team's shared sheets (Main-Data,
Online-Instore) are neither read nor written by this app any more; the old shared-sheet writer was removed, not just switched
off. Two routes to the own sheet: the service-account key (google_sheet.py, the owner's PC) or the Apps Script web app inside
that sheet (scripts/own-sheet.gs, which only opens the spreadsheet it is bound to)."""
import json
import re
import secrets
import time

import httpx

from . import logs
from .paths import user_dir
from .texts import ACTION_FA, notes_fa, reasons_fa

log = logs.get("sheet")

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
    cfg.setdefault("own_sheet_id", "1UHlktMbe6bhDMKQd51Z-H1pvrrgD3ppTl9QmI1cEFrQ")
    cfg.setdefault("workflow_sync", False)
    cfg.setdefault("auth_mode", "apps_script")
    cfg.setdefault("sheet_name", "")
    cfg.setdefault("sent_runs", [])
    if cfg.pop("oi", None) is not None:                         # the old shared-sheet writer: removed, see module doc
        changed = True
    if changed:
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    config_file().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def script_code(cfg: dict = None) -> str:
    cfg = cfg or load()
    from .paths import scripts_dir
    sheet_id = str(cfg.get("own_sheet_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{25,80}", sheet_id):
        raise SheetError("شناسه شیت اختصاصی معتبر نیست")
    if not re.fullmatch(r"[A-Za-z0-9_-]{30,128}", cfg.get("secret", "")):
        raise SheetError("کلید اتصال معتبر نیست")
    return (scripts_dir() / "own-sheet.gs").read_text(encoding="utf-8").replace("__SECRET__", cfg["secret"]).replace("__OWN_ID__", sheet_id)


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
    if cfg.get('auth_mode') == 'service_account':
        from .google_sheet import Client
        with Client(cfg['own_sheet_id']) as google:
            return google.ping()
    data = _post(cfg.get("webapp_url", ""), {"secret": cfg["secret"], "action": "ping"}, client)
    cfg["sheet_name"] = data.get("sheet", "")
    save(cfg)
    return data


def _row(r: dict) -> dict:
    secs = round(r["duration_ms"] / 1000, 1) if r.get("duration_ms") is not None else ""
    return {"smr": r["smr"], "site": r.get("site") or "", "category": r.get("category") or "", "created_at": r.get("created_at") or "",
            "action": r["action"], "action_fa": ACTION_FA.get(r["action"], r["action"]), "codes": "، ".join(r.get("reason_codes") or []),
            "reasons_fa": reasons_fa(r.get("reason_codes")), "notes_fa": notes_fa(r.get("notes")), "seconds": secs,
            "decided_at": r.get("decided_at", "")}


def send_run(run_id: str, results: list, user_name: str = "", cfg: dict = None, client=None) -> dict:
    """Adds one run's results to the person's sheet. Sending the same run twice adds nothing (the script checks)."""
    cfg = cfg or load()
    if cfg.get('auth_mode') == 'service_account':
        from .google_sheet import Client
        with Client(cfg['own_sheet_id']) as google:
            return google.append_run(run_id, [_row(r) for r in results])
    t0 = time.monotonic()
    data = _post(cfg.get("webapp_url", ""), {"secret": cfg["secret"], "action": "append", "run_id": run_id, "user": user_name or "",
                                            "rows": [_row(r) for r in results]}, client)
    if run_id not in cfg["sent_runs"]:
        cfg["sent_runs"] = (cfg["sent_runs"] + [run_id])[-500:]
        save(cfg)
    log.info("run %s sent to the Google Sheet: %s rows%s (%.1fs)", run_id, data.get("appended", 0),
             " (already there)" if data.get("duplicate") else "", time.monotonic() - t0)
    return data


# ---- NBO's own reason labels ----------------------------------------------------------------------------------------
def nbo_labels() -> dict:
    from .paths import config_dir
    data = json.loads((config_dir() / "nbo_reasons.json").read_text(encoding="utf-8"))
    return {"edit": data.get("edit", {}), "cancel": data.get("cancel", {})}
