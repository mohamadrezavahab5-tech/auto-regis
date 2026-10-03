"""NBO inside the app: the real NBO pages (the person's own login, OTP typed by them), with an AutoReview panel beside it that
reviews the requests visible on the current NBO page and suggests a decision for each. Applying anything in NBO stays off
(dry-run) - when it is switched on, every change will need the person's confirmation."""
import json

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout, QWidget

from ... import reference, store, workflow
from ...texts import ACTION_FA, reasons_fa
from .. import theme
from ..theme import C
from ..web import LOGGED_IN_JS, NBO_REGISTRATIONS, Page, nbo_profile
from ..widgets import Card, Pill, button, label, num, toast

# Every request ID written anywhere on the page. It used to look only at childless td / span / a / div / p elements with
# exactly the ID as their text, so a cell holding the ID next to an icon (or inside another tag) was missed and the panel
# said "no request on this page" with the list in plain sight (owner 2026-10-03). Now every text node is read.
# The result travels as JSON text: this Qt hands a JavaScript array back as an empty value, so the panel found nothing.
SCAN_JS = r"""(function () {
  var seen = {}, out = [], re = /SMR-\d{5,}/g;
  function take(text) {
    var m = (text || '').match(re);
    for (var k = 0; m && k < m.length; k++) if (!seen[m[k]]) { seen[m[k]] = 1; out.push(m[k]); }
  }
  var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null), node, n = 0;
  while ((node = walker.nextNode()) && n++ < 200000) take(node.nodeValue);
  if (!out.length) take(document.body.innerText);
  return JSON.stringify(out);
})()"""
def browser_toolbar(view, home_url):
    bar = QHBoxLayout()
    bar.setSpacing(6)
    b_back = button("", "ghost", "back", "صفحه‌ی قبل")
    b_back.clicked.connect(view.back)
    b_fwd = button("", "ghost", "forward", "صفحه‌ی بعد")
    b_fwd.clicked.connect(view.forward)
    b_reload = button("", "ghost", "refresh", "بارگذاری دوباره")
    b_reload.clicked.connect(view.reload)
    b_home = button("ثبت‌نام‌ها", "ghost", "home")
    b_home.clicked.connect(lambda: view.load(QUrl(home_url)))
    for b in (b_back, b_fwd, b_reload, b_home):
        bar.addWidget(b)
    return bar


class NboPage(QWidget):
    title = "NBO"
    subtitle = "خود NBO، با ورود خودت — پنل کناری تصمیم پیشنهادی هر درخواست این صفحه را نشان می‌دهد"

    def __init__(self, session, shell):
        super().__init__()
        self.setObjectName("page")
        self.session, self.shell = session, shell
        self._loaded = False
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(8)
        self.view = QWebEngineView()
        self.view.setPage(Page(nbo_profile(), self.view))
        top = browser_toolbar(self.view, NBO_REGISTRATIONS)
        top.addStretch(1)
        self.state = Pill("")
        top.addWidget(self.state)
        left.addLayout(top)
        frame = QFrame()
        frame.setProperty("card", "true")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(1, 1, 1, 1)
        fl.addWidget(self.view)
        left.addWidget(frame, 1)
        h.addLayout(left, 1)

        panel = Card(padding=14)
        panel.setFixedWidth(285)
        panel.lay.addWidget(label("AutoReview", "h2"))
        panel.lay.addWidget(label("این پنل درخواست‌های روی صفحه را بررسی می‌کند. برای ثبت نتیجه در NBO، صفحه‌ی «ثبت در NBO» را باز کن.",
                                  "caption", wrap=True))
        b_scan = button("بررسی درخواست‌های این صفحه", "primary", "review")
        b_scan.clicked.connect(self.review_page)
        panel.lay.addWidget(b_scan)
        self.panel_info = label("", "muted", wrap=True)
        panel.lay.addWidget(self.panel_info)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda it: (QGuiApplication.clipboard().setText(it.data(Qt.ItemDataRole.UserRole)),
                                                        toast(self.window(), "کد کپی شد")))
        panel.lay.addWidget(self.list, 1)
        b_apply = button("رفتن به ثبت در NBO", "primary", "check", "انتخاب درخواست‌ها و ثبت در NBO")
        b_apply.clicked.connect(lambda: shell.go('execution'))
        panel.lay.addWidget(b_apply)
        h.addWidget(panel)
        panel.hide()
        toggle = button('پنل بررسی', None, 'review', 'نمایش یا پنهان کردن پنل بررسی کنار NBO')
        toggle.setCheckable(True)
        toggle.toggled.connect(panel.setVisible)
        top.addWidget(toggle)
        apply_shortcut = button('صف اعمال', 'primary', 'check')
        apply_shortcut.clicked.connect(lambda: shell.go('execution'))
        top.addWidget(apply_shortcut)

        self.view.urlChanged.connect(lambda _u: self._check_login())
        session.run_finished.connect(lambda _id: self._show_page_results())
        self._smrs = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check_login)
        self._timer.start(5000)

    def on_show(self):
        if not self._loaded:
            self._loaded = True
            self.view.load(QUrl(NBO_REGISTRATIONS))
        self._check_login()

    def open_smr(self, smr):
        QGuiApplication.clipboard().setText(smr)
        if not self._loaded:
            self._loaded = True
            self.view.load(QUrl(NBO_REGISTRATIONS))
        toast(self.window(), f"{smr} کپی شد — در کادر «Request ID» بچسبان و Search بزن", "info")

    def _check_login(self):
        if not self.isVisible():
            return
        self.view.page().runJavaScript(LOGGED_IN_JS, 0, self._login_state)

    def _login_state(self, ok):
        if ok:
            self.state.set("وارد شده", C["approve"], C["approve_soft"])
        else:
            self.state.set("وارد نشده — همین‌جا وارد شو", C["warn"], C["warn_soft"])

    # ---- AutoReview panel
    def review_page(self):
        self.view.page().runJavaScript(SCAN_JS, 0, self._scanned)

    def _scanned(self, smrs):
        try:
            smrs = list(json.loads(smrs)) if isinstance(smrs, str) and smrs else list(smrs or [])
        except ValueError:
            smrs = []
        if not smrs:
            QMessageBox.information(self, "AutoReview", "روی این صفحه کد درخواستی (SMR-…) پیدا نشد. فهرست ثبت‌نام‌ها را باز کن.")
            return
        db = self.session.db()
        try:
            known = {r["smr"]: r for r in reference.all_nbo_rows(db)}
        finally:
            db.close()
        rules = self.session.rules()
        ok_status = set(rules["backlog"]["statuses"]) | set(rules["backlog"].get("optional_statuses", []))
        rows = [known[s] for s in smrs if s in known and known[s]["status"] in ok_status and str(known[s]["has_online"]).lower() == "true"]
        missing = [s for s in smrs if s not in known]
        other = [s for s in smrs if s in known and s not in {r["smr"] for r in rows}]
        self._smrs = smrs
        info = f"{num(len(smrs))} درخواست روی صفحه؛ {num(len(rows))} قابل بررسی."
        if missing:
            info += f" {num(len(missing))} در آخرین خروجی NBO نیست (اول «دریافت از NBO»)."
        if other:
            info += f" {num(len(other))} در وضعیتی است که بررسی نمی‌شود."
        self.panel_info.setText(info)
        if not rows:
            self._show_page_results()
            return
        if self.session.runner and self.session.runner.is_active():
            QMessageBox.information(self, "AutoReview", "یک بررسی دیگر در حال اجراست؛ بعد از تمام شدنش دوباره بزن.")
            return
        try:
            self.session.start_run(rows, "page", label=f"صفحه‌ی NBO — {num(len(rows))}")
            toast(self.window(), f"بررسی {num(len(rows))} درخواست شروع شد", "info")
        except Exception as e:
            QMessageBox.warning(self, "AutoReview", str(e))

    def _show_page_results(self):
        if not self._smrs:
            return
        db = self.session.db()
        try:
            latest = workflow.current_reviews(db, store.latest_states(db))
        finally:
            db.close()
        self.list.clear()
        for s in self._smrs:
            st = latest.get(s)
            if st:
                action, codes = st[0], st[1]
                text = f"{s}\n{ACTION_FA.get(action, action)}" + (f" — {reasons_fa(codes)}" if codes else "")
                it = QListWidgetItem(text)
                it.setBackground(QColor(theme.ACTION[action][1]))
            else:
                it = QListWidgetItem(f"{s}\nهنوز بررسی نشده")
            it.setForeground(QColor(C["text"]))
            it.setData(Qt.ItemDataRole.UserRole, s)
            self.list.addItem(it)
