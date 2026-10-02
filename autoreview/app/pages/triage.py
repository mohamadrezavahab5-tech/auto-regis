"""Quick review: the requests waiting for a person, one after another. The merchant's site opens live next to the evidence and
the exact reason the engine could not decide; one key records the verdict and the next request comes up.

Keys work on the Persian keyboard too: 1 / ۱ / F1 approve, 2 / ۲ / F2 edit (pick the NBO reason), 3 / ۳ / F3 cancel,
4 / ۴ / PageDown skip, PageUp back. Nothing here changes NBO; verdicts go to the workflow and the owner's sheet."""
import html

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWebEngineCore import QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLineEdit, QMessageBox, QScrollArea, QSizePolicy, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from ... import jalali, reference, sheets, store, workflow, workspace
from ...texts import ACTION_FA, notes_fa, reasons_fa
from .. import theme
from ..session import run_bg
from ..theme import C
from ...collectors.site import UA
from ..web import Page
from ..widgets import Card, EmptyState, Pill, button, label, ltr, num, toast
from .common import related_card
from .results import evidence_card

QUEUES = (("manual", "دستی و اختلاف", ("MANUAL", "CONFLICT"), "online"),
          ("online", "منتظر نظر Online", ("WAIT_ONLINE",), "online"),
          ("instore", "منتظر نظر Instore", ("WAIT_INSTORE",), "instore"))
KEYS = {"APPROVE": ("1", "۱", "F1"), "EDIT": ("2", "۲", "F2"), "CANCEL": ("3", "۳", "F3"), "SKIP": ("4", "۴", "PgDown"),
        "BACK": ("PgUp",)}



class DesktopView(QWebEngineView):
    """Shows a merchant site laid out like a desktop window. The pane is narrower than a monitor (about 950 px next to the
    evidence), so shops took it for a phone held sideways ("hold your phone upright") or showed their mobile menu. Zooming
    out keeps the page's own width at DESKTOP_WIDTH CSS pixels, so it gets its desktop layout."""
    DESKTOP_WIDTH = 1366

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = event.size().width()
        if width > 0:
            self.setZoomFactor(max(0.5, min(1.0, width / self.DESKTOP_WIDTH)))

class TriagePage(QWidget):
    title = "بررسی سریع"
    subtitle = "درخواست‌های منتظر یک نفر، پشت سر هم — سایت زنده کنارش، با یک کلید تصمیم بگیر"

    def __init__(self, session, shell):
        super().__init__()
        self.setObjectName("page")
        self.session, self.shell = session, shell
        self.items, self.index, self.labels = [], 0, sheets.nbo_labels()
        self.team = "online"
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(10)

        top = QHBoxLayout()
        self.queue = QComboBox()
        for key, text, _states, _team in QUEUES:
            self.queue.addItem(text, key)
        self.queue.currentIndexChanged.connect(lambda _i: self.on_show())
        top.addWidget(label("صف:", "h3"))
        top.addWidget(self.queue)
        self.counter = Pill("")
        top.addWidget(self.counter)
        top.addStretch(1)
        b_prev = button("قبلی", None, "forward")
        b_prev.clicked.connect(lambda: self.move(-1))
        b_next = button("بعدی", None, "back")
        b_next.clicked.connect(lambda: self.move(1))
        top.addWidget(b_prev)
        top.addWidget(b_next)
        main.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.info = QScrollArea()
        self.info.setWidgetResizable(True)
        self.info.setFrameShape(QFrame.Shape.NoFrame)
        self.info.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.info.setMinimumWidth(380)
        split.addWidget(self.info)
        # merchant pages: off the record, no pop-ups, no downloads - only to look at
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpUserAgent(UA)                     # a desktop browser, as the owner sees the site on his PC
        self.profile.downloadRequested.connect(lambda item: item.cancel())
        self.tabs = QTabWidget()
        self.site_view, self.product_view = DesktopView(), DesktopView()
        for view in (self.site_view, self.product_view):
            view.setPage(Page(self.profile, view))
        self.tabs.addTab(self.site_view, "سایت")
        self.tabs.addTab(self.product_view, "صفحه‌ی یک محصول")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([460, 760])
        main.addWidget(split, 1)

        bar = QFrame()
        bar.setProperty("card", "true")
        b = QHBoxLayout(bar)
        b.setContentsMargins(14, 10, 14, 10)
        self.b_ok = button("تأیید  (1)", "primary", "check")
        self.b_ok.clicked.connect(lambda: self.decide("APPROVE"))
        self.b_edit = button("اصلاح  (2)", None, "alert")
        self.b_edit.clicked.connect(lambda: self.ask_reason("EDIT"))
        self.b_cancel = button("لغو  (3)", "danger", "x")
        self.b_cancel.clicked.connect(lambda: self.ask_reason("CANCEL"))
        self.b_skip = button("رد شدن  (4)", None, "back")
        self.b_skip.clicked.connect(lambda: self.move(1))
        self.reason = QComboBox()
        self.reason.setMinimumWidth(300)
        self.reason.setVisible(False)
        self.reason.activated.connect(self._reason_picked)
        self.note = QLineEdit()
        self.note.setPlaceholderText("توضیح (اختیاری)")
        for w in (self.b_ok, self.b_edit, self.b_cancel, self.reason, self.b_skip):
            b.addWidget(w)
        b.addWidget(self.note, 1)
        main.addWidget(bar)
        self._pending_action = None

        for action, keys in KEYS.items():
            for k in keys:
                sc = QShortcut(QKeySequence(k), self)
                sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                sc.activated.connect(lambda a=action: self._key(a))

    # ---- queue
    def on_show(self):
        key = self.queue.currentData()
        _k, _t, states, self.team = next(q for q in QUEUES if q[0] == key)
        db = self.session.db()
        try:
            created = {r["smr"]: r.get("created_at") for r in reference.all_nbo_rows(db)}
            items = [c for c in workflow.cases(db) if (c.get("state") or workflow.state(c)) in states]
        finally:
            db.close()
        items.sort(key=lambda c: (jalali.parse_jdate(created.get(c["smr"])) or jalali.parse_jdate("1499/01/01"), c["smr"]))
        self.created = created
        current = self.items[self.index]["smr"] if self.items and 0 <= self.index < len(self.items) else None
        self.items = items
        self.index = next((i for i, c in enumerate(items) if c["smr"] == current), 0)
        self.show_current()

    def move(self, step):
        if not self.items:
            return
        self.index = max(0, min(len(self.items) - 1, self.index + step))
        self.show_current()

    def show_current(self):
        self.reason.setVisible(False)
        self._pending_action = None
        self.note.clear()
        has = bool(self.items)
        for w in (self.b_ok, self.b_edit, self.b_cancel, self.b_skip):
            w.setEnabled(has)
        if not has:
            self.counter.set("صف خالی است", C["approve"], C["approve_soft"])
            self.info.setWidget(EmptyState("check", "چیزی در این صف نمانده", "همه‌ی درخواست‌های این صف تصمیم گرفته شده‌اند."))
            self.site_view.setHtml("")
            self.product_view.setHtml("")
            return
        c = self.items[self.index]
        self.counter.set(f"{num(self.index + 1)} از {num(len(self.items))}", C["accent_text"], C["accent_soft"])
        db = self.session.db()
        try:
            res = store.latest_result(db, c["smr"])
            self.rel = reference.related(db, c["smr"])
        finally:
            db.close()
        self.info.setWidget(self._info(c, res))
        ev = (res or {}).get("evidence") or {}
        site = ev.get("home_url") or c.get("site") or ""
        url = site if "://" in site else "https://" + site
        self.site_view.setUrl(QUrl(url))
        samples = ev.get("samples") or []
        if samples:
            self.product_view.setUrl(QUrl(samples[0]))
        else:
            self.product_view.setHtml(f"<p dir='rtl' style='font-family:Vazirmatn,Segoe UI;color:{C['text3']}'>صفحه‌ی محصولی پیدا نشده بود.</p>")
        self.tabs.setCurrentIndex(0)
        self.setFocus()

    def _info(self, c, res):
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 0, 4, 8)
        v.setSpacing(10)
        head = Card()
        row = QHBoxLayout()
        row.addWidget(label(c["smr"], "h2"))
        row.addStretch(1)
        st = c.get("state") or workflow.state(c)
        row.addWidget(Pill(workflow.STATES[st].split("؛")[0], *theme.WORKFLOW.get(st, (C["text2"], C["surface2"]))))
        head.lay.addLayout(row)
        head.lay.addWidget(label(ltr(html.escape(c.get("site") or "")), "muted", selectable=True))
        created = self.created.get(c["smr"])
        head.lay.addWidget(label(" • ".join(x for x in (
            "Online + Instore" if c["channel"] == "both" else "Online", html.escape(c.get("category") or ""),
            f"ثبت در NBO: {jalali.fa_digits(created)}" if created else "") if x), "caption", wrap=True))
        b_open = button("باز کردن در مرورگر", None, "external")
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(self.site_view.url()))
        head.lay.addWidget(b_open, 0, Qt.AlignmentFlag.AlignRight)
        v.addWidget(head)

        why = Card(soft=True)
        sug = c.get("suggestion") or {}
        if res:
            why.lay.addWidget(label("موتور: " + ACTION_FA.get(res["action"], res["action"]) +
                                    (f" — {reasons_fa(res['reason_codes'])}" if res.get("reason_codes") else ""), "h3", wrap=True))
            text = notes_fa(res.get("notes"))
            if text:
                why.lay.addWidget(label(html.escape(text), "muted", wrap=True))
        elif sug:
            why.lay.addWidget(label("موتور: " + ACTION_FA.get(sug.get("action"), "—"), "h3"))
        else:
            why.lay.addWidget(label("موتور هنوز این درخواست را بررسی نکرده.", "muted", wrap=True))
        for team, title in (("online", "Online"), ("instore", "Instore")):
            verdict = c.get(team)
            if verdict:
                why.lay.addWidget(label(f"نظر {title}: {ACTION_FA.get(verdict['action'], verdict['action'])} — "
                                        f"{html.escape(verdict.get('actor') or '')}", "caption", wrap=True))
        v.addWidget(why)
        rel = related_card(getattr(self, "rel", None))
        if rel:
            v.addWidget(rel)
        if res and res.get("evidence"):
            v.addWidget(evidence_card(res["evidence"]))
        v.addStretch(1)
        return w

    # ---- verdicts
    def _key(self, action):
        if not self.items or self.reason.view().isVisible():
            return
        if action == "SKIP":
            self.move(1)
        elif action == "BACK":
            self.move(-1)
        elif action == "APPROVE":
            self.decide("APPROVE")
        else:
            self.ask_reason(action)

    def ask_reason(self, action):
        if not self.items:
            return
        self._pending_action = action
        self.reason.clear()
        self.reason.addItem("— دلیل " + ("اصلاح" if action == "EDIT" else "لغو") + " را انتخاب کن —", "")
        for code, text in self.labels.get(action.lower(), {}).items():
            self.reason.addItem(text, code)
        self.reason.setVisible(True)
        self.reason.setFocus()
        self.reason.showPopup()

    def _reason_picked(self, index):
        code = self.reason.itemData(index)
        if code and self._pending_action:
            self.decide(self._pending_action, code)

    def decide(self, action, reason=""):
        if not self.items:
            return
        c = self.items[self.index]
        note = self.note.text().strip() or "بررسی سریع"
        if sheets.load().get("auth_mode") == "workspace":
            def ok(result):
                db = self.session.db()
                try:
                    workspace.cache_cases(db, [result["case"]])
                finally:
                    db.close()
                self._after(c, action)
            run_bg(lambda _p: workspace.decide(c["smr"], self.team, action, note, c["revision"], reason), ok,
                   lambda e: QMessageBox.warning(self, "ثبت نظر", str(e)))
            return
        db = self.session.db()
        try:
            fresh = workflow.get(db, c["smr"])
            workflow.decide(db, c["smr"], self.team, action, self.session.user_label(), note,
                            fresh["revision"] if fresh else c["revision"], reason, self.labels)
        except ValueError as e:
            QMessageBox.warning(self, "ثبت نظر", str(e))
            return
        finally:
            db.close()
        self._after(c, action)

    def _after(self, case, action):
        toast(self.window(), f"{case['smr']}: {ACTION_FA.get(action, action)} ثبت شد")
        del self.items[self.index]
        if self.index >= len(self.items):
            self.index = max(0, len(self.items) - 1)
        self.show_current()
        self.session.data_changed.emit()
        self.session.sync_workflow()
