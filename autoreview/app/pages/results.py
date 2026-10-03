"""Results: every run, every decision, and WHY - the evidence behind each one (names, enamad, sitemap, cart, contact), the
rule trace, and the request's full dated history across runs. Export to Excel / send to the sheet from here."""
import html
from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QMessageBox, QPushButton,
                               QScrollArea, QSizePolicy, QSplitter, QTableView, QVBoxLayout, QWidget)

from ... import activity, export, jalali, reference, sheets, store
from ...texts import ACTION_FA, notes_fa, reasons_fa
from .. import theme
from ..theme import C
from ..widgets import Card, EmptyState, SearchBox, action_pill, button, label, ltr, num, toast
from .common import related_card

# "نظر موتور" is only the engine's verdict; "در NBO" says whether anything was really done there, and by whom
COLS = ("کد درخواست", "وب‌سایت", "دسته‌بندی", "نظر موتور", "در NBO", "دلیل", "توضیح")


class ResultModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        r = self.rows[index.row()]
        c = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (ltr(r["smr"]), ltr(r.get("site") or ""), r.get("category") or "", ACTION_FA.get(r["action"], r["action"]),
                    r.get("nbo") or "", reasons_fa(r["reason_codes"]), notes_fa(r["notes"]))[c]
        if role == Qt.ItemDataRole.BackgroundRole and c == 3:
            return QColor(theme.ACTION.get(r["action"], (C["text2"], C["surface2"]))[1])
        if role == Qt.ItemDataRole.ForegroundRole and c == 3:
            return QColor(theme.ACTION.get(r["action"], (C["text2"], C["surface2"]))[0])
        if role == Qt.ItemDataRole.ToolTipRole and c in (4, 5, 6):
            return notes_fa(r["notes"]) if c == 6 else (reasons_fa(r["reason_codes"]) if c == 5 else r.get("nbo") or "")
        if role == Qt.ItemDataRole.UserRole:
            return r
        return None


class Filter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.actions, self.text = set(), ""

    def set(self, actions=None, text=None):
        if actions is not None:
            self.actions = actions
        if text is not None:
            self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        if self.actions and r["action"] not in self.actions:
            return False
        if self.text:
            hay = " ".join([r["smr"], r.get("site") or "", r.get("category") or "", notes_fa(r["notes"]), reasons_fa(r["reason_codes"])]).lower()
            return self.text in hay
        return True


class ResultsPage(QWidget):
    title = "نتیجه‌ی بررسی‌ها"
    subtitle = "مرحله‌ی 2 از 5: تصمیم موتور برای هر درخواست، با دلیل و شواهد — خروجی Excel و ارسال به شیت"

    def __init__(self, session, shell):
        super().__init__()
        self.setObjectName("page")
        self.session, self.shell = session, shell
        self.run_id, self.detail_row = None, None
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label("اجرا:", "h3"))
        self.runs = QComboBox()
        self.runs.setMinimumWidth(240)
        self.runs.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.runs.currentIndexChanged.connect(self._run_selected)
        top.addWidget(self.runs, 1)
        top.addStretch(1)
        b_xlsx = button("Excel همین فهرست", None, "download")
        b_xlsx.clicked.connect(self.export_xlsx)
        b_sheet = button("ارسال کل این اجرا به شیت", None, "send")
        b_sheet.clicked.connect(self.send_sheet)
        b_flow = button("نظر تیم‌ها", None, "list-check")
        b_flow.clicked.connect(lambda: self.shell.go("workflow"))
        for b in (b_xlsx, b_sheet, b_flow):
            top.addWidget(b)
        main.addLayout(top)
        bar = QHBoxLayout()
        self.chips = {}
        for key, text in (("ALL", "همه"), ("APPROVE", "تایید"), ("EDIT", "اصلاح"), ("CANCEL", "لغو"), ("MANUAL", "دستی")):
            b = QPushButton(text)
            b.setProperty("kind", "chip")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self._chip(k))
            bar.addWidget(b)
            self.chips[key] = b
        self.chips["ALL"].setChecked(True)
        bar.addSpacing(10)
        self.search = SearchBox("جستجو در کد، سایت، دلیل…")
        self.search.textChanged.connect(lambda t: self.proxy.set(text=t))
        bar.addWidget(self.search, 1)
        main.addLayout(bar)
        self.summary = label("", "muted")
        main.addWidget(self.summary)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.model, self.proxy = ResultModel(), Filter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(True)
        for i, w in enumerate((124, 170, 120, 96, 230, 200)):
            self.table.setColumnWidth(i, w)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        split.addWidget(self.table)

        self.detail = QScrollArea()
        self.detail.setWidgetResizable(True)
        self.detail.setMinimumWidth(380)
        self.detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)   # long text wraps instead
        self.detail.setFrameShape(QFrame.Shape.NoFrame)
        self.detail.setWidget(EmptyState("results", "یک درخواست را انتخاب کن", "شواهد و مسیر تصمیم اینجا نمایش داده می‌شود."))
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([820, 420])
        main.addWidget(split, 1)
        session.run_finished.connect(lambda _id: self.on_show())

    # ---- runs
    def on_show(self):
        db = self.session.db()
        try:
            runs = store.list_runs(db)
        finally:
            db.close()
        current = self.run_id
        self.runs.blockSignals(True)
        self.runs.clear()
        state_fa = {"finished": "تمام", "stopped": "متوقف", "running": "در حال اجرا", "error": "خطا", "interrupted": "قطع‌شده"}
        pick = 0
        for i, r in enumerate(runs):
            c = r["counts"]
            text = (f"{jalali.jdatetime(r['started_at'])} — {r['label'] or 'بررسی'} — {state_fa.get(r['state'], r['state'] or '')} — "
                    f"تایید {num(c['APPROVE'])} • اصلاح {num(c['EDIT'])} • لغو {num(c['CANCEL'])} • دستی {num(c['MANUAL'])}")
            self.runs.addItem(text, (r["run_id"], r["label"] or ""))
            if r["run_id"] == current:
                pick = i
        self.runs.blockSignals(False)
        if runs:
            self.runs.setCurrentIndex(pick)
            self._run_selected(pick)
        else:
            self.model.set_rows([])
            self.summary.setText("هنوز اجرایی ثبت نشده است.")

    def _run_selected(self, index):
        data = self.runs.itemData(index) if index is not None and index >= 0 else None
        if not data:
            return
        self.run_id, run_label = data
        self.detail.setWidget(EmptyState("results", "یک درخواست را انتخاب کن", "شواهد و مسیر تصمیم اینجا نمایش داده می‌شود."))
        db = self.session.db()
        try:
            rows = store.results_of(db, self.run_id)
            places = activity.nbo_places(db)
        finally:
            db.close()
        for r in rows:
            r["nbo"] = places.get(r["smr"], "در خروجی NBO نیست")
        self.model.set_rows(rows)
        c = {k: sum(1 for r in rows if r["action"] == k) for k in ("APPROVE", "EDIT", "CANCEL", "MANUAL")}
        self.summary.setText(f"{num(len(rows))} درخواست — تایید {num(c['APPROVE'])} • اصلاح {num(c['EDIT'])} • لغو {num(c['CANCEL'])} • "
                             f"دستی {num(c['MANUAL'])}")

    def _chip(self, key):
        for k, b in self.chips.items():
            b.setChecked(k == key)
        self.proxy.set(actions=set() if key == "ALL" else {key})

    # ---- detail
    def _selection_changed(self, *_):
        rows = self.table.selectionModel().selectedRows()
        if rows:
            self._row_selected(rows[0], None)

    def _row_selected(self, cur, _prev):
        if not cur.isValid():
            return
        r = self.proxy.data(cur, Qt.ItemDataRole.UserRole)
        db = self.session.db()
        try:
            d = store.result_detail(db, self.run_id, r["smr"])
            hist = store.history(db, r["smr"])
            done = r["smr"] in store.manual_done_set(db)
            self._rel = reference.related(db, r["smr"])
        finally:
            db.close()
        self.detail.setWidget(self._detail_widget(d, hist, done))

    def _detail_widget(self, d, hist, done):
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 0, 6, 12)
        v.setSpacing(12)
        head = Card()
        top = QHBoxLayout()
        top.addWidget(label(d["smr"], "h2"))
        top.addStretch(1)
        top.addWidget(action_pill(d["action"], ACTION_FA.get(d["action"], d["action"])))
        head.lay.addLayout(top)
        site = d.get("site") or ""
        head.lay.addWidget(label(ltr(html.escape(site)), "muted", selectable=True))
        if d["reason_codes"]:
            head.lay.addWidget(label("<b>دلیل NBO:</b> " + html.escape(reasons_fa(d["reason_codes"])) +
                                     f" <span style='color:{C['text3']}'>({html.escape(', '.join(d['reason_codes']))})</span>", wrap=True))
        head.lay.addWidget(label(html.escape(notes_fa(d["notes"])), "muted", wrap=True))
        btns = QHBoxLayout()
        b_copy = button("کپی کد", None, "copy")
        b_copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(d["smr"]), toast(self.window(), "کد کپی شد")))
        btns.addWidget(b_copy)
        if site:
            b_site = button("باز کردن سایت", None, "external")
            url = site if "://" in site else "https://" + site
            b_site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            btns.addWidget(b_site)
        b_nbo = button("در NBO", None, "nbo")
        b_nbo.clicked.connect(lambda: self.shell.open_in_nbo(d["smr"]))
        btns.addWidget(b_nbo)
        btns.addStretch(1)
        head.lay.addLayout(btns)
        if d["action"] == "MANUAL":
            b_done = button("برگرداندن به صف دستی" if done else "بررسی دستی انجام شد", "primary" if not done else None, "check")
            b_done.clicked.connect(lambda: self._toggle_done(d["smr"], not done))
            head.lay.addWidget(b_done)
        v.addWidget(head)
        rel = related_card(getattr(self, "_rel", None))
        if rel:
            v.addWidget(rel)

        ev = d.get("evidence") or {}
        v.addWidget(self._evidence_card(ev))
        trace = Card()
        trace.lay.addWidget(label("مسیر تصمیم", "h3"))
        for step in d.get("trace", []):
            kind, _, rest = step.partition(" ")
            color = {"PASS": C["approve"], "FAIL": C["cancel"], "UNKNOWN": C["manual"], "BLOCKED": C["warn"]}.get(kind, C["text2"])
            sym = {"PASS": "✓", "FAIL": "✗", "UNKNOWN": "؟", "BLOCKED": "!"}.get(kind, "•")
            trace.lay.addWidget(label(f"<span style='color:{color}; font-weight:700'>{sym}</span>&nbsp; {html.escape(rest or step)}", wrap=True))
        v.addWidget(trace)

        hcard = Card()
        hcard.lay.addWidget(label("سابقه‌ی این درخواست", "h3"))
        names = {"DECISION": "تصمیم", "MANUAL_DONE": "دستی انجام شد", "MANUAL_REOPENED": "دوباره به صف دستی", "DUPLICATE_FOUND": "تکراری پیدا شد"}
        for e in hist:
            when = jalali.jdatetime(e["ts"])
            if e["kind"] == "DECISION":
                text = f"{ACTION_FA.get(e['action'], e['action'])}" + (f" — {reasons_fa(e['codes'])}" if e["codes"] else "")
            else:
                text = names.get(e["kind"], e["kind"]) + (f" — {e['detail'].get('user')}" if e.get("detail", {}).get("user") else "")
            hcard.lay.addWidget(label(f"<span style='color:{C['text3']}'>{when}</span>&nbsp;&nbsp;{html.escape(text)}", wrap=True))
        v.addWidget(hcard)
        v.addStretch(1)
        return w

    def _evidence_card(self, ev):
        return evidence_card(ev)

    def _toggle_done(self, smr, done):
        db = self.session.db()
        try:
            store.set_manual_done(db, smr, done, self.session.user_label())
        finally:
            db.close()
        toast(self.window(), "از صف دستی خارج شد" if done else "به صف دستی برگشت")
        self.session.data_changed.emit()
        idx = self.table.currentIndex()
        if idx.isValid():
            self._row_selected(idx, None)

    # ---- outputs
    def export_xlsx(self):
        if not self.run_id:
            return
        db = self.session.db()
        try:
            res = [self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()] for i in range(self.proxy.rowCount())]
            sources = store.run_sources(db, self.run_id)
        finally:
            db.close()
        default = f"AutoReview_{jalali.jdate(datetime.now(), False).replace('/', '-')}_{self.run_id}.xlsx"
        f, _ = QFileDialog.getSaveFileName(self, "ذخیره‌ی خروجی Excel", default, "Excel (*.xlsx)")
        if f:
            export.write_xlsx(f, res, sources=sources)
            toast(self.window(), "خروجی Excel ذخیره شد")

    def send_sheet(self):
        if not self.run_id:
            return
        if not (sheets.load().get("webapp_url") or sheets.load().get('auth_mode') == 'service_account'):
            QMessageBox.information(self, "شیت", "اول شیت خودت را در «اتصال‌ها» وصل کن.")
            self.shell.go("connections")
            return
        self.session.send_run_to_sheet(self.run_id, lambda r: toast(self.window(), "در شیت ثبت شد" if not r.get("duplicate") else "این اجرا قبلاً در شیت بود"),
                                       lambda e: QMessageBox.warning(self, "شیت", str(e)))


def evidence_card(ev):
    """The evidence behind one review (names, enamad, sitemap, cart, contact) - shared by Results and Quick review."""
    card = Card()
    card.lay.addWidget(label("شواهد", "h3"))

    def line(k, val):
        card.lay.addWidget(label(f"<span style='color:{C['text3']}'>{k}:</span>&nbsp; {val}", wrap=True, selectable=True))
    names = ev.get("names") or {}
    if names:
        line("صاحب حساب بانکی", html.escape(names.get("account_holder") or "—"))
        line("ثبت‌کننده", html.escape(names.get("registrant") or "—"))
        line("صاحب اینماد", html.escape(names.get("enamad_owner") or "—"))
    e = ev.get("enamad") or {}
    if e:
        found = {True: "دارد", False: "ندارد", None: "نامشخص"}.get(e.get("found"))
        status = {1: "معتبر", 3: "معتبر (مجوز کسب در انتظار)", 5: "منقضی", 6: "تعلیق"}.get(e.get("status"), e.get("status"))
        line("اینماد", f"{found}" + (f" — {status}" if e.get("found") else "") + (f" — اعتبار تا {jalali.fa_digits(e['valid_until'])}" if e.get("valid_until") else ""))
        if e.get("activities"):
            line("فعالیت‌های تاییدشده‌ی اینماد", html.escape("، ".join(e["activities"])))
    p = ev.get("products") or {}
    if p:
        cnt = p.get("product_count")
        line("نقشه‌ی سایت", {True: "دارد", False: "ندارد", None: "نامشخص"}.get(p.get("has_sitemap")))
        line("تعداد محصول", (num(cnt) + ("" if p.get("complete") else " (دست‌کم)")) if cnt is not None else "نامشخص")
    f = ev.get("facts") or {}
    if f:
        yn = {True: "بله", False: "خیر", None: "نامشخص"}
        line("افزودن به سبد", yn.get(f.get("can_add_to_cart")))
        line("اطلاعات تماس", yn.get(f.get("has_contact")))
    contact = ev.get("contact") or {}
    if contact.get("phone_only"):
        if contact.get("phone"):
            line("شماره تماس روی سایت", ltr(html.escape(contact["phone"])) +
                 (f" <span style='color:{C['text3']}'>— {ltr(html.escape(contact['page']))}</span>" if contact.get("page") else ""))
        elif contact.get("found") is False:
            line("شماره تماس روی سایت", "پیدا نشد (صفحه‌ی اصلی و صفحه‌ی تماس با مرورگر هم دیده شد)")
        else:
            line("شماره تماس روی سایت", "هنوز معلوم نیست (در متن صفحه‌ها نبود)")
        line("نماد اینماد روی سایت", yn.get(f.get("enamad_shown_on_site")))
    if ev.get("duplicate_of"):
        line("تکراریِ", html.escape("، ".join(ev["duplicate_of"])))
    if ev.get("rendered"):
        line("نگاه دوم با مرورگر پنهان", f"{num(len(ev['rendered']))} صفحه")
    home = ev.get("home") or {}
    if home and not home.get("ok"):
        line("باز شدن سایت", html.escape(str(home.get("error") or home.get("status"))))
    if card.lay.count() == 1:
        card.lay.addWidget(label("شاهد جداگانه‌ای ثبت نشده (مثلاً تکراری یا مانع).", "caption"))
    return card
