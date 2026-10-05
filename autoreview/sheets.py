"""Direct Google Sheets configuration and reporting; legacy settings are inert."""
import json
import re


from . import logs
from .paths import user_dir, config_dir
from .atomic_file import save_json
from .texts import ACTION_FA, notes_fa, reasons_fa

log = logs.get("sheet")



class SheetError(RuntimeError):
    def __init__(self, message, code='GOOGLE_API_ERROR'):
        self.code = code
        super().__init__(f'{code}: {message}')


def shared_config():
    try:
        return json.loads((config_dir() / 'workspace.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise SheetError('تنظیمات همراه برنامه خوانده نشد؛ نصب را ترمیم کن', 'DEPLOYMENT_MISMATCH') from error


def config_file():
    return user_dir() / "sheet.json"


def load() -> dict:
    try:
        cfg = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    # Keep legacy keys on disk for upgrade recovery, but never consult them for routing.
    previous = dict(cfg)
    cfg.setdefault('own_sheet_id', '1UHlktMbe6bhDMKQd51Z-H1pvrrgD3ppTl9QmI1cEFrQ')
    cfg.setdefault('sent_runs', [])
    cfg.setdefault('sheet_name', '')
    cfg.setdefault('sheet_editors', [])
    cfg.pop('oi', None)
    cfg.update(auth_mode='workspace', workspace_backend='google_sheets', workflow_sync=True, auto_send=False)
    if cfg != previous:
        save(cfg)
    return cfg


def save(cfg: dict) -> None:
    # Never persist a key accidentally passed by a legacy caller.
    save_json(config_file(), {k: v for k, v in cfg.items() if k not in ('private_key', 'app_key')})


def safe_diagnostic(value, payload=None):
    text = str(value)
    for key in ('app_key', 'secret', 'token', 'password', 'private_key', 'cookie', 'Authorization'):
        secret = (payload or {}).get(key)
        if isinstance(secret, str) and secret:
            text = text.replace(secret, '[REDACTED]')
    text = re.sub(r'https?://[^\s"<>]+', '[URL]', text)
    return text[:1000]


def ping(cfg=None, client=None):
    from . import workspace
    return workspace.health(cfg)


def _row(r: dict) -> dict:
    secs = round(r["duration_ms"] / 1000, 1) if r.get("duration_ms") is not None else ""
    return {"smr": r["smr"], "site": r.get("site") or "", "category": r.get("category") or "", "created_at": r.get("created_at") or "",
            "action": r["action"], "action_fa": ACTION_FA.get(r["action"], r["action"]), "codes": "، ".join(r.get("reason_codes") or []),
            "reasons_fa": reasons_fa(r.get("reason_codes")), "notes_fa": notes_fa(r.get("notes")), "seconds": secs,
            "decided_at": r.get("decided_at", "")}


def send_run(run_id, results, user_name='', cfg=None, client=None):
    """Export the existing Results report using the same direct Google credentials."""
    from .google_sheet import Client
    cfg = cfg or load()
    with Client(cfg['own_sheet_id']) as google:
        return google.append_run(run_id, [_row(r) for r in results])


# ---- NBO's own reason labels ----------------------------------------------------------------------------------------
def nbo_labels() -> dict:
    from .paths import config_dir
    data = json.loads((config_dir() / "nbo_reasons.json").read_text(encoding="utf-8"))
    return {"edit": data.get("edit", {}), "cancel": data.get("cancel", {})}
