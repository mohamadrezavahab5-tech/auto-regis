"""Review: load the data (NBO + CRM), pick a queue, run it, watch it live.

The order of the old daily routine is built in: reference data first (NBO + CRM, both required for the duplicate check),
then the day's backlog newest-first in batches, then results. Stale data asks before a run."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
                               QRadioButton, QSpinBox, QVBoxLayout)

from ... import jalali, reference, store
from ...texts import ACTION_FA, notes_fa, reasons_fa
from ..session import run_bg
from .. import theme
from ..theme import C
from ..widgets import Card, Pill, StatTile, Switch, button, label, ltr, num, toast
from .common import ACTION_COLORS, ScrollPage

STATE_FA = {"running": "در حال اجرا", "paused": "متوقف موقت", "stopping": "در حال توقف…", "finished": "تمام شد",
            "stopped": "متوقف شد", "error": "خطا", "idle": "آماده"}


class ReviewPage(ScrollPage):
    title = "بررسی"
    subtitle = "داده‌ها را به‌روز کن، صف را انتخاب کن و بررسی را اجرا کن"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell = session, shell

        # ---- sources
        src = Card()
        src.header("منابع داده", "هر دو لازم است: تکراری‌ها هم در NBO و هم در CRM بررسی می‌شوند")
        self.nbo_pill, self.nbo_text = Pill(), label("", "muted", wrap=True)
        b_nbo = button("دریافت از NBO", "primary", "download")
        b_nbo.clicked.connect(self.fetch_nbo)
        b_nbo_file = button("فایل NBO…", None, "upload", "خروجی Excel که خودت از NBO گرفته‌ای")
        b_nbo_file.clicked.connect(self.pick_nbo_file)
        src.lay.addLayout(self._source_row("NBO", self.nbo_pill, self.nbo_text, [b_nbo, b_nbo_file]))
        self.crm_pill, self.crm_text = Pill(), label("", "muted", wrap=True)
        b_crm = button("به‌روزرسانی CRM", "primary", "refresh")
        b_crm.clicked.connect(lambda: self.fetch_crm(False))
        b_crm_full = button("بارگذاری کامل", None, "download", "همه‌ی ثبت‌نام‌های CRM از اول (چند دقیقه)")
        b_crm_full.clicked.connect(lambda: self.fetch_crm(True))
        src.lay.addLayout(self._source_row("CRM", self.crm_pill, self.crm_text, [b_crm, b_crm_full]))
        self.ref_text = label("", "caption", wrap=True)
        src.lay.addWidget(self.ref_text)
        self.body.addWidget(src)
        self.automatic_status = label('', 'muted', wrap=True)
        self.body.addWidget(self.automatic_status)
        shell.automatic.changed.connect(lambda: self.automatic_status.setText(shell.automatic.status))
        self.automatic_status.setText('دریافت NBO و CRM خودکار است؛ ورود شخصی و OTP در صورت انقضا لازم می‌شود.')

        # ---- queue
        q = Card()
        q.header("صف بررسی", "از جدیدترین درخواست شروع می‌شود؛ بررسی‌شده‌ها دوباره بررسی نمی‌شوند مگر خودت بخواهی")
        self.q_all = QRadioButton()
        self.q_online = QRadioButton()
        self.q_both = QRadioButton()
        self.q_all.setChecked(True)
        grp = QButtonGroup(self)
        for rb in (self.q_all, self.q_online, self.q_both):
            grp.addButton(rb)
            q.lay.addWidget(rb)
        opts = QHBoxLayout()
        opts.setSpacing(14)
        self.only_new = Switch()
        self.only_new.setChecked(True)
        opts.addWidget(self.only_new)
        opts.addWidget(label("فقط بررسی‌نشده‌ها", "muted"))
        opts.addSpacing(20)
        opts.addWidget(label("تعداد در این نوبت", "muted"))
        self.size = QSpinBox()
        self.size.setRange(1, 2000)
        self.size.setValue(int(session.rules()["backlog"].get("batch_size", 150)))
        self.size.setFixedWidth(90)
        self.size.setAlignment(Qt.AlignmentFlag.AlignCenter)
        opts.addWidget(self.size)
        self.plan = label("", "caption")
        opts.addSpacing(10)
        opts.addWidget(self.plan, 1)
        q.lay.addLayout(opts)
        ctl = QHBoxLayout()
        self.b_start = button("شروع بررسی", "primary", "play")
        self.b_start.clicked.connect(self.start)
        self.b_pause = button("توقف موقت", None, "pause")
        self.b_pause.clicked.connect(self.toggle_pause)
        self.b_stop = button("توقف", "danger", "stop", icon_color=C["danger"])
        self.b_stop.clicked.connect(self.stop)
        self.b_again = button("دوباره بررسی «دستی»ها با قوانین فعلی", None, "refresh")
        self.b_again.setToolTip("درخواست‌های باز که آخرین نتیجه‌شان «بررسی دستی» بوده و هنوز کسی دستی تصمیم نگرفته")
        self.b_again.clicked.connect(self.review_manual_again)
        for b in (self.b_start, self.b_pause, self.b_stop):
            ctl.addWidget(b)
        ctl.addStretch(1)
        ctl.addWidget(self.b_again)
        q.lay.addLayout(ctl)
        for w in (self.q_all, self.q_online, self.q_both, self.only_new):
            w.toggled.connect(lambda *_: self._update_plan())
        self.size.valueChanged.connect(lambda *_: self._update_plan())
        self.body.addWidget(q)

        # ---- the current run
        run = Card()
        self.run_title = label("هنوز بررسی‌ای اجرا نشده", "h2")
        self.run_sub = label("", "muted")
        run.lay.addWidget(self.run_title)
        run.lay.addWidget(self.run_sub)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        run.lay.addWidget(self.progress)
        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.tiles = {k: StatTile(t, ACTION_COLORS[k]) for k, t in
                      (("APPROVE", "تایید"), ("EDIT", "نیاز به اصلاح"), ("CANCEL", "لغو"), ("MANUAL", "بررسی دستی"))}
        for t in self.tiles.values():
            tiles.addWidget(t)
        run.lay.addLayout(tiles)
        run.lay.addWidget(label("آخرین تصمیم‌ها", "h3"))
        self.feed = QListWidget()
        self.feed.setMinimumHeight(240)
        run.lay.addWidget(self.feed)
        self.body.addWidget(run)
        self.body.addStretch(1)

        self._shown_ids = set()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._live)
        self._timer.start(1000)
        session.data_changed.connect(self._reload_if_visible)
        session.run_finished.connect(self._finished)
        self._sync_buttons()

    def _source_row(self, name, pill, text, buttons):
        row = QHBoxLayout()
        row.setSpacing(12)
        head = QVBoxLayout()
        head.setSpacing(4)
        head.addWidget(label(name, "h3"))
        head.addWidget(pill, 0, Qt.AlignmentFlag.AlignRight)
        row.addLayout(head)
        row.addWidget(text, 1)
        for b in buttons:
            row.addWidget(b)
        return row

    # ---- data
    def _reload_if_visible(self):
        if self.isVisible():
            self.on_show()

    def on_show(self):
        """One load at a time: a change while one is running loads once more after it, never in parallel."""
        if getattr(self, "_loading", False):
            self._again = True
            return
        self._loading, self._again = True, False

        def work(_p):
            s = self.session
            nbo_ok, crm_ok = s.approved_sets()
            return {"board": s.board(), "nbo_ok": len(nbo_ok), "crm_ok": len(crm_ok), **s.queue_counts()}

        def done(d):
            self._loading = False
            self._show(d)
            if self._again:
                self._reload_if_visible()

        def failed(_e):
            self._loading = False
        run_bg(work, done, failed)

    def _show(self, d):
        b = d["board"]

        def show(pill, text, meta, busy, what):
            if busy:
                pill.set("در حال دریافت…", C["info"], C["info_soft"])
            elif not meta:
                pill.set("بارگذاری نشده", C["danger"], C["danger_soft"])
                text.setText(f"هنوز {what} بارگذاری نشده است.")
                return
            else:
                stale = reference.is_stale(meta)
                pill.set("قدیمی" if stale else "به‌روز", C["warn"] if stale else C["approve"], C["warn_soft"] if stale else C["approve_soft"])
            if meta:
                origin = "خودکار" if meta["origin"] == "auto" else meta["origin"].replace("file:", "فایل ")
                text.setText(f"{num(meta['rows'])} درخواست — {jalali.ago(meta['loaded_at'])} ({jalali.jdatetime(meta['loaded_at'])}) — {origin}")
        show(self.nbo_pill, self.nbo_text, b["nbo_meta"], "nbo" in self.session.busy, "خروجی NBO")
        show(self.crm_pill, self.crm_text, b["crm_meta"], "crm" in self.session.busy, "داده‌ی CRM")
        self.ref_text.setText(f"مرجع تکراری‌ها: تاییدشده‌های NBO {num(d['nbo_ok'])} • تاییدشده‌های CRM {num(d['crm_ok'])}")
        self.q_online.setText(f"Online — بررسی و تصمیم در شیت من   ({num(d['online'])} بررسی‌نشده از {num(d['online_all'])})")
        self.q_both.setText(f"Online + Instore — منتظر نظر هر دو تیم در شیت من   ({num(d['both'])} بررسی‌نشده از {num(d['both_all'])})")
        self.q_all.setText(f"هر دو با هم — اول Online + Instore (تیم Instore منتظرشان است)، بعد Online   "
                           f"({num(d['all'])} بررسی‌نشده از {num(d['all_all'])})")
        self._counts = d
        self._update_plan()

    def _update_plan(self):
        d = getattr(self, "_counts", None)
        if not d:
            return
        kind = "all" if self.q_all.isChecked() else "online" if self.q_online.isChecked() else "both"
        avail = d[kind] if self.only_new.isChecked() else d[kind + "_all"]
        n = min(self.size.value(), avail)
        self.plan.setText(f"این نوبت: {num(n)} درخواست" + ("" if avail else " — چیزی برای بررسی نمانده"))

    # ---- loading
    def fetch_nbo(self):
        if 'nbo' in self.session.busy:
            return
        client = self.shell.nbo_client
        self.session._busy('nbo', True)
        toast(self.window(), "در حال گرفتن خروجی از NBO…", "info")
        self.nbo_pill.set("در حال دریافت…", C["info"], C["info_soft"])

        def exported(data, err):
            if err == "login":
                self.session._busy('nbo', False)
                self.on_show()
                if self.shell.nbo_login():
                    self.fetch_nbo()
                return
            if err:
                self.session._busy('nbo', False)
                msg = {"network": "به NBO وصل نشد. اتصال اینترنت را بررسی کن.", "timeout": "NBO در زمان مناسب پاسخ نداد.",
                       "not_excel": "پاسخ NBO فایل Excel نبود.", "http_403": "این حساب اجازه‌ی Export در NBO را ندارد."}.get(err, f"خطا از NBO: {err}")
                self.on_show()
                QMessageBox.warning(self, "دریافت از NBO", msg)
                return
            self.session.import_nbo_bytes(data, lambda n: toast(self.window(), f"NBO: {num(n)} درخواست بارگذاری شد"),
                                          lambda e: QMessageBox.warning(self, "خروجی NBO", str(e)))
        from ..web import ALL_NBO_STATUSES
        client.export(ALL_NBO_STATUSES, exported)

    def pick_nbo_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "خروجی NBO", "", "Excel (*.xlsx);;CSV (*.csv)")
        if f:
            self.session.import_nbo_file(f, lambda n: toast(self.window(), f"NBO: {num(n)} درخواست بارگذاری شد"),
                                         lambda e: QMessageBox.warning(self, "خواندن فایل NBO", str(e)))
            self.on_show()

    def fetch_crm(self, full):
        if 'crm' in self.session.busy:
            return
        if full and QMessageBox.question(self, "بارگذاری کامل CRM", "همه‌ی ثبت‌نام‌های CRM از اول خوانده می‌شود (چند دقیقه). ادامه؟") \
                != QMessageBox.StandardButton.Yes:
            return
        self.crm_pill.set("در حال دریافت…", C["info"], C["info_soft"])

        def done(r):
            toast(self.window(), f"CRM: {num(r['rows'])} ردیف " + ("تغییر کرده خوانده شد" if r["incremental"] else "خوانده شد"))

        def failed(e):
            self.on_show()
            QMessageBox.warning(self, "CRM", str(e))
            if "ورود" in str(e) or "رد کرد" in str(e):
                self.shell.relogin()

        def progress(p):
            pages, n = p
            self.crm_text.setText(f"در حال خواندن… صفحه‌ی {num(pages)} — {num(n)} ردیف")
        self.session.refresh_crm(full, done, failed, progress)

    # ---- running
    def start(self):
        s = self.session
        kind = "all" if self.q_all.isChecked() else "online" if self.q_online.isChecked() else "both"
        board = s.board()
        if not board["nbo_meta"] or not board["crm_meta"]:
            QMessageBox.warning(self, "منبع ناقص", "اول هر دو منبع را بارگذاری کن: خروجی NBO و داده‌ی CRM.\n"
                                "تکراری‌ها در هر دو بررسی می‌شوند؛ بدون یکی، درخواست تکراری ممکن است تایید شود.")
            return
        stale = [n for n, m in (("NBO", board["nbo_meta"]), ("CRM", board["crm_meta"])) if reference.is_stale(m)]
        if stale and QMessageBox.question(self, "داده‌ی قدیمی", f"داده‌ی {' و '.join(stale)} بیش از 12 ساعت پیش گرفته شده. "
                                          "با همین داده بررسی کنم؟") != QMessageBox.StandardButton.Yes:
            return
        rows = s.queue_rows(kind, self.only_new.isChecked())[: self.size.value()]
        if not rows:
            QMessageBox.information(self, "صف خالی", "در این صف درخواستی برای بررسی نمانده است.")
            return
        label_text = {"all": "Online و Online-Instore", "online": "آنلاین"}.get(kind, "Online-Instore")
        try:
            s.start_run(rows, kind, label=f"{label_text} — {num(len(rows))}")
        except Exception as e:
            QMessageBox.warning(self, "شروع بررسی", str(e))
            return
        self.feed.clear()
        self._shown_ids = set()
        self._started = time.time()
        self._sync_buttons()

    def review_manual_again(self):
        s = self.session
        board = s.board()
        if not board["nbo_meta"] or not board["crm_meta"]:
            QMessageBox.warning(self, "منبع ناقص", "اول هر دو منبع را بارگذاری کن: خروجی NBO و داده‌ی CRM.")
            return
        self.b_again.setEnabled(False)
        run_bg(lambda _p: s.manual_again_rows(), self._manual_again_ready,
               lambda e: (self.b_again.setEnabled(True), QMessageBox.warning(self, "دوباره بررسی", str(e))))

    def _manual_again_ready(self, rows):
        s = self.session
        self.b_again.setEnabled(True)
        if not rows:
            QMessageBox.information(self, "دوباره بررسی", "درخواست بازی با نتیجه‌ی «بررسی دستی» نمانده است.")
            return
        if QMessageBox.question(self, "دوباره بررسی", f"{num(len(rows))} درخواست باز با نتیجه‌ی «بررسی دستی» دوباره با قوانین "
                                "فعلی بررسی شوند؟ (هر نوبت حداکثر به اندازه‌ی «تعداد در این نوبت»)") != QMessageBox.StandardButton.Yes:
            return
        try:
            s.start_run(rows[: self.size.value()], "all", label=f"دوباره بررسی دستی‌ها — {num(min(len(rows), self.size.value()))}")
        except Exception as e:
            QMessageBox.warning(self, "دوباره بررسی", str(e))
            return
        self.feed.clear()
        self._shown_ids = set()
        self._started = time.time()
        self._sync_buttons()

    def toggle_pause(self):
        r = self.session.runner
        if r:
            r.resume() if r.progress.state == "paused" else r.pause()
            self._sync_buttons()

    def stop(self):
        r = self.session.runner
        if r and QMessageBox.question(self, "توقف", "بررسی متوقف شود؟ درخواست‌های در حال بررسی تمام می‌شوند.") == QMessageBox.StandardButton.Yes:
            r.stop()
            self._sync_buttons()

    def _sync_buttons(self):
        r = self.session.runner
        st = r.progress.state if r else "idle"
        active = st in ("running", "paused", "stopping")
        self.b_start.setEnabled(not active)
        self.b_again.setEnabled(not active)
        self.b_pause.setEnabled(st in ("running", "paused"))
        self.b_pause.setText("ادامه" if st == "paused" else "توقف موقت")
        self.b_stop.setEnabled(st in ("running", "paused"))

    def _live(self):
        r = self.session.runner
        if not r:
            return
        p = r.progress
        self.progress.setMaximum(max(1, p.total))
        self.progress.setValue(p.done)
        eta = p.eta_seconds()
        eta_txt = f" — حدود {jalali.fa_digits(max(1, int(eta // 60)))} دقیقه مانده" if eta and eta > 30 else ""
        self.run_title.setText(f"{STATE_FA.get(p.state, p.state)}: {num(p.done)} از {num(p.total)}{eta_txt}")
        self.run_sub.setText(p.error or ("" if p.state != "paused" else "تا «ادامه» را نزنی درخواست تازه‌ای شروع نمی‌شود."))
        for k, t in self.tiles.items():
            t.set_value(p.counts.get(k, 0))
        self._sync_buttons()
        if not self.isVisible() or len(self._shown_ids) >= p.done:
            return
        db = self.session.db()
        try:
            res = store.results_of(db, p.run_id)
        finally:
            db.close()
        for x in res:
            if x["smr"] in self._shown_ids:
                continue
            self._shown_ids.add(x["smr"])
            reason = reasons_fa(x["reason_codes"]) or notes_fa(x["notes"][:1])
            item = QListWidgetItem(f"{ACTION_FA.get(x['action'], x['action'])}   •   {ltr(x['smr'])}   •   {ltr(x['site'] or '')}   •   {reason}")
            item.setForeground(QColor(C["text"]))
            item.setData(Qt.ItemDataRole.ToolTipRole, notes_fa(x["notes"]))
            item.setBackground(QColor(theme.ACTION[x["action"]][1]))
            self.feed.insertItem(0, item)
        while self.feed.count() > 200:
            self.feed.takeItem(self.feed.count() - 1)

    def _finished(self, run_id):
        self._live()
        r = self.session.runner
        c = r.progress.counts if r else {}
        toast(self.window(), f"بررسی تمام شد: تایید {num(c.get('APPROVE', 0))} • اصلاح {num(c.get('EDIT', 0))} • لغو {num(c.get('CANCEL', 0))} • "
                             f"دستی {num(c.get('MANUAL', 0))}")
        self.on_show()
        if self.session.run_kind in ("both", "all"):
            self.shell.go("workflow")                           # the Online verdicts now wait for Instore there
