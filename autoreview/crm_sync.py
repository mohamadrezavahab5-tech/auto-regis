"""CRM (Dynamics, crm.snapppay.ir) - READ-ONLY. It is also how a person signs in to the app: their own CRM login.

Auth: the person's CRM (domain) username/password, typed in the app. 'SNAPP\\' is added the same way the team's other CRM
tool does it. The login is stored DPAPI-encrypted (only this Windows user on this PC can decrypt it) while signed in;
without "remember me" it is deleted when the app closes. A refused password is deleted at once and never re-sent
automatically (repeated failures can lock the domain account).
Transport: PowerShell Invoke-WebRequest with NTLM. Nothing is ever written to CRM: the only verb used is GET."""
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from . import logs
from .paths import config_dir, data_dir, scripts_dir

FORMATTED = "@OData.Community.Display.V1.FormattedValue"
DOMAIN = "SNAPP"                     # the NTLM domain seen from crm.snapppay.ir
BASE = "http://crm.snapppay.ir/CRM-SnappPay-DB/"
log = logs.get("crm")
_authenticated_identity = None


def authenticated_identity():
    """RAM-only identity established by a successful CRM WhoAmI in this process."""
    return dict(_authenticated_identity) if _authenticated_identity else None


class CrmAuthError(RuntimeError):
    """CRM refused the stored login (HTTP 401)."""


class CrmUnreachable(RuntimeError):
    """crm.snapppay.ir did not answer (VPN / network)."""


def normalize_username(username: str) -> str:
    """'ali' -> 'SNAPP\\\\ali'; 'SNAPP\\\\ali' and 'ali@domain' are left as typed."""
    u = (username or "").strip()
    return u if ("\\" in u or "@" in u) else f"{DOMAIN}\\{u}"


def _script(name: str) -> Path:
    p = scripts_dir() / name
    if not p.is_file():
        raise FileNotFoundError(f"helper script missing: {p}")
    return p


def cred_file() -> Path:
    return data_dir() / "crm_credential.xml"


def stored_username():
    """The user name inside the saved login (the password part stays encrypted). None when nothing is saved."""
    try:
        raw = cred_file().read_bytes()
    except OSError:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):                    # Export-Clixml writes UTF-16 with a BOM
        text = raw.decode("utf-16", errors="ignore")
    else:
        text = raw.decode("utf-8-sig", errors="ignore")
    m = re.search(r'<S N="UserName">(.*?)</S>', text)
    return m.group(1).replace("&#92;", "\\") if m else None


def have_credentials() -> bool:
    """A saved login exists AND has the domain form. A login saved by an older version without 'SNAPP\\\\' is dropped
    (CRM always refuses it, and resending it would only count towards a lock-out)."""
    user = stored_username()
    if user is None:
        return False
    if "\\" not in user and "@" not in user:
        forget_credentials()
        return False
    return True


def powershell_environment():
    """Windows PowerShell must not inherit incompatible PowerShell 7 module paths."""
    env = os.environ.copy()
    base = Path(env.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0'
    env['PSModulePath'] = str(base / 'Modules')
    return env


def _ps(args, stdin=None, timeout=300):
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", *args]
    return subprocess.run(cmd, input=stdin, capture_output=True, timeout=timeout,
                          env=powershell_environment(),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def save_credentials(username: str, password: str) -> None:
    if not username or not password:
        raise ValueError("username and password are required")
    data = f"{normalize_username(username)}\n{password}\n".encode("utf-8")
    r = _ps([str(_script("crm-save-cred.ps1")), "-OutFile", str(cred_file())], stdin=data, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("ذخیره‌ی ورود CRM ممکن نشد: " + r.stderr.decode("utf-8", "replace").strip()[:200])


def forget_credentials() -> None:
    global _authenticated_identity
    _authenticated_identity = None
    try:
        cred_file().unlink()
    except FileNotFoundError:
        pass


def server_reachable(timeout=8.0) -> bool:
    """Unauthenticated probe: CRM answers 401 (asks for a login) when it is reachable. No credential is sent."""
    try:
        r = httpx.get(BASE, timeout=timeout, follow_redirects=False)
        return r.status_code in (200, 302, 401, 403)
    except httpx.HTTPError:
        return False


def get(path: str, runner=None) -> dict:
    """One GET against the CRM Web API. `runner` is injectable for tests."""
    if runner:
        return runner(path)
    try:
        r = _ps([str(_script("crm-get.ps1")), "-Path", path, "-CredFile", str(cred_file())], timeout=90)
    except subprocess.TimeoutExpired:
        raise CrmUnreachable("CRM بیش از ۹۰ ثانیه پاسخ نداد؛ اتصال شبکه / VPN را بررسی کن.")
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip() or r.stdout.decode("utf-8", "replace").strip()
        if "401" in err or "Unauthorized" in err:
            forget_credentials()          # never resend a refused password
            log.warning("CRM refused the login (401); the saved login was removed")
            raise CrmAuthError("CRM نام کاربری یا رمز را رد کرد. برای جلوگیری از قفل شدن حساب، ورود ذخیره‌شده پاک شد؛ "
                               "یک بار دیگر با دقت وارد شو.")
        if "remote name could not be resolved" in err or "Unable to connect" in err or "timed out" in err.lower():
            raise CrmUnreachable("سرور CRM در دسترس نیست (اتصال شبکه / VPN را بررسی کن).")
        raise RuntimeError("خواندن از CRM ناموفق بود: " + err[:300])
    return json.loads(r.stdout.decode("utf-8-sig"))


def whoami(runner=None) -> dict:
    """Cheapest authenticated call: proves the stored login works. -> {'UserId': ..., ...}"""
    global _authenticated_identity
    who = get("WhoAmI", runner)
    name = stored_username()
    if not who.get('UserId') or not name:
        _authenticated_identity = None
        raise CrmAuthError('هویت تأییدشدهٔ CRM دریافت نشد')
    _authenticated_identity = {'username': name, 'user_id': who['UserId']}
    return who


def login(username: str, password: str, runner=None) -> dict:
    """Store the login, prove it against CRM and read the person's display name. -> {username, display_name, user_id}.
    On any failure the stored login is removed again."""
    save_credentials(username, password)
    try:
        who = whoami(runner)
        user_id = who.get("UserId")
        name = normalize_username(username)
        try:
            rec = get(f"systemusers({user_id})?$select=fullname", runner) if user_id else {}
            name = rec.get("fullname") or name
        except Exception:                                       # the display name is a nicety, not a reason to fail
            pass
        log.info("signed in to CRM as %s", normalize_username(username))
        return {"username": normalize_username(username), "display_name": name, "user_id": user_id}
    except Exception:
        forget_credentials()
        raise


def get_all(path: str, runner=None, max_pages=200):
    """Follow @odata.nextLink until exhausted."""
    rows, page = [], 0
    while path and page < max_pages:
        data = get(path, runner)
        rows.extend(data.get("value", []))
        path, page = data.get("@odata.nextLink"), page + 1
    return rows


def load_mapping():
    return json.loads((config_dir() / "crm_api.json").read_text(encoding="utf-8"))


def fetch_rows(mapping=None, runner=None):
    """-> [{'smr','status','site'}] of the approved registrations (the OData filter in crm_api.json)."""
    m = mapping or load_mapping()
    f = m["fields"]
    if not m.get("entity_set") or not all(f.get(k) for k in ("smr", "status", "site")):
        raise RuntimeError("CRM mapping is not filled in (config/crm_api.json)")
    sel = ",".join(sorted({f["smr"], f["status"], f["site"]}))
    path = f"{m['entity_set']}?$select={sel}" + (f"&$filter={quote(m['filter'], safe='(),' + chr(39))}" if m.get("filter") else "")
    t0 = time.monotonic()
    rows = []
    for r in get_all(path, runner):
        status = r.get(f["status"] + FORMATTED) if m.get("status_labels_are_formatted_values", True) else r.get(f["status"])
        smr = str(r.get(f["smr"]) or "").strip()
        if smr:
            rows.append({"smr": smr, "status": str(status if status is not None else r.get(f["status"]) or "").strip(),
                         "site": str(r.get(f["site"]) or "").strip()})
    log.info("CRM: %d approved registrations read in %.1fs", len(rows), time.monotonic() - t0)
    return rows


def fetch_reference(since=None, runner=None, progress=None, mapping=None):
    """The whole Merchant Registrations list (every status) for the reference: [{caseid, status, site, brand, person_company,
    store_type, created_on, modified_on}]. since = ISO time: only rows modified after it (incremental load).
    progress(pages_done, rows_so_far) is called after every page."""
    m = mapping or load_mapping()
    f = m["reference"]["fields"]
    path = f"{m['entity_set']}?$select={','.join(f.values())}"
    if since:
        path += "&$filter=" + quote(f"{f['modified_on']} gt {since}", safe="")
    t0 = time.monotonic()
    rows, pages = [], 0
    seen_pages = set()
    while path and pages < 5000:
        if path in seen_pages:
            raise RuntimeError("CRM صفحه‌بندی تکراری برگرداند؛ دریافت متوقف شد تا در حلقه نیفتد.")
        seen_pages.add(path)
        data = get(path, runner)
        for r in data.get("value", []):
            caseid = str(r.get(f["caseid"]) or "").strip()
            if not caseid:
                continue
            rows.append({"caseid": caseid,
                         "status": str(r.get(f["status"] + FORMATTED) or r.get(f["status"]) or "").strip(),
                         "site": str(r.get(f["site"]) or "").strip(),
                         "brand": str(r.get(f["brand"]) or "").strip(),
                         "person_company": str(r.get(f["person_company"] + FORMATTED) or "").strip(),
                         "store_type": str(r.get(f["store_type"] + FORMATTED) or "").strip(),
                         "created_on": str(r.get(f["created_on"]) or ""), "modified_on": str(r.get(f["modified_on"]) or "")})
        pages += 1
        if progress:
            progress(pages, len(rows))
        path = data.get("@odata.nextLink")
    if path:
        raise RuntimeError("CRM بیش از ۵۰۰۰ صفحه برگرداند؛ دریافت برای جلوگیری از حلقه متوقف شد.")
    log.info("CRM reference: %d rows (%s) in %.1fs", len(rows), f"changed since {since}" if since else "full", time.monotonic() - t0)
    return rows


def check(runner=None, reachable=None) -> list:
    """Connection test for the Connections page: [(step, ok: bool|None, detail)]. Stops at the first failing step."""
    steps = []
    up = server_reachable() if reachable is None else reachable
    steps.append(("سرور CRM پاسخ می‌دهد", up, "crm.snapppay.ir" if up else "در دسترس نیست؛ شبکه / VPN را بررسی کن"))
    if not up:
        return steps
    if not have_credentials():
        steps.append(("ورود به CRM", False, "وارد نشده‌ای"))
        return steps
    try:
        whoami(runner)
        steps.append(("ورود به CRM پذیرفته شد", True, stored_username() or ""))
    except Exception as e:
        steps.append(("ورود به CRM پذیرفته شد", False, str(e)))
        return steps
    try:
        rows = fetch_rows(runner=runner)
        with_site = sum(1 for r in rows if r["site"])
        steps.append(("خواندن ثبت‌نام‌های تاییدشده", bool(rows), f"{len(rows):,} ردیف، {with_site:,} با آدرس سایت"))
    except Exception as e:
        steps.append(("خواندن ثبت‌نام‌های تاییدشده", False, str(e)))
    return steps
