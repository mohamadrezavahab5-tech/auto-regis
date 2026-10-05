"""Applies ONE decision in NBO's own screen, the way the team's old scripts did (action-online-instore.py, 2026-09-26):
registrations list -> search the request -> Details -> 'Assign to me' -> 'Change Status' -> APPROVE / EDIT NEEDED / CANCEL
-> NBO reason -> the dialog's 'Change Status'. It runs in the person's own NBO session inside the app.

What the old code did and this never does:
- force-enable a disabled button (a disabled button means NBO does not allow the step: we stop and say so);
- pick a 'similar' reason (the reason is NBO's exact label from config/nbo_reasons.json, and it must read back as selected);
- act on a page that does not show this very request.
Rehearsal = every step except the final click (and except 'Assign to me', which already changes NBO): proves the screen
flow on a real request without changing anything."""
import json
import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from .. import logs
from .web import NBO_REGISTRATIONS, Page, nbo_profile

log = logs.get("nbo-actor")

OPTION = {"APPROVE": "APPROVE", "EDIT": "EDIT NEEDED", "CANCEL": "CANCEL"}
ERRORS = {
    "login": "وارد NBO نیستی؛ از صفحه‌ی NBO داخل اپ وارد شو",
    "no_search": "کادر جستجوی فهرست ثبت‌نام‌ها پیدا نشد (صفحه‌ی NBO عوض شده؟)",
    "not_found": "این کد درخواست در فهرست NBO پیدا نشد",
    "no_details": "لینک Details این درخواست پیدا نشد",
    "detail_mismatch": "صفحه‌ی باز شده مال همین درخواست نبود؛ کاری انجام نشد",
    "needs_assign": "تمرین تا Assign درست بود: درخواست هنوز به این حساب Assign نشده و دکمه‌ی «Assign to me» پیدا شد و فعال است "
                    "(اجرای Real همین دکمه را می‌زند؛ پنجره‌ی Change Status فقط روی درخواستِ Assign‌شده تمرین می‌شود)",
    "assign_missing": "دکمه‌ی Change Status فعال نیست و دکمه‌ی «Assign to me» هم پیدا نشد (شاید به کس دیگری Assign شده)",
    "change_status_disabled": "NBO اجازه‌ی تغییر وضعیت نداد (دکمه‌ی Change Status غیرفعال ماند)",
    "option_missing": "این گزینه در پنجره‌ی تغییر وضعیت NBO نبود؛ کاری انجام نشد",
    "reason_missing": "کادر دلیل در پنجره‌ی NBO پیدا نشد؛ کاری انجام نشد",
    "reason_not_selected": "دلیل دقیق NBO انتخاب نشد؛ کاری انجام نشد",
    "final_disabled": "دکمه‌ی نهایی Change Status غیرفعال بود؛ کاری انجام نشد",
    "timeout": "NBO در زمان مناسب پاسخ نداد",
    "load_failed": "صفحه‌ی NBO بارگذاری نشد؛ اتصال/VPN و دسترسی به NBO را بررسی کن",
}
# NBO answered the final click with an error message: its own words are shown
REFUSED = "NBO تغییر را نپذیرفت — پیام خود NBO: "
# errors after which NBO may already have changed: never retried automatically
AFTER_SEND = {"sent_unconfirmed", "nbo_refused"}
SYSTEMIC_ERRORS = frozenset({
    "login", "session_expired", "no_search", "load_failed", "script", "browser_failure", "timeout", "busy",
})

_HELPERS = r"""
const sleep = ms => new Promise(r => setTimeout(r, ms));
const txt = el => (el && (el.innerText || el.textContent) || '').trim();
const buttons = (root, label) => [...(root || document).querySelectorAll('button')].filter(b => txt(b) === label);
const setValue = (input, value) => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, value);
  input.dispatchEvent(new Event('input', {bubbles: true}));
  input.dispatchEvent(new Event('change', {bubbles: true}));
};
// Waits are real-time deadlines: a hidden page's timers are slowed to ~1/s, so counting loop rounds would lie.
const until = ms => { const end = Date.now() + ms; return () => Date.now() < end; };
const loggedIn = () => { try { const t = localStorage.getItem('token'); return !!t && t !== 'null'; } catch (e) { return false; } };
"""


def _wrap(body: str) -> str:
    """Runs body as an async function; the outcome is left in window.__arAct for polling (runJavaScript cannot await)."""
    return ("(function(){window.__arAct={state:'running'};(async()=>{" + _HELPERS + body +
            "})().then(r=>{window.__arAct=Object.assign({state:'done'},r||{});})"
            ".catch(e=>{const sent=!!(window.__arAct&&window.__arAct.sent);"
            "window.__arAct={state:'error',error:sent?'sent_unconfirmed':'script',message:String(e)};});return true;})()")


def search_js(smr: str) -> str:
    s = json.dumps(smr)
    return _wrap(f"""
  if (!loggedIn()) return {{error: 'login'}};
  let input = null;
  for (const more = until(10000); more() && !input; ) {{ input = document.querySelector('input'); if (!input) await sleep(250); }}
  if (!input) return {{error: 'no_search'}};
  setValue(input, {s});
  const search = buttons(document, 'Search')[0];
  if (!search) return {{error: 'no_search'}};
  search.click();
  for (const more = until(15000); more(); ) {{
    await sleep(250);
    const rows = [...document.querySelectorAll('tr, [role="row"]')].filter(r => txt(r).includes({s}));
    for (const row of rows) {{
      const link = [...row.querySelectorAll('a')].find(a => txt(a) === 'Details' && a.href);
      if (link) return {{href: link.href}};
    }}
  }}
  return {{error: document.body.innerText.includes({s}) ? 'no_details' : 'not_found'}};
""")


def detail_js(smr: str, action: str, reason_label: str, rehearsal: bool, allow_assign: bool) -> str:
    s, opt, reason = json.dumps(smr), json.dumps(OPTION[action]), json.dumps(reason_label or "")
    return _wrap(f"""
  if (!loggedIn()) return {{error: 'login'}};
  let seen = false;
  for (const more = until(20000); more() && !seen; ) {{ seen = document.body.innerText.includes({s}); if (!seen) await sleep(250); }}
  if (!seen) return {{error: 'detail_mismatch'}};
  let main = buttons(document, 'Change Status')[0];
  if ((!main || main.disabled) && {json.dumps(allow_assign)}) {{
    const assign = buttons(document, 'Assign to me')[0];
    if (assign && !assign.disabled) {{ assign.click(); return {{assigned: true}}; }}   // the app reloads the page, then runs again
  }}
  for (const more = until({json.dumps(30000 if allow_assign else 4000)}); more() && (!main || main.disabled); ) {{
    await sleep(500); main = buttons(document, 'Change Status')[0];
  }}
  if (!main) return {{error: 'change_status_disabled'}};
  if (main.disabled) {{
    if ({json.dumps(allow_assign)}) return {{error: 'change_status_disabled'}};
    const assign = buttons(document, 'Assign to me')[0];          // rehearsal: is the step the real run needs there?
    return {{error: assign && !assign.disabled ? 'needs_assign' : 'assign_missing'}};
  }}
  main.click();
  let dialog = null, option = null;
  for (const more = until(10000); more() && !option; ) {{
    await sleep(250);
    dialog = document.querySelector('[role="dialog"]') || document.body;
    option = [...dialog.querySelectorAll('span.label-text')].find(l => txt(l) === {opt});
  }}
  if (!option) return {{error: 'option_missing'}};
  option.click();
  if ({reason}) {{
    let input = null;
    for (const more = until(5000); more() && !input; ) {{
      input = dialog.querySelector("input[id^='react-select-']");
      if (input && (!input.isConnected || !input.getClientRects().length)) input = null;
      if (!input) await sleep(100);
    }}
    if (!input) return {{error: 'reason_missing'}};
    input.focus();
    setValue(input, {reason});
    let pick = null;
    for (const more = until(6000); more() && !pick; ) {{
      pick = [...document.querySelectorAll('[id*="-option-"]')].find(o => txt(o) === {reason});
      if (!pick) await sleep(100);
    }}
    if (!pick) return {{error: 'reason_not_selected'}};
    pick.click();
    let selected = false;
    for (const more = until(3000); more() && !selected; ) {{
      const chosen = [...dialog.querySelectorAll('[class*="singleValue"], [class*="multiValue"]')].map(txt);
      selected = chosen.includes({reason});
      if (!selected) await sleep(100);
    }}
    if (!selected) return {{error: 'reason_not_selected'}};
  }}
  let final = null;
  for (const more = until(3000); more() && !final; ) {{
    final = buttons(dialog, 'Change Status').pop();
    if (final && final.disabled) final = null;
    if (!final) await sleep(100);
  }}
  if (!final) {{
    document.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Escape', bubbles: true}}));
    return {{error: 'final_disabled'}};
  }}
  if ({json.dumps(rehearsal)}) {{
    document.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Escape', bubbles: true}}));
    return {{rehearsed: true}};
  }}
  // The dialog closing is not proof: NBO can close it and refuse the change (a cancel was "sent" while the request
  // stayed in progress, live 2026-10-03). Whatever NBO says after the click - a toast, an alert - is read and returned.
  const heard = () => {{
    const seen = [];
    document.querySelectorAll('[role="alert"], [role="status"], [class*="Toastify"], [class*="toast"], [class*="Toast"], '
      + '[class*="snackbar"], [class*="Snackbar"], [class*="notification"], [class*="Notification"], [class*="alert"]')
      .forEach(el => {{ const t = txt(el); if (t && t.length < 300 && !seen.includes(t)) seen.push(t); }});
    return seen;
  }};
  const before = heard();                               // only what appears AFTER the click counts
  const said = () => heard().filter(t => !before.includes(t)).join(' | ');
  window.__arAct.sent = true;
  final.click();
  let notice = '', closed = false;
  for (const more = until(10000); more(); ) {{
    await sleep(250);
    notice = said() || notice;
    const open = document.querySelector('[role="dialog"]');
    if (!open || !buttons(open, 'Change Status').length) {{ closed = true; break; }}
  }}
  for (const more = until(2500); more(); ) {{ await sleep(250); notice = said() || notice; }}
  if (/error|fail|invalid|denied|forbidden|unable|cannot|خطا|ناموفق|نامعتبر|مجاز نیست|امکان/i.test(notice))
    return {{error: 'nbo_refused', message: notice}};
  if (!closed) return {{error: 'sent_unconfirmed', message: notice}};
  return {{sent: true, notice: notice}};
""")


class NboActor(QObject):
    """One request at a time. run(...) -> on_done({'ok': bool, 'sent': bool, 'rehearsed': bool, 'error': code, 'message': fa})."""

    step = Signal(str)

    def __init__(self, parent=None, list_url=NBO_REGISTRATIONS, profile=None):
        super().__init__(parent)
        self.list_url = list_url
        self.page = Page(profile or nbo_profile(), self)
        self.busy = False
        self._load_cb = None
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._load_expired)
        self.page.loadFinished.connect(self._loaded)

    # ---- plumbing
    def _loaded(self, ok):
        self._load_timer.stop()
        cb, self._load_cb = self._load_cb, None
        if cb:
            QTimer.singleShot(500, lambda: cb(ok))

    def _load_expired(self):
        cb, self._load_cb = self._load_cb, None
        self.page.setUrl(QUrl('about:blank'))
        if cb:
            cb(False)

    def _load(self, url, cb):
        self._load_cb = cb
        self._load_timer.start(45000)
        self.page.load(QUrl(url))

    def _script(self, js, cb, timeout_ms, detail=False):
        finished = [False]
        timer = QTimer(self)
        timer.setSingleShot(True)

        def complete(state, expired=False):
            if finished[0]:
                return
            finished[0] = True
            timer.stop()
            timer.deleteLater()
            if expired:
                # Cancel the old document before allowing the next request to use this page.
                self.page.setUrl(QUrl('about:blank'))
            if detail and state.get('error') == 'timeout':
                state = dict(state, error='sent_unconfirmed')
            cb(state)

        def poll():
            if finished[0]:
                return
            def got(raw):
                if finished[0]:
                    return
                try:
                    state = json.loads(raw) if isinstance(raw, str) and raw else {}
                except (ValueError, TypeError):
                    state = {}
                if state.get('state') in ('done', 'error'):
                    complete(state)
                else:
                    QTimer.singleShot(250, poll)
            self.page.runJavaScript('JSON.stringify(window.__arAct||{})', 0, got)
        timer.timeout.connect(lambda: complete({'state': 'error', 'error': 'timeout'}, expired=True))
        timer.start(timeout_ms)
        self.page.runJavaScript(js, 0, lambda _r: QTimer.singleShot(100, poll))

    # ---- the flow
    def run(self, smr, action, reason_label, on_done, rehearsal=False):
        if self.busy:
            on_done({"ok": False, "error": "busy", "message": "یک اجرای دیگر در NBO در جریان است"})
            return
        if action not in OPTION or (action != "APPROVE" and not reason_label):
            on_done({"ok": False, "error": "bad_request", "message": "اقدام یا دلیل NBO معتبر نیست"})
            return
        self.busy = True
        attempts = {"assign": 0}
        started = time.monotonic()
        stage = ["بارگذاری فهرست"]

        def finish(result):
            self.busy = False
            code = result.get("error")
            # a rehearsal on a request not yet assigned to this account ends at Assign (it never assigns): when the button
            # the real run will press is there and enabled, everything up to that point worked
            partial = rehearsal and code == "needs_assign"
            said = str(result.get("message") or result.get("notice") or "").strip()
            if code == "nbo_refused":
                message = REFUSED + said
            elif code == "sent_unconfirmed":
                message = "نتیجهٔ مرحلهٔ تغییر وضعیت نامشخص است؛ پیش از تکرار، وضعیت را در NBO بررسی کن" + (f" (NBO: {said})" if said else "")
            else:
                message = ERRORS.get(code, "" if not code else (said or code))
            elapsed = round(time.monotonic() - started, 1)
            if code:
                message += f" — مرحله: {stage[0]}؛ زمان سپری‌شده: {elapsed} ثانیه"
            out = {"elapsed_seconds": elapsed, "stage": stage[0], "ok": not code or partial, "sent": bool(result.get("sent")), "rehearsed": bool(result.get("rehearsed")) or partial,
                   "error": code or "", "message": message, "notice": said if not code else ""}
            log.info("NBO %s %s for %s -> %s%s", "rehearsal" if rehearsal else "action", action, smr, code or "ok",
                     f" | NBO said: {said}" if said else "")
            log.info("NBO stage=%s elapsed=%.1fs outcome=%s", stage[0], elapsed, code or "ok")
            on_done(out)

        def on_list(ok):
            if not ok:
                return finish({"error": "load_failed"})
            stage[0] = "جستجوی درخواست"
            self.step.emit("جستجوی درخواست در NBO…")
            self._script(search_js(smr), on_found, 40_000)

        def on_found(r):
            if r.get("error"):
                return finish(r)
            stage[0] = "بارگذاری جزئیات"
            self.step.emit("باز کردن جزئیات درخواست…")
            self._load(r["href"], lambda ok: on_detail(ok, r["href"]))

        def on_detail(ok, href):
            if not ok:
                return finish({"error": "load_failed"})
            self.step.emit("تمرین تغییر وضعیت (بدون ثبت)…" if rehearsal else "تغییر وضعیت در NBO…")
            stage[0] = "تغییر وضعیت / تأیید پاسخ NBO"
            self._script(detail_js(smr, action, reason_label, rehearsal, allow_assign=not rehearsal and attempts["assign"] < 1),
                         lambda r: after_detail(r, href), 120_000, detail=not rehearsal)

        def after_detail(r, href):
            if r.get("assigned"):
                attempts["assign"] += 1
                self.step.emit("Assign to me انجام شد؛ بارگذاری دوباره…")
                return QTimer.singleShot(4000, lambda: self._load(href, lambda ok: on_detail(ok, href)))
            finish(r)

        self.step.emit("باز کردن فهرست ثبت‌نام‌ها…")
        self._load(self.list_url, on_list)
