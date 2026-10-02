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
}
# errors after which NBO may already have changed: never retried automatically
AFTER_SEND = {"sent_unconfirmed"}

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
            ".catch(e=>{window.__arAct={state:'error',error:'script',message:String(e)};});return true;})()")


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
  await sleep(1500);
  if ({reason}) {{
    const input = dialog.querySelector("input[id^='react-select-']");
    if (!input) return {{error: 'reason_missing'}};
    input.focus();
    setValue(input, {reason});
    let pick = null;
    for (const more = until(6000); more() && !pick; ) {{
      await sleep(250);
      pick = [...document.querySelectorAll('[id*="-option-"]')].find(o => txt(o) === {reason});
    }}
    if (!pick) return {{error: 'reason_not_selected'}};
    pick.click();
    await sleep(800);
    const chosen = [...dialog.querySelectorAll('[class*="singleValue"], [class*="multiValue"]')].map(txt);
    if (!chosen.includes({reason})) return {{error: 'reason_not_selected'}};
  }}
  const final = buttons(dialog, 'Change Status').pop();
  if (!final || final.disabled) {{
    document.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Escape', bubbles: true}}));
    return {{error: 'final_disabled'}};
  }}
  if ({json.dumps(rehearsal)}) {{
    document.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Escape', bubbles: true}}));
    return {{rehearsed: true}};
  }}
  final.click();
  for (const more = until(10000); more(); ) {{
    await sleep(250);
    const open = document.querySelector('[role="dialog"]');
    if (!open || !buttons(open, 'Change Status').length) return {{sent: true}};
  }}
  return {{error: 'sent_unconfirmed'}};
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
        self.page.loadFinished.connect(self._loaded)

    # ---- plumbing
    def _loaded(self, ok):
        cb, self._load_cb = self._load_cb, None
        if cb:
            QTimer.singleShot(1500, lambda: cb(ok))

    def _load(self, url, cb):
        self._load_cb = cb
        self.page.load(QUrl(url))

    def _script(self, js, cb, timeout_ms):
        waited = [0]

        def poll():
            def got(raw):
                state = json.loads(raw) if isinstance(raw, str) and raw else {}
                if state.get("state") in ("done", "error"):
                    cb(state)
                elif waited[0] >= timeout_ms:
                    cb({"state": "error", "error": "timeout"})
                else:
                    waited[0] += 400
                    QTimer.singleShot(400, poll)
            self.page.runJavaScript("JSON.stringify(window.__arAct||{})", 0, got)
        self.page.runJavaScript(js, 0, lambda _r: QTimer.singleShot(300, poll))

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

        def finish(result):
            self.busy = False
            code = result.get("error")
            # a rehearsal on a request not yet assigned to this account ends at Assign (it never assigns): when the button
            # the real run will press is there and enabled, everything up to that point worked
            partial = rehearsal and code == "needs_assign"
            out = {"ok": not code or partial, "sent": bool(result.get("sent")), "rehearsed": bool(result.get("rehearsed")) or partial,
                   "error": code or "", "message": ERRORS.get(code, "" if not code else
                                                             ("ثبت نهایی زده شد ولی بسته شدن پنجره دیده نشد؛ در NBO نگاه کن"
                                                              if code == "sent_unconfirmed" else str(result.get("message") or code)))}
            log.info("NBO %s %s for %s -> %s", "rehearsal" if rehearsal else "action", action, smr, code or "ok")
            on_done(out)

        def on_list(ok):
            if not ok:
                return finish({"error": "timeout"})
            self.step.emit("جستجوی درخواست در NBO…")
            self._script(search_js(smr), on_found, 40_000)

        def on_found(r):
            if r.get("error"):
                return finish(r)
            self.step.emit("باز کردن جزئیات درخواست…")
            self._load(r["href"], lambda ok: on_detail(ok, r["href"]))

        def on_detail(ok, href):
            if not ok:
                return finish({"error": "timeout"})
            self.step.emit("تمرین تغییر وضعیت (بدون ثبت)…" if rehearsal else "تغییر وضعیت در NBO…")
            self._script(detail_js(smr, action, reason_label, rehearsal, allow_assign=not rehearsal and attempts["assign"] < 1),
                         lambda r: after_detail(r, href), 120_000)

        def after_detail(r, href):
            if r.get("assigned"):
                attempts["assign"] += 1
                self.step.emit("Assign to me انجام شد؛ بارگذاری دوباره…")
                return QTimer.singleShot(4000, lambda: self._load(href, lambda ok: on_detail(ok, href)))
            finish(r)

        self.step.emit("باز کردن فهرست ثبت‌نام‌ها…")
        self._load(self.list_url, on_list)
