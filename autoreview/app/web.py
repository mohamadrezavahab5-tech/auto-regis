"""Embedded browsers.

NBO: one persistent profile in the person's own folder. The person signs in on NBO's own page inside the app (username,
password, OTP typed by them; the app never sees them). The export then runs INSIDE that NBO page - the same request NBO's
own 'Export' button sends - so the session token never leaves NBO's page; only the Excel bytes come back.
CRM: its own profile; Windows sign-in (NTLM) is answered with the person's CRM login from this session.
Renderer: an off-the-record hidden browser for the 'second look' at merchant pages drawn by JavaScript (no downloads, no
pop-ups, no images, nothing kept)."""
import base64
import concurrent.futures
import json
from collections import deque

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWidgets import QApplication

from .. import logs
from ..collectors.site import UA
from ..paths import web_dir

log = logs.get("web")

NBO_BASE = "https://nbo.snapppay.ir"
NBO_LOGIN = NBO_BASE + "/login"
NBO_REGISTRATIONS = NBO_BASE + "/merchant-support/registration"
CRM_BASE = "http://crm.snapppay.ir/CRM-SnappPay-DB/"
CRM_REGISTRATIONS = CRM_BASE + "main.aspx?etn=new_merchantregistration&pagetype=entitylist"
EXPORT_PATH = "/api/chandler/api/v1/backoffice/registrations/export"
LIST_PATH = "/api/chandler/api/v1/backoffice/registrations"
ALL_NBO_STATUSES = ("PENDING", "COMMERCIAL_IN_PROGRESS", "COMMERCIAL_APPROVED", "ACTIVATING", "COMPLETED", "REQUIRED_EDITING",
                    "CANCELLED", "PENDING_ACTIVATION", "DRAFT")

LOGGED_IN_JS = "(function(){try{var t=localStorage.getItem('token');return !!t && t!=='null';}catch(e){return false;}})()"

_EXPORT_JS = r"""(function () {
  window.__ar = {state: 'running'}; window.__arData = null;
  var t = null; try { t = localStorage.getItem('token'); } catch (e) {}
  if (!t || t === 'null') { window.__ar = {state: 'login'}; return true; }
  fetch('__PATH__?__QS__', {headers: {'Authorization': 'Bearer ' + t, 'Accept': '*/*'}})
    .then(function (r) {
      if (r.status === 401) { window.__ar = {state: 'login'}; return null; }
      if (!r.ok) { window.__ar = {state: 'http', status: r.status}; return null; }
      window.__ar = {state: 'reading', type: r.headers.get('content-type') || ''};
      return __READ__;
    })
    .then(function (body) {
      if (body === null || body === undefined) return;
      __DONE__
    })
    .catch(function (e) { window.__ar = {state: 'error', message: String(e)}; });
  return true;
})()"""
_READ_BYTES = "r.arrayBuffer()"
_DONE_BYTES = r"""var bytes = new Uint8Array(body), CH = 0x8000, parts = [];
      for (var i = 0; i < bytes.length; i += CH) parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + CH)));
      window.__arData = btoa(parts.join(''));
      window.__ar = {state: 'done', size: bytes.length};"""
_READ_JSON = "r.json()"
_DONE_JSON = r"""var n = null;
      if (body && typeof body === 'object') {
        n = body.totalElements; if (n === undefined && body.data) n = body.data.totalElements;
        if (n === undefined && body.page) n = body.page.totalElements;
      }
      window.__ar = {state: 'done', total: (n === undefined ? null : n)};"""


def statuses_qs(statuses) -> str:
    """The query NBO's own filter box sends: statuses%5B0%5D=PENDING&statuses%5B1%5D=..."""
    return "&".join(f"statuses%5B{i}%5D={s}" for i, s in enumerate(statuses) if s.replace("_", "").isalnum())


def export_js(statuses) -> str:
    return (_EXPORT_JS.replace("__PATH__", EXPORT_PATH).replace("__QS__", statuses_qs(statuses))
            .replace("__READ__", _READ_BYTES).replace("__DONE__", _DONE_BYTES))


def probe_js() -> str:
    return (_EXPORT_JS.replace("__PATH__", LIST_PATH).replace("__QS__", statuses_qs(["PENDING"]) + "&pageNumber=0")
            .replace("__READ__", _READ_JSON).replace("__DONE__", _DONE_JSON))


# ---- profiles ---------------------------------------------------------------------------------------------------------------
_profiles = {}


def _persistent(name):
    if name not in _profiles:
        prof = QWebEngineProfile(name, QApplication.instance())
        prof.setPersistentStoragePath(str(web_dir() / name))
        prof.setCachePath(str(web_dir() / f"{name}-cache"))
        prof.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        prof.downloadRequested.connect(lambda d: d.cancel())          # nothing is saved by a page on its own
        _profiles[name] = prof
    return _profiles[name]


def nbo_profile():
    return _persistent("nbo")


def crm_profile():
    return _persistent("crm")


class Page(QWebEnginePage):
    """No pop-up windows; console noise goes to the debug log only."""

    def createWindow(self, _type):
        return None

    def javaScriptConsoleMessage(self, level, message, line, source):
        log.debug("page console: %s", message[:300])


# ---- NBO: export and quick check inside the person's own NBO session -------------------------------------------------------
class NboClient(QObject):
    """A hidden NBO page (no window). run(js) starts an async job in the page and polls until it reports done."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.page = Page(nbo_profile(), self)
        self._loaded = False
        self._waiting = []
        self.page.loadFinished.connect(self._on_load)

    def _on_load(self, ok):
        self._loaded = ok
        waiting, self._waiting = self._waiting, []
        for cb in waiting:
            cb(ok)

    def ensure(self, cb):
        if self._loaded:
            cb(True)
            return
        self._waiting.append(cb)
        if len(self._waiting) == 1:
            self.page.load(QUrl(NBO_BASE + "/"))
            QTimer.singleShot(40_000, lambda: self._waiting and self._on_load(False))   # NBO did not answer: report, never hang

    def reload(self):
        self._loaded = False

    def logged_in(self, cb):
        self.ensure(lambda ok: self.page.runJavaScript(LOGGED_IN_JS, 0, lambda v: cb(bool(v))) if ok else cb(False))

    def _poll(self, on_state, timeout_ms):
        waited = {"ms": 0}

        def tick():
            def got(state):
                st = json.loads(state) if isinstance(state, str) and state not in ("", "null") else None
                if st and st.get("state") not in ("running", "reading"):
                    on_state(st)
                    return
                waited["ms"] += 500
                if waited["ms"] > timeout_ms:
                    on_state({"state": "timeout"})
                    return
                QTimer.singleShot(500, tick)
            self.page.runJavaScript("JSON.stringify(window.__ar||null)", 0, got)
        QTimer.singleShot(400, tick)

    def export(self, statuses, on_done, timeout_ms=360_000):
        """on_done(bytes | None, error: None | 'login' | 'network' | 'http_<n>' | 'timeout' | 'not_excel' | message)."""
        def start(ok):
            if not ok:
                on_done(None, "network")
                return
            self.page.runJavaScript(export_js(statuses), 0, lambda _: self._poll(after, timeout_ms))

        def after(st):
            s = st.get("state")
            if s == "done":
                self._read_data(st.get("size", 0), on_done)
            elif s == "login":
                on_done(None, "login")
            elif s == "http":
                on_done(None, f"http_{st.get('status')}")
            elif s == "timeout":
                on_done(None, "timeout")
            else:
                on_done(None, str(st.get("message") or s))
        self.ensure(start)

    def _read_data(self, size, on_done, chunk=900_000):
        parts = []

        def length(n):
            if not n:
                on_done(None, "empty")
                return
            step(0, int(n))

        def step(i, n):
            if i >= n:
                self.page.runJavaScript("window.__arData=null;", 0)
                try:
                    data = base64.b64decode("".join(parts))
                except ValueError:
                    on_done(None, "decode")
                    return
                on_done(data, None if data[:2] == b"PK" else "not_excel")
                return
            self.page.runJavaScript(f"window.__arData.substr({i},{chunk})", 0, lambda s: (parts.append(s or ""), step(i + chunk, n)))
        self.page.runJavaScript("window.__arData ? window.__arData.length : 0", 0, length)

    def probe(self, on_done, timeout_ms=60_000):
        """Quick check: can this session read the PENDING list? on_done({'state':..., 'total': n|None, ...})."""
        def start(ok):
            if not ok:
                on_done({"state": "network"})
                return
            self.page.runJavaScript(probe_js(), 0, lambda _: self._poll(on_done, timeout_ms))
        self.ensure(start)


# ---- hidden renderer for the 'second look' ------------------------------------------------------------------------------
class PageRenderer(QObject):
    """render_async(url) from ANY thread -> awaitable HTML after the page's JavaScript ran (or None)."""
    _request = Signal(str, object)

    def __init__(self, parent=None, max_pages=2, settle_ms=2500, timeout_ms=25_000):
        super().__init__(parent)
        self.profile = QWebEngineProfile(self)                    # off the record: nothing is stored
        self.profile.setHttpUserAgent(UA)
        s = self.profile.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, True)
        self.profile.downloadRequested.connect(lambda d: d.cancel())
        self.max_pages, self.settle_ms, self.timeout_ms = max_pages, settle_ms, timeout_ms
        self.queue, self.active = deque(), 0
        self._request.connect(self._enqueue)

    def render_async(self, url):
        import asyncio
        fut = concurrent.futures.Future()
        self._request.emit(url, fut)
        return asyncio.wrap_future(fut)

    def _enqueue(self, url, fut):
        self.queue.append((url, fut))
        self._pump()

    def _pump(self):
        while self.active < self.max_pages and self.queue:
            url, fut = self.queue.popleft()
            self.active += 1
            self._render(url, fut)

    def _render(self, url, fut):
        page = Page(self.profile, self)
        page.setAudioMuted(True)
        state = {"done": False}

        def finish(html):
            if state["done"]:
                return
            state["done"] = True
            if not fut.done():
                fut.set_result(html or None)
            page.deleteLater()
            self.active -= 1
            self._pump()

        def snapshot():
            if not state["done"]:
                page.toHtml(finish)

        page.loadFinished.connect(lambda ok: QTimer.singleShot(self.settle_ms, snapshot))
        QTimer.singleShot(self.timeout_ms, snapshot)              # a slow page: take whatever is there
        QTimer.singleShot(self.timeout_ms + 5000, lambda: finish(None))
        page.load(QUrl(url))
