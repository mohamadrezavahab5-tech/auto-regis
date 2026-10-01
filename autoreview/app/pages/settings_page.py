"""Settings: every rule the owner may adjust, grouped, with the source of each default. Saved per person (settings.json in
their profile); shared with colleagues by export/import (the same filter applies, unknown keys are refused)."""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QMessageBox, QRadioButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QWidget)

from ... import settings, updates, winsetup
from ...jalali import fa_digits
from ...version import __version__
from ...imports import status_key
from ..widgets import Card, Switch, button, label, num, toast
from .common import NBO_STATUS_FA, ScrollPage

GROUP_FA = (("normal", "عادی"), ("gold", "طلا (همیشه دستی)"), ("special", "خاص (همیشه دستی)"), ("services", "خدمات"), ("education", "آموزشی"))
NBO_APPROVED_CHOICES = ("COMMERCIAL_APPROVED", "ACTIVATING", "PENDING_ACTIVATION", "COMPLETED", "COMMERCIAL_IN_PROGRESS")
CRM_STORE_TYPES = ("آنلاین", "آنلاین - آفلاین", "آنلاین - شبکه اجتماعی", "آنلاین - آفلاین -شبکه اجتماعی", "آفلاین", "شبکه اجتماعی",
                   "آفلاین -شبکه اجتماعی")


def _radio_row(options, current):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    grp = QButtonGroup(w)
    btns = {}
    for value, text in options:
        b = QRadioButton(text)
        b.setChecked(value == current)
        grp.addButton(b)
        h.addWidget(b)
        btns[value] = b
    h.addStretch(1)
    return w, btns


def _spin(lo, hi, value, width=90):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(value))
    s.setFixedWidth(width)
    s.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return s


class SettingsPage(ScrollPage):
    title = "تنظیمات"
    subtitle = "قواعد بررسی را تنظیم کن — برای هر نفر جدا ذخیره می‌شود و قابل اشتراک با همکاران است"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell = session, shell
        self._built = False

    def on_show(self):
        while self.body.count():
            it = self.body.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._build()

    def _cur(self, dotted, file="rules"):
        return settings.current_value(file, dotted)

    def _build(self):
        rules = settings.load_rules()
        cats = list(settings.load_category_map()["titles_by_nbo_category"].keys())

        # ---- decisions
        d = Card()
        d.header("قواعد تصمیم", "وقتی یک بررسی رد می‌شود، چه تصمیمی گرفته شود")
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(12)
        g.addWidget(label("نام صاحب اینماد با صاحب حساب یکی نیست", "h3"), 0, 0)
        w, self.r_owner = _radio_row((("EDIT", "اصلاح"), ("CANCEL", "لغو"), ("MANUAL", "دستی")), self._cur("owner_mismatch_action.value"))
        g.addWidget(w, 0, 1)
        g.addWidget(label("سایت باز نمی‌شود", "h3"), 1, 0)
        w, self.r_unreach = _radio_row((("EDIT", "اصلاح (آدرس نامعتبر)"), ("MANUAL", "دستی")), self._cur("unreachable_site_action.value"))
        g.addWidget(w, 1, 1)
        g.addWidget(label("نقشه‌ی سایت (sitemap) پیدا نشد", "h3"), 2, 0)
        w, self.r_sitemap = _radio_row((("EDIT", "اصلاح"), ("MANUAL", "دستی")), self._cur("sitemap_missing_action.value"))
        g.addWidget(w, 2, 1)
        self.s_services = Switch()
        self.s_services.setChecked(bool(self._cur("services_go_manual.value")))
        g.addWidget(label("دسته‌ی خدمات همیشه دستی بررسی شود", "h3"), 3, 0)
        g.addWidget(self.s_services, 3, 1, Qt.AlignmentFlag.AlignLeft)
        self.s_mismatch = Switch()
        self.s_mismatch.setChecked(bool(self._cur("mismatch_allowed", "category_map")))
        g.addWidget(label("مغایرت دسته‌ی اینماد ← اصلاح (فقط بعد از بازبینی جدول نگاشت)", "h3"), 4, 0)
        g.addWidget(self.s_mismatch, 4, 1, Qt.AlignmentFlag.AlignLeft)
        g.setColumnStretch(2, 1)
        d.lay.addLayout(g)
        d.lay.addWidget(label("بررسی‌های قابل خاموش کردن:", "muted"))
        checks = QHBoxLayout()
        self.c_cart, self.c_seal, self.c_agree, self.c_https = Switch(), Switch(), Switch(), Switch()
        for sw, key, text in ((self.c_https, "checks.https.enabled", "سایت بدون https ← اصلاح"),
                              (self.c_cart, "checks.add_to_cart.enabled", "افزودن به سبد"),
                              (self.c_seal, "checks.enamad_on_site.enabled", "نماد اینماد روی سایت (در Action Test 4 نبود)"),
                              (self.c_agree, "checks.agreement.enabled", "قرارداد (Agreement) — قاعده‌اش هنوز تعریف نشده")):
            sw.setChecked(bool(self._cur(key)))
            checks.addWidget(sw)
            checks.addWidget(label(text, "muted"))
            checks.addSpacing(16)
        checks.addStretch(1)
        d.lay.addLayout(checks)
        self.body.addWidget(d)

        # ---- products
        p = Card()
        p.header("حداقل تعداد محصول", "از نقشه‌ی سایت شمرده می‌شود؛ خانه‌ی خالیِ جدول = مقدار گروه")
        row = QHBoxLayout()
        self.p_default = _spin(0, 100000, self._cur("min_products.default.value"))
        self.p_services = _spin(0, 100000, self._cur("min_products.services.value"))
        self.p_education = _spin(0, 100000, self._cur("min_products.education.value"))
        self.p_nocat = _spin(0, 100000, self._cur("min_products.no_category.value") or 25)
        for text, sp in (("عادی", self.p_default), ("خدمات", self.p_services), ("آموزشی", self.p_education), ("بدون دسته", self.p_nocat)):
            row.addWidget(label(text, "muted"))
            row.addWidget(sp)
            row.addSpacing(20)
        row.addStretch(1)
        p.lay.addLayout(row)
        per_cat = self._cur("min_products.by_category_fa.values") or {}
        groups = rules.get("category_groups", {})
        self.cat_table = QTableWidget(len(cats), 3)
        self.cat_table.setHorizontalHeaderLabels(("دسته‌ی NBO", "گروه", "حداقل محصول (اختیاری)"))
        self.cat_table.verticalHeader().setVisible(False)
        self.cat_table.verticalHeader().setDefaultSectionSize(38)
        self.cat_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.cat_table.setColumnWidth(1, 200)
        self.cat_table.setColumnWidth(2, 170)
        self.cat_table.setMinimumHeight(420)
        for i, cat in enumerate(cats):
            it = QTableWidgetItem(cat)
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.cat_table.setItem(i, 0, it)
            combo = QComboBox()
            for key, text in GROUP_FA:
                combo.addItem(text, key)
            current = next((gk for gk in ("gold", "special", "services", "education") if cat in groups.get(gk, [])), "normal")
            combo.setCurrentIndex(combo.findData(current))
            self.cat_table.setCellWidget(i, 1, combo)
            self.cat_table.setItem(i, 2, QTableWidgetItem(str(per_cat[cat]) if cat in per_cat else ""))
        p.lay.addWidget(self.cat_table)
        self.body.addWidget(p)

        # ---- sources
        s = Card()
        s.header("منابع و وضعیت‌ها", "کدام وضعیت‌ها «تاییدشده» حساب شوند (مرجع تکراری‌ها) و صف روزانه چه باشد")
        s.lay.addWidget(label("وضعیت‌های تاییدشده در NBO:", "muted"))
        nbo_now = set(self._cur("approved_statuses.nbo") or [])
        rown = QHBoxLayout()
        self.nbo_checks = {}
        for code in NBO_APPROVED_CHOICES:
            sw = Switch()
            sw.setChecked(code in nbo_now)
            self.nbo_checks[code] = sw
            rown.addWidget(sw)
            rown.addWidget(label(f"{NBO_STATUS_FA.get(code, code)}", "muted"))
            rown.addSpacing(12)
        rown.addStretch(1)
        s.lay.addLayout(rown)
        s.lay.addWidget(label("نوع فروشگاه در CRM که در مرجع تکراری‌ها حساب شود:", "muted"))
        st_now = {status_key(x) for x in (self._cur("crm_store_types") or [])}
        rows = QGridLayout()
        self.store_checks = {}
        for i, t in enumerate(CRM_STORE_TYPES):
            sw = Switch()
            sw.setChecked(status_key(t) in st_now)
            self.store_checks[t] = sw
            rows.addWidget(sw, i // 4, (i % 4) * 2)
            rows.addWidget(label(t, "muted"), i // 4, (i % 4) * 2 + 1)
        s.lay.addLayout(rows)
        r2 = QHBoxLayout()
        self.s_cip = Switch()
        self.s_cip.setChecked(bool(self._cur("backlog.include_optional")))
        r2.addWidget(self.s_cip)
        r2.addWidget(label("«در حال بررسی تجاری» (COMMERCIAL_IN_PROGRESS) هم در صف روزانه باشد", "muted"))
        r2.addSpacing(24)
        r2.addWidget(label("اندازه‌ی پیش‌فرض هر نوبت", "muted"))
        self.batch = _spin(10, 2000, self._cur("backlog.batch_size"))
        r2.addWidget(self.batch)
        r2.addStretch(1)
        s.lay.addLayout(r2)
        self.body.addWidget(s)

        # ---- workflow + automation
        wf = Card()
        wf.header("گردش کار و به‌روزرسانی خودکار", "فقط آنلاین: نظر Online کافی است • آنلاین + حضوری: تأیید هر دو تیم لازم است")
        r5 = QHBoxLayout()
        self.s_engine = Switch()
        self.s_engine.setChecked(bool(self._cur("workflow.engine_verdict_counts")))
        r5.addWidget(self.s_engine)
        r5.addWidget(label("نتیجه‌ی موتور (تأیید / اصلاح / لغو) خودکار نظر تیم Online حساب شود؛ «دستی» همیشه منتظر یک نفر می‌ماند", "muted"))
        r5.addStretch(1)
        wf.lay.addLayout(r5)
        wf.lay.addWidget(label("خاموش = هر درخواست را یک نفر تأیید کند. در هر دو حالت، هر کس می‌تواند از صفحه‌ی «گردش کار» نظر را عوض کند.",
                               "caption", wrap=True))
        r6 = QHBoxLayout()
        self.nbo_every = _spin(5, 240, self._cur("automation.nbo_minutes") or 15)
        self.crm_every = _spin(2, 120, self._cur("automation.crm_minutes") or 5)
        for text, w in (("گرفتن خروجی NBO هر (دقیقه)", self.nbo_every), ("به‌روزرسانی CRM هر (دقیقه)", self.crm_every)):
            r6.addWidget(label(text, "muted"))
            r6.addWidget(w)
            r6.addSpacing(16)
        r6.addStretch(1)
        wf.lay.addLayout(r6)
        r9 = QHBoxLayout()
        self.s_autopilot = Switch()
        self.s_autopilot.setChecked(self._cur("automation.autopilot") is not False)
        r9.addWidget(self.s_autopilot)
        r9.addWidget(label("خلبان خودکار: بعد از هر دریافت NBO، درخواست‌های جدید خودشان بررسی شوند", "muted"))
        r9.addStretch(1)
        wf.lay.addLayout(r9)
        r7 = QHBoxLayout()
        self.s_tray = Switch()
        self.s_tray.setChecked(self._cur("automation.keep_in_tray") is not False)
        r7.addWidget(self.s_tray)
        r7.addWidget(label("با بستن پنجره، برنامه کنار ساعت ویندوز بماند و با شیت همگام بماند", "muted"))
        r7.addSpacing(24)
        self.s_autostart = Switch()
        self.s_autostart.setEnabled(updates.can_self_update())
        self.s_autostart.setChecked(winsetup.autostart_enabled())
        self.s_autostart.toggled.connect(self._autostart)
        r7.addWidget(self.s_autostart)
        r7.addWidget(label("با روشن شدن ویندوز اجرا شود", "muted"))
        r7.addStretch(1)
        wf.lay.addLayout(r7)
        r8 = QHBoxLayout()
        r8.addWidget(label(f"نسخه‌ی نصب‌شده: {fa_digits(__version__)}", "muted"))
        b_upd = button("بررسی نسخه‌ی جدید", None, "refresh")
        b_upd.clicked.connect(lambda: self.shell.check_update(manual=True))
        r8.addWidget(b_upd)
        r8.addStretch(1)
        wf.lay.addLayout(r8)
        self.body.addWidget(wf)

        # ---- speed
        sp = Card()
        sp.header("سرعت", "بیشتر = سریع‌تر، ولی فشار بیشتر روی اینترنت و سایت‌ها")
        r3 = QHBoxLayout()
        self.conc = _spin(1, 16, self._cur("runtime.concurrency"))
        self.econc = _spin(1, 4, self._cur("runtime.enamad_concurrency"))
        self.timeout = _spin(5, 120, self._cur("runtime.http_timeout_seconds"))
        self.deadline = _spin(30, 900, self._cur("runtime.request_deadline_seconds") or 150)
        for text, w in (("بررسی هم‌زمان", self.conc), ("استعلام اینماد هم‌زمان", self.econc), ("مهلت هر صفحه (ثانیه)", self.timeout),
                        ("سقف زمان هر درخواست (ثانیه)", self.deadline)):
            r3.addWidget(label(text, "muted"))
            r3.addWidget(w)
            r3.addSpacing(16)
        r3.addStretch(1)
        sp.lay.addLayout(r3)
        self.body.addWidget(sp)

        # ---- save / share
        foot = Card(soft=True)
        r4 = QHBoxLayout()
        b_save = button("ذخیره‌ی تنظیمات", "primary", "check")
        b_save.clicked.connect(self.save)
        b_export = button("خروجی تنظیمات (برای همکار)", None, "upload")
        b_export.clicked.connect(self.export)
        b_import = button("ورود تنظیمات از فایل", None, "download")
        b_import.clicked.connect(self.import_)
        b_reset = button("بازگشت به پیش‌فرض", "danger", "refresh")
        b_reset.clicked.connect(self.reset)
        for b in (b_save, b_export, b_import):
            r4.addWidget(b)
        r4.addStretch(1)
        r4.addWidget(b_reset)
        foot.lay.addLayout(r4)
        self.body.addWidget(foot)
        self.body.addStretch(1)

    def _collect(self):
        def picked(btns):
            return next(k for k, b in btns.items() if b.isChecked())
        groups = {"gold": [], "special": [], "services": [], "education": []}
        per_cat = {}
        for i in range(self.cat_table.rowCount()):
            cat = self.cat_table.item(i, 0).text()
            g = self.cat_table.cellWidget(i, 1).currentData()
            if g in groups:
                groups[g].append(cat)
            txt = (self.cat_table.item(i, 2).text() if self.cat_table.item(i, 2) else "").strip()
            if txt:
                if not txt.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).isdigit():
                    raise ValueError(f"حداقل محصول «{cat}» باید عدد باشد")
                per_cat[cat] = int(txt.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        rules = {
            "owner_mismatch_action.value": picked(self.r_owner), "unreachable_site_action.value": picked(self.r_unreach),
            "sitemap_missing_action.value": picked(self.r_sitemap), "services_go_manual.value": self.s_services.isChecked(),
            "checks.add_to_cart.enabled": self.c_cart.isChecked(), "checks.enamad_on_site.enabled": self.c_seal.isChecked(),
            "checks.agreement.enabled": self.c_agree.isChecked(), "checks.https.enabled": self.c_https.isChecked(),
            "min_products.no_category.value": self.p_nocat.value(),
            "min_products.default.value": self.p_default.value(), "min_products.services.value": self.p_services.value(),
            "min_products.education.value": self.p_education.value(), "min_products.by_category_fa.values": per_cat,
            "category_groups.gold": groups["gold"], "category_groups.special": groups["special"],
            "category_groups.services": groups["services"], "category_groups.education": groups["education"],
            "approved_statuses.nbo": [c for c, sw in self.nbo_checks.items() if sw.isChecked()],
            "crm_store_types": [t for t, sw in self.store_checks.items() if sw.isChecked()],
            "backlog.include_optional": self.s_cip.isChecked(), "backlog.batch_size": self.batch.value(),
            "runtime.concurrency": self.conc.value(), "runtime.enamad_concurrency": self.econc.value(),
            "runtime.http_timeout_seconds": self.timeout.value(), "runtime.request_deadline_seconds": self.deadline.value(),
            "workflow.engine_verdict_counts": self.s_engine.isChecked(),
            "automation.nbo_minutes": self.nbo_every.value(), "automation.crm_minutes": self.crm_every.value(),
            "automation.keep_in_tray": self.s_tray.isChecked(), "automation.autopilot": self.s_autopilot.isChecked(),
        }
        return {"rules": rules, "category_map": {"mismatch_allowed": self.s_mismatch.isChecked()}}

    def _autostart(self, on):
        try:
            winsetup.set_autostart(Path(sys.executable), on)
        except OSError as e:
            QMessageBox.warning(self, "اجرا با ویندوز", str(e))
            return
        toast(self.window(), "با روشن شدن ویندوز، برنامه در پس‌زمینه اجرا می‌شود" if on else "اجرای خودکار با ویندوز خاموش شد")

    def save(self):
        try:
            over = self._collect()
        except ValueError as e:
            QMessageBox.warning(self, "تنظیمات", str(e))
            return
        if not over["rules"]["approved_statuses.nbo"]:
            QMessageBox.warning(self, "تنظیمات", "حداقل یک وضعیت تاییدشده‌ی NBO لازم است (مرجع تکراری‌ها).")
            return
        if over["rules"]["checks.agreement.enabled"] and QMessageBox.question(
                self, "قرارداد", "قاعده‌ی «Agreement» هنوز تعریف نشده؛ اگر روشن شود همه‌ی درخواست‌ها به بررسی دستی می‌روند. روشن بماند؟") \
                != QMessageBox.StandardButton.Yes:
            return
        rejected = settings.save_user(over)
        self.session.refresh_workflow()                    # the engine-verdict switch applies to open cases right away
        self.session.data_changed.emit()
        toast(self.window(), "تنظیمات ذخیره شد" + (f" ({num(len(rejected))} مورد نامعتبر نادیده گرفته شد)" if rejected else ""))

    def export(self):
        f, _ = QFileDialog.getSaveFileName(self, "خروجی تنظیمات", "AutoReview-settings.json", "JSON (*.json)")
        if f:
            settings.export_to(f)
            toast(self.window(), "فایل تنظیمات ذخیره شد")

    def import_(self):
        f, _ = QFileDialog.getOpenFileName(self, "ورود تنظیمات", "", "JSON (*.json)")
        if not f:
            return
        try:
            rejected = settings.import_from(f)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "ورود تنظیمات", f"فایل خوانده نشد: {e}")
            return
        self.on_show()
        self.session.data_changed.emit()
        toast(self.window(), "تنظیمات وارد شد" + (f" ({num(len(rejected))} مورد ناشناخته رد شد)" if rejected else ""))

    def reset(self):
        if QMessageBox.question(self, "بازگشت به پیش‌فرض", "همه‌ی تنظیمات شخصی پاک و مقدارهای پیش‌فرض برگردانده شود؟") \
                == QMessageBox.StandardButton.Yes:
            settings.reset()
            self.on_show()
            self.session.data_changed.emit()
            toast(self.window(), "به پیش‌فرض برگشت")
