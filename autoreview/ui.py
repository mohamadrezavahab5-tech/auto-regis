"""Desktop window (PySide6, Persian, right-to-left). Everything runs in the background - no browser windows are opened.

DRY-RUN: the window plans and reports. It cannot change anything in NBO or in any sheet (see guard.py / config/execution.json)."""
import json
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import backlog, crm_sync, export, imports, nbo_session, store
from .export import ACTION_FA
from .paths import app_root, config_dir, data_dir
from .pipeline import Runner, load_json

COLORS = {"APPROVE": "#e3f4e7", "EDIT": "#fff3d6", "CANCEL": "#fbe1e1", "MANUAL": "#e6e9f2"}
STATE_FA = {"idle": "آماده", "running": "در حال اجرا", "paused": "متوقف موقت", "stopping": "در حال توقف…",
            "finished": "تمام شد", "stopped": "متوقف شد", "error": "خطا"}

STYLE = """
* { font-family: 'Segoe UI', 'Tahoma'; font-size: 13px; color: #1e2433; }
QMainWindow, QDialog { background: #f3f5fa; }
QFrame#card { background: #ffffff; border: 1px solid #e3e7f0; border-radius: 14px; }
QLabel#title { font-size: 20px; font-weight: 700; }
QLabel#subtitle { color: #6b7590; }
QLabel#banner { background: #fff6dd; border: 1px solid #f0d98c; border-radius: 10px; padding: 9px 12px; color: #6b5300; }
QLabel#muted { color: #6b7590; }
QPushButton { background: #ffffff; border: 1px solid #d5daea; border-radius: 9px; padding: 8px 14px; }
QPushButton:hover { background: #eef1fb; border-color: #b9c2e6; }
QPushButton:focus { border: 2px solid #4f46e5; }
QPushButton:disabled { color: #a3abc2; background: #f3f5fa; }
QPushButton#primary { background: #4f46e5; color: #ffffff; border: none; font-weight: 600; padding: 9px 22px; }
QPushButton#primary:hover { background: #4338ca; }
QPushButton#primary:disabled { background: #c7c9f0; color: #ffffff; }
QPushButton#danger { color: #b42318; border-color: #f2c4c0; }
QSpinBox { background: #ffffff; border: 1px solid #d5daea; border-radius: 8px; padding: 5px 8px; min-width: 60px; max-width: 80px; }
QCheckBox { spacing: 8px; }
QProgressBar { background: #e6e9f4; border: none; border-radius: 6px; max-height: 12px; text-align: center; color: transparent; }
QProgressBar::chunk { background: #4f46e5; border-radius: 6px; }
QTableWidget { background: #ffffff; alternate-background-color: #f8f9fd; border: none; gridline-color: transparent; selection-background-color: #dfe3fb; selection-color: #1e2433; }
QHeaderView::section { background: #eef1f9; border: none; border-bottom: 1px solid #dde2f0; padding: 9px 10px; font-weight: 600; color: #46506b; }
QFrame#tile { border-radius: 12px; }
QLabel#tileNum { font-size: 26px; font-weight: 700; }
QLabel#tileCap { font-size: 12px; }
"""

TILES = (("APPROVE", "تایید", "#e3f4e7", "#166534"), ("EDIT", "نیاز به اصلاح", "#fff3d6", "#8a5a00"),
         ("CANCEL", "لغو", "#fbe1e1", "#9b1c1c"), ("MANUAL", "بررسی دستی", "#e6e9f6", "#3b4470"))


class Bridge(QObject):
    changed = Signal()
    crm_done = Signal(str, bool)
    nbo_done = Signal(str, bool)


class CrmLoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ورود به CRM")
        self.user, self.pw = QLineEdit(), QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        form = QFormLayout(self)
        form.addRow(QLabel("این مشخصات فقط روی همین کامپیوتر و با رمزنگاری ویندوز ذخیره می‌شود.\nاپ فقط از CRM می‌خواند و چیزی در آن نمی‌نویسد."))
        form.addRow("نام کاربری", self.user)
        form.addRow("رمز عبور", self.pw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        form.addRow(bb)


class SettingsDialog(QDialog):
    """Minimum product counts: general defaults + optional per-category numbers. Saved into config/rules.json."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("تنظیمات حداقل محصول")
        self.resize(520, 560)
        self.rules_path = config_dir() / "rules.json"
        self.rules = json.loads(self.rules_path.read_text(encoding="utf-8"))
        cats = list(load_json("category_map.json")["titles_by_nbo_category"].keys())
        mp = self.rules["min_products"]
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("حداقل تعداد محصول در نقشه‌ی سایت. خانه‌ی خالی = از مقدار پیش‌فرضِ گروه استفاده می‌شود."))
        self.defaults = {}
        form = QFormLayout()
        for key, title in (("default", "پیش‌فرض"), ("services", "خدمات"), ("education", "آموزشی")):
            sp = QSpinBox(); sp.setRange(0, 100000); sp.setValue(int(mp[key]["value"])); self.defaults[key] = sp
            form.addRow(title, sp)
        lay.addLayout(form)
        self.table = QTableWidget(len(cats), 2)
        self.table.setHorizontalHeaderLabels(["دسته‌بندی NBO", "حداقل محصول"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        values = mp.get("by_category_fa", {}).get("values", {})
        for i, c in enumerate(cats):
            it = QTableWidgetItem(c); it.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, QTableWidgetItem(str(values[c]) if c in values else ""))
        lay.addWidget(self.table)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def save(self):
        mp = self.rules["min_products"]
        for k, sp in self.defaults.items():
            mp[k]["value"] = sp.value()
        vals = {}
        for i in range(self.table.rowCount()):
            txt = (self.table.item(i, 1).text() or "").strip()
            if txt.isdigit():
                vals[self.table.item(i, 0).text()] = int(txt)
        mp.setdefault("by_category_fa", {})["values"] = vals
        self.rules_path.write_text(json.dumps(self.rules, ensure_ascii=False, indent=2), encoding="utf-8")
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoReview - بررسی خودکار ثبت‌نام آنلاین")
        self.resize(1220, 820)
        self.db_path = data_dir() / "autoreview.db"
        self.bridge = Bridge()
        self.bridge.changed.connect(self.refresh)
        self.bridge.crm_done.connect(self.on_crm_done)
        self.bridge.nbo_done.connect(self.on_nbo_done)
        self.runner = None
        self.rows_all, self.approved_nbo, self.approved_crm = [], [], []
        self.crm_note = "CRM: بارگذاری نشده (لازم است)"

        self.setStyleSheet(STYLE)
        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root); lay.setContentsMargins(22, 18, 22, 18); lay.setSpacing(14)

        head = QHBoxLayout()
        col = QVBoxLayout(); col.setSpacing(2)
        ttl = QLabel("AutoReview"); ttl.setObjectName("title")
        sub = QLabel("بررسی خودکار ثبت‌نام‌های آنلاین — پس‌زمینه، بدون باز شدن مرورگر"); sub.setObjectName("subtitle")
        col.addWidget(ttl); col.addWidget(sub)
        head.addLayout(col); head.addStretch(1)
        b_settings = QPushButton("تنظیم حداقل محصول"); b_settings.clicked.connect(self.open_settings)
        head.addWidget(b_settings)
        lay.addLayout(head)
        banner = QLabel("حالت آزمایشی: اپ فقط می‌خواند و پیشنهاد می‌دهد. هیچ تغییری در NBO یا شیت‌ها اعمال نمی‌شود.")
        banner.setObjectName("banner")
        lay.addWidget(banner)

        def card():
            f = QFrame(); f.setObjectName("card")
            v = QVBoxLayout(f); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(10)
            return f, v

        # --- sources
        c1, v1 = card()
        r1 = QHBoxLayout()
        self.nbo_label = QLabel("فایل خروجی NBO: انتخاب نشده"); self.nbo_label.setObjectName("muted")
        b_nbo = QPushButton("دریافت خودکار از NBO"); b_nbo.clicked.connect(self.fetch_nbo)
        b_nbo_login = QPushButton("ورود به NBO"); b_nbo_login.clicked.connect(self.nbo_login)
        b_nbo_file = QPushButton("فایل NBO (جایگزین)"); b_nbo_file.clicked.connect(self.pick_nbo)
        for w in (b_nbo, b_nbo_login, b_nbo_file, self.nbo_label):
            r1.addWidget(w)
        r1.addStretch(1)
        r2 = QHBoxLayout()
        self.crm_label = QLabel(self.crm_note); self.crm_label.setObjectName("muted")
        b_crm = QPushButton("دریافت خودکار از CRM"); b_crm.clicked.connect(self.fetch_crm)
        b_login = QPushButton("ورود به CRM"); b_login.clicked.connect(self.crm_login)
        b_file = QPushButton("فایل CRM (جایگزین)"); b_file.clicked.connect(self.pick_crm_file)
        for w in (b_crm, b_login, b_file, self.crm_label):
            r2.addWidget(w)
        r2.addStretch(1)
        v1.addLayout(r1); v1.addLayout(r2)
        lay.addWidget(c1)

        # --- batch + controls
        c2, v2 = card()
        opt = QHBoxLayout()
        self.size = QSpinBox(); self.size.setRange(10, 2000); self.size.setValue(int(load_json("rules.json")["backlog"]["batch_size"]))
        self.batch_no = QSpinBox(); self.batch_no.setRange(1, 1)
        for sp in (self.size, self.batch_no):
            sp.setButtonSymbols(QSpinBox.NoButtons); sp.setAlignment(Qt.AlignCenter)
        self.with_cip = QCheckBox("شامل COMMERCIAL_IN_PROGRESS")
        self.plan = QLabel(""); self.plan.setObjectName("muted")
        for w in (QLabel("اندازه‌ی دسته"), self.size, QLabel("دسته‌ی شماره"), self.batch_no, self.with_cip):
            opt.addWidget(w)
        opt.addSpacing(12); opt.addWidget(self.plan); opt.addStretch(1)
        self.size.valueChanged.connect(self.plan_batches); self.with_cip.toggled.connect(self.plan_batches)
        ctl = QHBoxLayout()
        self.b_start = QPushButton("شروع بررسی"); self.b_start.setObjectName("primary"); self.b_start.clicked.connect(self.start)
        self.b_pause = QPushButton("توقف موقت"); self.b_pause.clicked.connect(self.toggle_pause)
        self.b_stop = QPushButton("توقف"); self.b_stop.setObjectName("danger"); self.b_stop.clicked.connect(self.stop)
        self.b_export = QPushButton("خروجی Excel"); self.b_export.clicked.connect(self.export_xlsx)
        self.sources_hint = QLabel(""); self.sources_hint.setObjectName("muted")
        for w in (self.b_start, self.b_pause, self.b_stop):
            ctl.addWidget(w)
        ctl.addSpacing(12); ctl.addWidget(self.sources_hint)
        ctl.addStretch(1); ctl.addWidget(self.b_export)
        v2.addLayout(opt); v2.addLayout(ctl)
        lay.addWidget(c2)

        # --- progress + tiles
        c3, v3 = card()
        self.counts = QLabel("آماده — فایل NBO را انتخاب کن و «شروع بررسی» را بزن"); self.counts.setObjectName("muted")
        self.bar = QProgressBar(); self.bar.setTextVisible(False)
        tiles = QHBoxLayout(); tiles.setSpacing(12)
        self.tile_nums = {}
        for key, cap, bg, fg in TILES:
            tf = QFrame(); tf.setObjectName("tile"); tf.setStyleSheet("QFrame#tile{background:%s;}" % bg)
            tv = QVBoxLayout(tf); tv.setContentsMargins(14, 10, 14, 10); tv.setSpacing(0)
            num = QLabel("0"); num.setObjectName("tileNum"); num.setStyleSheet("color:%s;" % fg)
            c = QLabel(cap); c.setObjectName("tileCap"); c.setStyleSheet("color:%s;" % fg)
            num.setAlignment(Qt.AlignCenter); c.setAlignment(Qt.AlignCenter)
            tv.addWidget(num); tv.addWidget(c)
            self.tile_nums[key] = num
            tiles.addWidget(tf)
        v3.addWidget(self.counts); v3.addWidget(self.bar); v3.addLayout(tiles)
        lay.addWidget(c3)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["کد درخواست", "وب‌سایت", "دسته‌بندی", "تصمیم", "دلیل (کد NBO)", "توضیح"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((130, 220, 160, 110, 300)):
            self.table.setColumnWidth(i, w)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table, 1)
        self.status = QLabel(""); self.status.setObjectName("muted"); lay.addWidget(self.status)

        root.setFocus()
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(1500)
        self.sync_buttons()

    # ---- helpers
    def sources_missing(self):
        """NBO and CRM hold different requests, so a duplicate can hide in either: both must be loaded before a run."""
        miss = []
        if not (self.rows_all and self.approved_nbo):
            miss.append("NBO")
        if not self.approved_crm:
            miss.append("CRM")
        return miss

    def sync_buttons(self):
        st = self.runner.progress.state if self.runner else "idle"
        active = st in ("running", "paused", "stopping")
        miss = self.sources_missing()
        self.sources_hint.setText("NBO ✓ و CRM ✓ — تکراری‌ها با هر دو بررسی می‌شود" if not miss else "برای شروع هر دو منبع لازم است — کم: " + "، ".join(miss))
        self.b_start.setEnabled(not active and not miss)
        self.b_pause.setEnabled(st in ("running", "paused"))
        self.b_pause.setText("ادامه" if st == "paused" else "توقف موقت")
        self.b_stop.setEnabled(active)

    def pick_nbo(self):
        f, _ = QFileDialog.getOpenFileName(self, "خروجی NBO", "", "Excel/CSV (*.xlsx *.csv)")
        if not f:
            return
        try:
            self.rows_all = imports.read_export(f, "nbo")
        except Exception as e:
            QMessageBox.critical(self, "خطا در خواندن فایل", str(e)); return
        self.approved_nbo = [{"id": r["smr"], "site": r["site"]} for r in imports.approved_rows(self.rows_all, load_json("rules.json")["approved_statuses"]["nbo"])]
        self.nbo_label.setText(f"NBO: {Path(f).name} ({len(self.rows_all)} ردیف)")
        self.plan_batches()

    # ---- NBO (automatic)
    def nbo_login(self):
        QMessageBox.information(self, "ورود به NBO", "یک پنجره‌ی Chrome باز می‌شود. خودت نام کاربری، رمز و کد OTP را وارد کن؛ بعد از ورود خودکار بسته می‌شود.\nاپ رمز را نمی‌بیند و ذخیره نمی‌کند.")
        self.nbo_label.setText("NBO: منتظر ورود…")

        def work():
            try:
                nbo_session.login_interactive()
                self.bridge.nbo_done.emit("NBO: ورود انجام شد", True)
            except Exception as e:
                self.bridge.nbo_done.emit(f"NBO: {e}", False)
        threading.Thread(target=work, daemon=True).start()

    def fetch_nbo(self):
        if not nbo_session.have_session():
            self.nbo_login(); return
        self.nbo_label.setText("NBO: در حال دریافت خروجی…")
        rules = load_json("rules.json")
        pending = list(rules["backlog"]["statuses"]) + (list(rules["backlog"].get("optional_statuses", [])) if self.with_cip.isChecked() else [])
        approved = list(rules["approved_statuses"]["nbo"])

        def work():
            try:
                d = data_dir()
                pend_file = nbo_session.download_export(pending, d / "nbo_pending.xlsx")
                appr_file = nbo_session.download_export(approved, d / "nbo_approved.xlsx")
                self.rows_all = imports.read_export(pend_file, "nbo")
                self.approved_nbo = [{"id": r["smr"], "site": r["site"]} for r in imports.read_export(appr_file, "nbo")]
                self.bridge.nbo_done.emit(f"NBO: {len(self.rows_all)} در انتظار، {len(self.approved_nbo)} تاییدشده", True)
            except nbo_session.NboLoginRequired as e:
                self.bridge.nbo_done.emit(f"NBO: {e}", False)
            except Exception as e:
                self.bridge.nbo_done.emit(f"NBO: {e}", False)
        threading.Thread(target=work, daemon=True).start()

    def on_nbo_done(self, text, ok):
        self.nbo_label.setText(text)
        if ok:
            self.plan_batches()
        else:
            QMessageBox.warning(self, "NBO", text)

    def plan_batches(self):
        if not self.rows_all:
            return
        cfg = load_json("rules.json")["backlog"]
        picked, _ = backlog.select(self.rows_all, cfg, include_optional=self.with_cip.isChecked())
        self.batches = backlog.batches(picked, self.size.value())
        self.batch_no.setRange(1, max(1, len(self.batches)))
        self.plan.setText(f"{len(picked)} درخواست امروز، {len(self.batches)} دسته")
        self.sync_buttons()

    # ---- CRM
    def crm_login(self):
        d = CrmLoginDialog(self)
        if d.exec() == QDialog.Accepted and d.user.text() and d.pw.text():
            try:
                crm_sync.save_credentials(d.user.text().strip(), d.pw.text())
                self.crm_label.setText("CRM: ورود ذخیره شد")
            except Exception as e:
                QMessageBox.critical(self, "خطا", str(e))

    def fetch_crm(self):
        if not crm_sync.have_credentials():
            self.crm_login()
            if not crm_sync.have_credentials():
                return
        self.crm_label.setText("CRM: در حال دریافت…")

        def work():
            try:
                rows = crm_sync.fetch_rows()
                ok = imports.approved_rows(rows, load_json("rules.json")["approved_statuses"]["crm"])
                self.approved_crm = [{"id": r["smr"], "site": r["site"]} for r in ok]
                self.bridge.crm_done.emit(f"CRM: {len(rows)} ردیف، {len(ok)} تاییدشده", True)
            except Exception as e:
                self.bridge.crm_done.emit(f"CRM: {e}", False)
        threading.Thread(target=work, daemon=True).start()

    def on_crm_done(self, text, ok):
        self.crm_label.setText(text)
        self.sync_buttons()
        if not ok:
            QMessageBox.warning(self, "دریافت از CRM", text)

    def pick_crm_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "خروجی CRM", "", "Excel/CSV (*.xlsx *.csv)")
        if not f:
            return
        try:
            rows = imports.read_export(f, "crm")
        except Exception as e:
            QMessageBox.critical(self, "خطا در خواندن فایل", str(e)); return
        ok = imports.approved_rows(rows, load_json("rules.json")["approved_statuses"]["crm"])
        self.approved_crm = [{"id": r["smr"], "site": r["site"]} for r in ok]
        self.crm_label.setText(f"CRM: {Path(f).name} ({len(ok)} تاییدشده)")
        self.sync_buttons()

    def open_settings(self):
        SettingsDialog(self).exec()

    # ---- run
    def start(self):
        if not getattr(self, "batches", None):
            QMessageBox.information(self, "دسته‌ای نیست", "در فایل، درخواستی مطابق فیلتر امروز پیدا نشد."); return
        miss = self.sources_missing()
        if miss:
            QMessageBox.warning(self, "منبع ناقص", "NBO و CRM درخواست‌های متفاوتی دارند و تکراری می‌تواند در هر کدام باشد؛ هر دو لازم است.\nهنوز بارگذاری نشده: " + "، ".join(miss))
            return
        rows = self.batches[self.batch_no.value() - 1]
        self.runner = Runner(self.db_path, on_update=lambda p: self.bridge.changed.emit())
        self.runner.start(rows, self.approved_nbo, self.approved_crm)
        self.table.setRowCount(0)
        self.sync_buttons()

    def toggle_pause(self):
        if self.runner:
            self.runner.resume() if self.runner.progress.state == "paused" else self.runner.pause()
            self.sync_buttons()

    def stop(self):
        if self.runner:
            self.runner.stop()

    def refresh(self):
        if not self.runner:
            return
        p = self.runner.progress
        self.bar.setMaximum(max(p.total, 1)); self.bar.setValue(p.done)
        c = p.counts
        self.counts.setText(f"{STATE_FA.get(p.state, p.state)} — {p.done} از {p.total} درخواست بررسی شد")
        for k, lab in self.tile_nums.items():
            lab.setText(str(c.get(k, 0)))
        if p.error:
            self.status.setText("خطا: " + p.error)
        db = store.connect(self.db_path)
        res = store.results_of(db, p.run_id)
        if self.table.rowCount() != len(res):
            self.table.setRowCount(len(res))
            for i, r in enumerate(res):
                cells = [r["smr"], r["site"] or "", r["category"] or "", ACTION_FA.get(r["action"], r["action"]), "، ".join(r["reason_codes"]), " | ".join(r["notes"])]
                for j, txt in enumerate(cells):
                    it = QTableWidgetItem(txt)
                    if j == 3:
                        it.setBackground(QColor(COLORS.get(r["action"], "#ffffff"))); it.setForeground(QColor("#1e2433"))
                    self.table.setItem(i, j, it)
            self.table.scrollToBottom()
        db.close()
        self.sync_buttons()

    def export_xlsx(self):
        db = store.connect(self.db_path)
        run = self.runner.progress.run_id if self.runner else store.latest_run(db)
        res = store.results_of(db, run) if run else []
        sources = store.run_sources(db, run) if run else None
        db.close()
        if not res:
            QMessageBox.information(self, "خروجی", "هنوز نتیجه‌ای وجود ندارد."); return
        f, _ = QFileDialog.getSaveFileName(self, "ذخیره خروجی", str(app_root() / f"autoreview_{run}.xlsx"), "Excel (*.xlsx)")
        if f:
            export.write_xlsx(f, res, sources=sources)
            self.status.setText("ذخیره شد: " + f)


def main():
    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.RightToLeft)
    app.setFont(QFont("Segoe UI", 10))
    w = MainWindow(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
