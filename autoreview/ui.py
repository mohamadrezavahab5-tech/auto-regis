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

from . import backlog, crm_sync, export, imports, store
from .export import ACTION_FA
from .paths import app_root, config_dir, data_dir
from .pipeline import Runner, load_json

COLORS = {"APPROVE": "#e3f4e7", "EDIT": "#fff3d6", "CANCEL": "#fbe1e1", "MANUAL": "#e6e9f2"}
STATE_FA = {"idle": "آماده", "running": "در حال اجرا", "paused": "متوقف موقت", "stopping": "در حال توقف…",
            "finished": "تمام شد", "stopped": "متوقف شد", "error": "خطا"}


class Bridge(QObject):
    changed = Signal()
    crm_done = Signal(str, bool)


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
        self.resize(1180, 760)
        self.db_path = data_dir() / "autoreview.db"
        self.bridge = Bridge()
        self.bridge.changed.connect(self.refresh)
        self.bridge.crm_done.connect(self.on_crm_done)
        self.runner = None
        self.rows_all, self.approved_nbo, self.approved_crm = [], [], []
        self.crm_note = "CRM: بارگذاری نشده (تکراری‌های CRM بررسی نمی‌شوند)"

        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        banner = QLabel("حالت آزمایشی: اپ فقط می‌خواند و پیشنهاد می‌دهد. هیچ تغییری در NBO یا شیت‌ها اعمال نمی‌شود.")
        banner.setStyleSheet("background:#fff3d6;padding:8px;border:1px solid #e0c36b;border-radius:4px;")
        lay.addWidget(banner)

        # --- sources
        src = QHBoxLayout()
        self.nbo_label = QLabel("فایل خروجی NBO: انتخاب نشده")
        b_nbo = QPushButton("انتخاب فایل NBO…"); b_nbo.clicked.connect(self.pick_nbo)
        self.crm_label = QLabel(self.crm_note)
        b_crm = QPushButton("دریافت خودکار از CRM"); b_crm.clicked.connect(self.fetch_crm)
        b_login = QPushButton("ورود به CRM…"); b_login.clicked.connect(self.crm_login)
        b_file = QPushButton("فایل CRM (جایگزین)…"); b_file.clicked.connect(self.pick_crm_file)
        for w in (b_nbo, self.nbo_label):
            src.addWidget(w)
        src.addStretch(1)
        for w in (self.crm_label, b_login, b_crm, b_file):
            src.addWidget(w)
        lay.addLayout(src)

        # --- batch options
        opt = QHBoxLayout()
        self.size = QSpinBox(); self.size.setRange(10, 2000); self.size.setValue(int(load_json("rules.json")["backlog"]["batch_size"]))
        self.batch_no = QSpinBox(); self.batch_no.setRange(1, 1)
        self.with_cip = QCheckBox("شامل COMMERCIAL_IN_PROGRESS")
        self.plan = QLabel("")
        b_settings = QPushButton("تنظیم حداقل محصول…"); b_settings.clicked.connect(self.open_settings)
        for w in (QLabel("اندازه‌ی دسته"), self.size, QLabel("دسته‌ی شماره"), self.batch_no, self.with_cip, self.plan):
            opt.addWidget(w)
        opt.addStretch(1); opt.addWidget(b_settings)
        lay.addLayout(opt)
        self.size.valueChanged.connect(self.plan_batches); self.with_cip.toggled.connect(self.plan_batches)

        # --- controls + progress
        ctl = QHBoxLayout()
        self.b_start = QPushButton("شروع بررسی"); self.b_start.clicked.connect(self.start)
        self.b_pause = QPushButton("توقف موقت"); self.b_pause.clicked.connect(self.toggle_pause)
        self.b_stop = QPushButton("توقف"); self.b_stop.clicked.connect(self.stop)
        self.b_export = QPushButton("خروجی Excel…"); self.b_export.clicked.connect(self.export_xlsx)
        for w in (self.b_start, self.b_pause, self.b_stop, self.b_export):
            ctl.addWidget(w)
        ctl.addStretch(1)
        lay.addLayout(ctl)
        self.bar = QProgressBar(); lay.addWidget(self.bar)
        self.counts = QLabel(""); lay.addWidget(self.counts)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["کد درخواست", "وب‌سایت", "دسته‌بندی", "تصمیم", "دلیل (کد NBO)", "توضیح"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((130, 220, 160, 110, 300)):
            self.table.setColumnWidth(i, w)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        lay.addWidget(self.table, 1)
        self.status = QLabel(""); lay.addWidget(self.status)

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(1500)
        self.sync_buttons()

    # ---- helpers
    def sync_buttons(self):
        st = self.runner.progress.state if self.runner else "idle"
        active = st in ("running", "paused", "stopping")
        self.b_start.setEnabled(not active and bool(self.rows_all))
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

    def open_settings(self):
        SettingsDialog(self).exec()

    # ---- run
    def start(self):
        if not getattr(self, "batches", None):
            QMessageBox.information(self, "دسته‌ای نیست", "در فایل، درخواستی مطابق فیلتر امروز پیدا نشد."); return
        if not self.approved_crm and QMessageBox.question(self, "CRM بارگذاری نشده",
                "تکراری‌های CRM بررسی نمی‌شوند (فقط تکراری‌های NBO). ادامه بدهم؟") != QMessageBox.Yes:
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
        self.counts.setText(f"{STATE_FA.get(p.state, p.state)} — {p.done} از {p.total}   |   تایید {c['APPROVE']}   اصلاح {c['EDIT']}   لغو {c['CANCEL']}   دستی {c['MANUAL']}")
        if p.error:
            self.status.setText("خطا: " + p.error)
        db = store.connect(self.db_path)
        res = store.results_of(db, p.run_id)
        if self.table.rowCount() != len(res):
            self.table.setRowCount(len(res))
            for i, r in enumerate(res):
                cells = [r["smr"], r["site"] or "", r["category"] or "", ACTION_FA.get(r["action"], r["action"]), "، ".join(r["reason_codes"]), " | ".join(r["notes"])]
                for j, txt in enumerate(cells):
                    it = QTableWidgetItem(txt); it.setBackground(QColor(COLORS.get(r["action"], "#ffffff"))); it.setForeground(QColor("#111"))
                    self.table.setItem(i, j, it)
            self.table.scrollToBottom()
        db.close()
        self.sync_buttons()

    def export_xlsx(self):
        db = store.connect(self.db_path)
        run = self.runner.progress.run_id if self.runner else store.latest_run(db)
        res = store.results_of(db, run) if run else []
        db.close()
        if not res:
            QMessageBox.information(self, "خروجی", "هنوز نتیجه‌ای وجود ندارد."); return
        f, _ = QFileDialog.getSaveFileName(self, "ذخیره خروجی", str(app_root() / f"autoreview_{run}.xlsx"), "Excel (*.xlsx)")
        if f:
            export.write_xlsx(f, res)
            self.status.setText("ذخیره شد: " + f)


def main():
    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.RightToLeft)
    app.setFont(QFont("Segoe UI", 10))
    w = MainWindow(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
