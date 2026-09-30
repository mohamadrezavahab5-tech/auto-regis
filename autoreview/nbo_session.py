"""Automatic NBO export - READ-ONLY.

Login: the person signs in themselves (user name, password, OTP) in a real Chrome window that this module opens ONCE.
Only the browser's session state is kept (data/nbo_state.json, never read by anything but the browser). No password is ever seen or stored.
Export: afterwards everything runs in a HEADLESS Chrome (no visible window/tabs): it opens the requests page with the saved session,
lets NBO's own page authenticate, and calls NBO's own export endpoint (GET backoffice/registrations/export). Only GET requests are made.
Changing a status (PUT change-status) is deliberately not implemented here - that belongs to the guarded executor."""
import time
from pathlib import Path

from .paths import data_dir

BASE = "https://nbo.snapppay.ir"
LOGIN_URL = BASE + "/login"
LIST_URL = BASE + "/merchant-support/registration"
API = BASE + "/api/chandler/api/v1/backoffice/registrations"


class NboLoginRequired(RuntimeError):
    pass


def state_file() -> Path:
    return data_dir() / "nbo_state.json"


def have_session() -> bool:
    return state_file().is_file()


def forget_session() -> None:
    state_file().unlink(missing_ok=True)


def export_params(statuses) -> dict:
    """Query for the export endpoint, in the form NBO's own filter box sends: statuses[0]=PENDING&statuses[1]=..."""
    return {f"statuses[{i}]": s for i, s in enumerate(statuses)}


def looks_like_xlsx(data: bytes) -> bool:
    return data[:2] == b"PK"


def login_interactive(timeout_s: int = 600) -> None:
    """Opens Chrome for the person to sign in. Returns when the site is past /login, then stores the session state."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if "/login" not in page.url and page.url.startswith(BASE):
                break
            page.wait_for_timeout(1000)
        else:
            browser.close()
            raise TimeoutError("NBO login was not completed in time")
        page.wait_for_timeout(2500)                      # let the app finish storing its session
        ctx.storage_state(path=str(state_file()))
        browser.close()


def download_export(statuses, dest: Path) -> Path:
    """Headless: -> path of the downloaded .xlsx. Raises NboLoginRequired when the saved session is missing/expired."""
    from playwright.sync_api import sync_playwright
    if not have_session():
        raise NboLoginRequired("not signed in to NBO yet")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(storage_state=str(state_file()))
        page = ctx.new_page()
        auth = {}

        def on_request(req):
            if req.url.startswith(API) and "authorization" in req.headers and "authorization" not in auth:
                auth["authorization"] = req.headers["authorization"]          # kept in memory only, used for this run
        page.on("request", on_request)
        page.goto(LIST_URL, wait_until="networkidle", timeout=60000)
        if "/login" in page.url:
            browser.close()
            raise NboLoginRequired("the NBO session expired - sign in again")
        headers = {"Accept": "application/json, application/octet-stream, */*"}
        headers.update(auth)
        resp = ctx.request.get(API + "/export", params=export_params(statuses), headers=headers, timeout=300000)
        body = resp.body()
        browser.close()
    if resp.status == 401:
        raise NboLoginRequired("the NBO session expired - sign in again")
    if resp.status != 200 or not looks_like_xlsx(body):
        raise RuntimeError(f"NBO export did not return an Excel file (HTTP {resp.status}, {len(body)} bytes)")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return dest
