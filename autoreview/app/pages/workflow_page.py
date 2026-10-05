"""The two-team workflow: for every request in the queue, the Online verdict (the engine's or a person's), the Instore verdict
for Online + Instore requests, and what NBO finally did. Everything here is mirrored into the owner's sheet every 30 seconds;
nothing here changes NBO."""
import html

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QSpinBox, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
                               QHeaderView, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter,
                               QTableView, QVBoxLayout, QWidget)

from ... import execution, jalali, reference, sheets, workflow, workspace
from ...texts import ACTION_FA, reasons_fa
from .. import theme
from ..session import run_bg
from ..theme import C
from ..widgets import Card, EmptyState, Pill, SearchBox, button, label, ltr, num, toast
from .common import related_card

COLS = ("کد درخواست", "مسیر", "وب‌سایت", "نظر Online", "نظر Instore", "وضعیت", "آخرین تغییر")
# two rows: what is still open, then what is finished (next to the search box)
CHIPS_OPEN = (("OPEN", "همه‌ی باز"), ("WAIT_ONLINE", "منتظر Online"), ("MANUAL", "دستی"), ("WAIT_INSTORE", "منتظر Instore"),
              ("CONFLICT", "اختلاف"), ("READY", "آماده‌ی تأیید"), ("EDIT", "اصلاح"), ("CANCEL", "لغو"))
CHIPS_DONE = (("DONE_APPROVED", "تأییدشده در NBO"), ("DONE_CLOSED", "بسته‌شده"), ("ALL", "همه"))
CHIPS = CHIPS_OPEN + CHIPS_DONE


def short(state):
    return workflow.STATES[state].split("؛")[0]
SOURCE_FA = {"engine": "موتور", "human": "در اپ", "sheet": "از شیت", "workspace": "همکار"}


def verdict_text(v, needed=True):
    if not v:
        return "منتظر" if needed else "لازم نیست"
    who = "موتور" if v.get("source") == "engine" else (v.get("actor") or "")
    return f"{ACTION_FA.get(v['action'], v['action'])} — {who}"


class FlowModel(QAbstractTableModel):
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
        st = r.get("state") or workflow.state(r)
        if role == Qt.ItemDataRole.DisplayRole:
            return (ltr(r["smr"]), "Online + Instore" if r["channel"] == "both" else "Online", ltr(r.get("site") or ""),
                    verdict_text(r.get("online")), verdict_text(r.get("instore"), r["channel"] == "both"),
                    short(st), jalali.ago(r.get("updated_at")))[c]
        if role == Qt.ItemDataRole.EditRole:                       # sort key
            return r.get("updated_at") or "" if c == 6 else self.data(index, Qt.ItemDataRole.DisplayRole)
        if c == 5 and role in (Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.BackgroundRole):
            fg, bg = theme.WORKFLOW.get(st, (C["text2"], C["surface2"]))
            return QColor(fg if role == Qt.ItemDataRole.ForegroundRole else bg)
        if c in (3, 4) and role == Qt.ItemDataRole.ForegroundRole:
            v = r.get("online" if c == 3 else "instore")
            if v:
                return QColor(theme.ACTION.get(v["action"], (C["text2"], ""))[0])
            return QColor(C["text3"])
        if role == Qt.ItemDataRole.ToolTipRole and c == 6:
            return jalali.jdatetime(r["updated_at"]) if r.get("updated_at") else ""
        if role == Qt.ItemDataRole.UserRole:
            return r
        return None


class FlowFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.states, self.text, self.path = set(workflow.OPEN_STATES), "", None
        self.setSortRole(Qt.ItemDataRole.EditRole)

    def set(self, states=None, text=None, path=...):
        if path is not ...:
            self.path = path                            # None = both paths, 'online', or 'both' (Online + Instore)
        if states is not None:
            self.states = states
        if text is not None:
            self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        r = self.sourceModel().rows[row]
        if self.states and (r.get("state") or workflow.state(r)) not in self.states:
            return False
        if self.path and (r.get("channel") or "online") != self.path:
            return False
        if self.text:
            return self.text in " ".join([r["smr"], r.get("site") or "", r.get("category") or ""]).lower()
        return True


class WorkflowPage(QWidget):
    title = "نظر تیم‌ها"
    subtitle = "مرحله‌ی 3 از 5: نظر Online و Instore برای هر درخواست و اینکه نوبت کیست؛ و NBO در نهایت چه کرد — هم‌زمان با شیت"

    def __init__(self, session, shell):
        super().__init__()
        self.setObjectName("page")
        self.session, self.shell = session, shell
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(12)

        top = QHBoxLayout()
        self.sync_pill = Pill("شیت")
        top.addWidget(self.sync_pill)
        self.sync_text = label("", "caption", wrap=True)
        top.addWidget(self.sync_text, 1)
        b_sync = button("همگام‌سازی الان", None, "refresh")
        b_sync.clicked.connect(lambda: session.sync_workflow(force=True))
        b_sheet = button("باز کردن شیت من", None, "sheet")
        b_sheet.clicked.connect(self._open_sheet)
        top.addWidget(b_sync)
        top.addWidget(b_sheet)
        main.addLayout(top)

        self.chips = {}

        def chip_row(items):
            row = QHBoxLayout()
            row.setSpacing(6)
            for key, text in items:
                b = QPushButton(text)
                b.setProperty("kind", "chip")
                b.setCheckable(True)
                b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)     # never squeezed into a cut-off label
                b.clicked.connect(lambda _=False, k=key: self._chip(k))
                row.addWidget(b)
                self.chips[key] = b
            return row
        # the two backlogs apart (owner 2026-10-03: "I want to see Online's backlog"): which path, then which state
        paths = QHBoxLayout()
        paths.setSpacing(8)
        paths.addWidget(label("مسیر:", "muted"))
        self.path_chips = {}
        for key, text in ((None, "هر دو"), ("online", "فقط Online"), ("both", "Online + Instore")):
            b = QPushButton(text)
            b.setProperty("kind", "chip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self._path(k))
            self.path_chips[key] = b
            paths.addWidget(b)
        self.path_chips[None].setChecked(True)
        paths.addStretch(1)
        main.addLayout(paths)
        bar = chip_row(CHIPS_OPEN)
        bar.addStretch(1)
        main.addLayout(bar)
        row2 = chip_row(CHIPS_DONE)
        row2.addSpacing(10)
        self.search = SearchBox("جستجو در کد، سایت، دسته…")
        self.search.textChanged.connect(lambda t: (self.proxy.set(text=t), self._send_summary()))
        row2.addWidget(self.search, 1)
        main.addLayout(row2)
        self.chips["OPEN"].setChecked(True)
        self.summary = label("", "muted")
        main.addWidget(self.summary)
        # Send from here too (owner 2026-10-03: "why is there no 'all / how many' on this page"): the rows the filter
        # shows - or the rows selected with Ctrl / Shift - that are ready for NBO, as many as the box says.
        send = QHBoxLayout()
        send.setSpacing(10)
        self.send_text = label("", "h3")
        send.addWidget(self.send_text, 1)
        send.addWidget(label("چند تا:", "muted"))
        self.send_limit = QSpinBox()
        self.send_limit.setRange(0, 5000)
        self.send_limit.setValue(10)
        self.send_limit.setSpecialValueText("همه")
        self.send_limit.setFixedWidth(90)
        self.send_limit.valueChanged.connect(lambda _v: self._send_summary())
        send.addWidget(self.send_limit)
        self.b_send = button("ثبت در NBO", "primary", "check")
        self.b_send.clicked.connect(self._send)
        send.addWidget(self.b_send)
        b_auto = button("ثبت خودکار…", None, "play", "کلید ثبت خودکار (Autopilot) در صفحه‌ی «ثبت در NBO» است")
        b_auto.clicked.connect(lambda: self.shell.go("execution"))
        send.addWidget(b_auto)
        main.addLayout(send)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.model, self.proxy = FlowModel(), FlowFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(6, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)   # Ctrl / Shift: several to send
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, w in enumerate((118, 132, 140, 130, 122, 150)):
            self.table.setColumnWidth(i, w)
        self.table.selectionModel().selectionChanged.connect(self._selected)
        split.addWidget(self.table)
        self.detail = QScrollArea()
        self.detail.setWidgetResizable(True)
        self.detail.setMinimumWidth(360)
        self.detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail.setFrameShape(QFrame.Shape.NoFrame)
        self._empty_detail()
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([860, 400])
        main.addWidget(split, 1)
        self.current = None
        session.data_changed.connect(self._reload_if_visible)
        session.workflow_sync_changed.connect(self.show_status)

    # ---- data
    def _reload_if_visible(self):
        if self.isVisible():
            self.on_show()

    def on_show(self):
        db = self.session.db()
        try:
            rows = workflow.cases(db)
            pending = workflow.pending_count(db)
        finally:
            db.close()
        keep = self.current["smr"] if self.current else None
        self.model.set_rows(rows)
        self._counts()
        self._pending = pending
        self.show_status()
        self._send_summary()
        if keep:
            for i in range(self.proxy.rowCount()):
                if self.proxy.data(self.proxy.index(i, 0), Qt.ItemDataRole.UserRole)["smr"] == keep:
                    self.table.selectionModel().select(self.proxy.index(i, 0), self.table.selectionModel().SelectionFlag.ClearAndSelect
                                                       | self.table.selectionModel().SelectionFlag.Rows)
                    break
        if not rows:
            self.detail.setWidget(EmptyState("list-check", "هنوز درخواستی در گردش کار نیست",
                                             "بعد از دریافت خروجی NBO، درخواست‌های صف اینجا می‌آیند و با هر بررسی نظر موتور ثبت می‌شود."))

    # ---- sending the shown / selected rows to NBO
    def _sendable(self):
        """The cases a press of «ثبت در NBO» takes: selected rows when several are selected, else every row the filter
        shows; only those ready for NBO now (complete verdicts, pending in fresh NBO data, not tried yet), oldest first."""
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if len(rows) < 2:
            rows = range(self.proxy.rowCount())
        shown = [self.proxy.data(self.proxy.index(r, 0), Qt.ItemDataRole.UserRole)["smr"] for r in rows]
        ready = self.shell.execution.ready_cases(set(ACTION_FA) - {"MANUAL"}, smrs=shown)
        return ready[:self.send_limit.value()] if self.send_limit.value() else ready

    def _send_summary(self):
        picked = len({i.row() for i in self.table.selectionModel().selectedRows()}) > 1
        cases = self._sendable()
        kinds = {}
        for c in cases:
            kinds[execution.target(c)[0]] = kinds.get(execution.target(c)[0], 0) + 1
        parts = "، ".join(f"{num(n)} {ACTION_FA[k]}" for k, n in kinds.items())
        where = "ردیف‌های انتخاب‌شده" if picked else "این فهرست"
        self.send_text.setText(f"از {where}: {num(len(cases))} درخواست آماده‌ی ثبت در NBO" + (f" ({parts})" if parts else ""))
        ex = self.shell.execution
        self.b_send.setEnabled(bool(cases) and ex.may_apply() and not ex.batch and not ex.mode.live)
        self.b_send.setText(f"ثبت {num(len(cases))} درخواست در NBO" if cases else "ثبت در NBO")

    def _send(self):
        cases = self._sendable()
        if not cases:
            return
        if QMessageBox.question(self, "ثبت در NBO",
                                f"{num(len(cases))} درخواست با حساب NBO که داخل اپ وارد شده، یکی‌یکی واقعاً در NBO ثبت می‌شود.\n"
                                "با اولین خطا می‌ایستد. شروع کنم؟") != QMessageBox.StandardButton.Yes:
            return
        try:
            n = self.shell.execution.start_batch(cases, rehearsal=False)
        except (PermissionError, ValueError) as e:
            QMessageBox.information(self, "NBO", str(e))
            return
        if n == 'busy':
            QMessageBox.information(self, "NBO", "یک ثبت دیگر در جریان است؛ اول تمام شود یا در «ثبت در NBO» توقف را بزن.")
            return
        toast(self.window(), f"ثبت {num(len(cases))} درخواست شروع شد؛ پیشرفت در نوار بالا و صفحه‌ی «ثبت در NBO»", "info")
        self._send_summary()

    def show_status(self):
        s = self.session
        cfg = sheets.load()
        pending = getattr(self, "_pending", 0)
        refresh_error = s.workflow_refresh_error
        if "workflow" in s.busy:
            self.sync_pill.set(ltr("Sheet · syncing…"), C["info"], C["info_soft"])
        elif not cfg.get("workflow_sync"):
            self.sync_pill.set(ltr("Sheet · sync off"), C["text2"], C["surface2"])
        elif s.workflow_sync_status.startswith("همگام‌سازی ناموفق"):
            self.sync_pill.set(ltr("Sheet · offline"), C["danger"], C["danger_soft"])
        elif refresh_error:
            self.sync_pill.set(ltr("Sheet · report stale"), C["danger"], C["danger_soft"])
        else:
            self.sync_pill.set(ltr("Sheet · synced"), C["approve"], C["approve_soft"])
        status = s.workflow_sync_status
        if refresh_error:
            status += f" — بازسازی گردش کار ناموفق؛ داده‌ی قبلی ممکن است قدیمی باشد: {refresh_error}"
        self.sync_text.setText(status + (f" — {num(pending)} پرونده منتظر ارسال" if pending else ""))

    def _path(self, key):
        for k, b in self.path_chips.items():
            b.setChecked(k == key)
        self.proxy.set(path=key)
        self._counts()
        self._send_summary()

    def _counts(self):
        """The numbers on the chips and in the summary line, for the chosen path."""
        rows = [r for r in self.model.rows if not self.proxy.path or (r.get("channel") or "online") == self.proxy.path]
        counts = {k: 0 for k in workflow.STATES}
        for r in rows:
            counts[r.get("state") or workflow.state(r)] += 1
        open_n = sum(counts[k] for k in workflow.OPEN_STATES)
        for key, text in CHIPS:
            n = open_n if key == "OPEN" else (len(rows) if key == "ALL" else counts.get(key, 0))
            self.chips[key].setText(f"{text}  {num(n)}")
        every = self.model.rows
        for key, b in self.path_chips.items():
            n = sum(1 for r in every if (r.get("state") or workflow.state(r)) in workflow.OPEN_STATES
                    and (key is None or (r.get("channel") or "online") == key))
            b.setText({None: "هر دو", "online": "فقط Online", "both": "Online + Instore"}[key] + f"  {num(n)} باز")
        where = {None: "", "online": " (فقط Online)", "both": " (Online + Instore)"}[self.proxy.path]
        self.summary.setText(f"{num(open_n)} درخواست باز{where} — {num(counts['READY'])} آماده‌ی تأیید، "
                             f"{num(counts['WAIT_INSTORE'])} منتظر تیم حضوری، {num(counts['DONE_APPROVED'])} تأییدشده، "
                             f"{num(counts['DONE_CLOSED'])} بسته‌شده")

    def _chip(self, key):
        for k, b in self.chips.items():
            b.setChecked(k == key)
        self.proxy.set(states=set(workflow.OPEN_STATES) if key == "OPEN" else (set() if key == "ALL" else {key}))
        self._send_summary()

    def _open_sheet(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(f"https://docs.google.com/spreadsheets/d/{sheets.load()['own_sheet_id']}/edit"))

    # ---- detail
    def _empty_detail(self):
        self.detail.setWidget(EmptyState("list-check", "یک درخواست را انتخاب کن",
                                         "نظر هر دو تیم، پیشنهاد موتور و کار بعدی اینجا نمایش داده می‌شود."))

    def _selected(self, *_):
        rows = self.table.selectionModel().selectedRows()
        self._send_summary()
        if not rows:
            return
        self.current = self.proxy.data(rows[0], Qt.ItemDataRole.UserRole)
        db = self.session.db()
        try:
            self._rel = reference.related(db, self.current["smr"])
        finally:
            db.close()
        self.detail.setWidget(self._detail(self.current))

    def _detail(self, r):
        st = r.get("state") or workflow.state(r)
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 0, 6, 12)
        v.setSpacing(12)
        head = Card()
        t = QHBoxLayout()
        t.addWidget(label(r["smr"], "h2"))
        t.addStretch(1)
        fg, bg = theme.WORKFLOW.get(st, (C["text2"], C["surface2"]))
        t.addWidget(Pill(short(st), fg, bg))
        head.lay.addLayout(t)
        head.lay.addWidget(label(ltr(html.escape(r.get("site") or "")), "muted", selectable=True))
        head.lay.addWidget(label(("Online + Instore — تأیید هر دو تیم لازم است" if r["channel"] == "both"
                                  else "Online — نظر تیم Online کافی است") +
                                 (f" • {html.escape(r.get('category') or '')}" if r.get("category") else ""), "caption", wrap=True))
        head.lay.addWidget(label(self._next_step(r, st), "h3", wrap=True))
        v.addWidget(head)
        rel = related_card(getattr(self, "_rel", None))
        if rel:
            v.addWidget(rel)

        for team, title in (("online", "تیم Online"), ("instore", "تیم Instore")):
            if team == "instore" and r["channel"] != "both":
                continue
            card = Card()
            row = QHBoxLayout()
            row.addWidget(label(title, "h3"))
            row.addStretch(1)
            verdict = r.get(team)
            if verdict:
                row.addWidget(Pill(ACTION_FA.get(verdict["action"], verdict["action"]),
                                   *theme.ACTION.get(verdict["action"], (C["text2"], C["surface2"]))))
            card.lay.addLayout(row)
            if verdict:
                src = SOURCE_FA.get(verdict.get("source"), "")
                card.lay.addWidget(label(f"{html.escape(verdict.get('actor') or '')} • {src} • "
                                         f"{jalali.jdatetime(verdict['at']) if verdict.get('at') else ''}", "caption", wrap=True))
                codes = verdict.get("reasons") or ([verdict["reason"]] if verdict.get("reason") else [])
                if codes:
                    card.lay.addWidget(label("<b>دلیل NBO:</b> " + html.escape(reasons_fa(codes)), wrap=True))
                card.lay.addWidget(label(html.escape(verdict.get("note") or ""), "muted", wrap=True))
            else:
                card.lay.addWidget(label("هنوز نظری ثبت نشده" + (f" — در تب {ltr('Online + Instore')} شیت خودت هم می‌شود ثبت کرد"
                                                                  if team == "instore" else ""), "muted", wrap=True))
            if r.get("active"):
                b = button("ثبت / تغییر نظر " + title, "primary" if not verdict else None, "check")
                b.setEnabled(team in workspace.allowed_teams(self.session.profile))
                b.setToolTip('ثبت نظر فقط برای تیم خودتان یا مدیر مجاز است')
                b.clicked.connect(lambda _=False, tm=team: self.decide(tm))
                card.lay.addWidget(b, 0, Qt.AlignmentFlag.AlignRight)
            v.addWidget(card)

        sug = r.get("suggestion")
        if sug:
            card = Card(soft=True)
            card.lay.addWidget(label("پیشنهاد موتور: " + ACTION_FA.get(sug.get("action"), sug.get("action") or "—"), "h3"))
            if sug.get("reason_codes"):
                card.lay.addWidget(label(html.escape(reasons_fa(sug["reason_codes"])), "muted", wrap=True))
            v.addWidget(card)
        # owner 2026-10-03: "it must say WHICH checks" - the same list and evidence as the request file
        from .case_view import checks_card
        from .results import evidence_card
        from ... import store
        db = self.session.db()
        try:
            result = store.latest_result(db, r["smr"])
        finally:
            db.close()
        v.addWidget(checks_card(result))
        if result:
            v.addWidget(evidence_card(result.get("evidence") or {}))
        btns = QHBoxLayout()
        tgt = execution.target(r)
        if tgt and r.get("active"):
            from .execution_page import apply_case
            b_apply = button({"APPROVE": "تأیید در NBO", "EDIT": "اصلاح در NBO", "CANCEL": "لغو در NBO"}[tgt[0]], "primary", "check")
            b_apply.clicked.connect(lambda: apply_case(self, self.shell.execution, r))
            btns.addWidget(b_apply)                     # no rehearsal button: the owner works for real only (2026-10-03)
        b_nbo = button("باز کردن در NBO", None, "nbo")
        b_nbo.clicked.connect(lambda: self.shell.open_in_nbo(r["smr"]))
        b_copy = button("کپی کد", None, "copy")
        b_copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(r["smr"]), toast(self.window(), "کد کپی شد")))
        btns.addWidget(b_nbo)
        btns.addWidget(b_copy)
        btns.addStretch(1)
        v.addLayout(btns)
        v.addWidget(label(f"وضعیت در NBO: {html.escape(r.get('source_status') or '—')} • نسخه‌ی پرونده {num(r.get('revision', 0))}",
                          "caption", wrap=True))
        v.addStretch(1)
        return w

    @staticmethod
    def _next_step(r, st):
        return {
            "WAIT_ONLINE": "کار بعدی: نظر تیم Online (یا اجرای بررسی خودکار)",
            "MANUAL": "کار بعدی: یک نفر از تیم Online بررسی و نظر ثبت کند",
            "WAIT_INSTORE": f"کار بعدی: نظر تیم Instore — در اپ، یا در تب {ltr('Online + Instore')} شیت خودت",
            "CONFLICT": "دو تیم نظر متفاوت دارند؛ یکی باید نظرش را عوض کند",
            "READY": "کار بعدی: ثبت در NBO — دکمه‌ی «تأیید در NBO» پایین همین پرونده، یا ثبت گروهی از بالای فهرست",
            "EDIT": "کار بعدی: اصلاح در NBO با همین دلیل — دکمه‌ی «اصلاح در NBO»",
            "CANCEL": "کار بعدی: لغو در NBO با همین دلیل — دکمه‌ی «لغو در NBO»",
            "DONE_APPROVED": "تمام شد — در NBO تأیید شده",
            "DONE_CLOSED": "تمام شد — در NBO بسته شده (اصلاح/لغو/…)",
        }.get(st, "خارج از صف فعال")

    # ---- verdicts
    def decide(self, team):
        if team not in workspace.allowed_teams(self.session.profile):
            QMessageBox.information(self, 'دسترسی', 'ثبت نظر این تیم برای حساب شما مجاز نیست.')
            return
        r = self.current
        if not r:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"نظر {'تیم Online' if team == 'online' else 'تیم Instore'} — {r['smr']}")
        dlg.setMinimumWidth(460)
        form = QFormLayout(dlg)
        form.setSpacing(10)
        action = QComboBox()
        for key in ("APPROVE", "EDIT", "CANCEL", "MANUAL", "REOPEN"):
            action.addItem(ACTION_FA.get(key, "") if key != "REOPEN" else "پاک کردن نظر (بازگشایی)", key)
        reason = QComboBox()
        labels = sheets.nbo_labels()

        def fill(*_):
            reason.clear()
            options = labels.get(str(action.currentData()).lower(), {})
            reason.addItem("— دلیل NBO —" if options else "دلیل لازم نیست", "")
            for code, text in options.items():
                reason.addItem(text, code)
            reason.setEnabled(bool(options))
        action.currentIndexChanged.connect(fill)
        fill()
        note = QPlainTextEdit()
        note.setPlaceholderText("توضیح یا مرجع بررسی (لازم)")
        note.setFixedHeight(90)
        form.addRow("نظر", action)
        form.addRow("دلیل", reason)
        form.addRow("توضیح", note)
        form.addRow(label(f"ثبت به نام {self.session.user_label()} — در NBO چیزی تغییر نمی‌کند.", "caption", wrap=True))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText("ثبت نظر")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف")
        form.addRow(box)
        box.rejected.connect(dlg.reject)

        def save():
            if team not in workspace.allowed_teams(self.session.profile):
                return
            choice, code, text = action.currentData(), reason.currentData() or "", note.toPlainText()
            if sheets.load().get("auth_mode") == "workspace":
                box.setEnabled(False)

                def ok(result):
                    db = self.session.db()
                    try:
                        workspace.cache_cases(db, [result["case"]])
                    finally:
                        db.close()
                    dlg.accept()
                    self.session.data_changed.emit()

                def bad(e):
                    box.setEnabled(True)
                    QMessageBox.warning(dlg, "ثبت نظر", str(e))
                run_bg(lambda _p: workspace.decide(r["smr"], team, choice, text, r["revision"], code), ok, bad)
                return
            db = self.session.db()
            try:
                workflow.decide(db, r["smr"], team, choice, self.session.user_label(), text, r["revision"], code, labels)
            except ValueError as e:
                QMessageBox.warning(dlg, "ثبت نظر", str(e))
                return
            finally:
                db.close()
            dlg.accept()
            toast(self.window(), "نظر ثبت شد")
            self.session.data_changed.emit()
            self.session.sync_workflow(force=True)
        box.accepted.connect(save)
        dlg.exec()
