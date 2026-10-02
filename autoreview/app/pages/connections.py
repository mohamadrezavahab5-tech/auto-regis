"""Connections: prove every link works, step by step, with real numbers - CRM, NBO (including a real export whose column
headers are verified), the person's Google Sheet (guided setup) and the shared Online-Instore sheet (structure + dropdown
values checked before anything may be written), enamad and plain websites."""
import asyncio
import os
import re
import tempfile

import httpx
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from ... import crm_sync, imports, sheets, google_credentials, workspace
from ...collectors import enamad as enamad_mod
from ...collectors import site as sitec
from ...paths import logs_dir, user_dir
from ..session import run_bg
from ..widgets import Card, StepList, Switch, button, label, num, toast
from .common import ScrollPage


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


SHEET_STEPS = ("1. شیت خودت را باز کن (همان «AutoReview - Results») و از منوی «Extensions» گزینه‌ی «Apps Script» را بزن.\n"
               "2. از اسکریپت قبلی نسخه پشتیبان بگیر؛ کد AutoReview را با «کپی کد اسکریپت» به نسخه جدید به‌روز کن و Save بزن.\n"
               "3. دکمه‌ی «Deploy» و بعد «New deployment» را بزن و نوع را «Web app» بگذار.\n"
               "4. در «Execute as» گزینه‌ی «Me» و در «Who has access» گزینه‌ی «Anyone» را انتخاب کن و «Deploy» را بزن.\n"
               "5. گوگل اجازه می‌خواهد: حساب خودت را انتخاب کن و «Allow» را بزن (اسکریپت مال خودت است).\n"
               "6. «Web app URL» را کپی کن، در کادر زیر بچسبان و «تست» را بزن.")


class ConnectionsPage(ScrollPage):
    title = "اتصال‌ها"
    subtitle = "هر اتصال را مرحله‌به‌مرحله و با عدد واقعی تست کن"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell = session, shell

        # ---- CRM
        crm = Card()
        b_crm = button("تست اتصال CRM", "primary", "refresh")
        b_crm.clicked.connect(self.test_crm)
        b_relogin = button("ورود دوباره", None, "user")
        b_relogin.clicked.connect(shell.relogin)
        crm.header("CRM", "crm.snapppay.ir — فقط خواندن، با حساب خودت", [b_crm, b_relogin])
        self.crm_steps = StepList()
        crm.lay.addWidget(self.crm_steps)
        self.body.addWidget(crm)

        # ---- NBO
        nbo = Card()
        b_login = button("ورود به NBO", None, "user")
        b_login.clicked.connect(lambda: shell.nbo_login() and self.test_nbo())
        b_nbo = button("تست اتصال", "primary", "refresh")
        b_nbo.clicked.connect(self.test_nbo)
        b_full = button("تست کامل خروجی", None, "download", "یک خروجی واقعی (فقط PENDING) می‌گیرد و ستون‌هایش را با ساختار مورد انتظار مقایسه می‌کند")
        b_full.clicked.connect(self.test_nbo_export)
        nbo.header("NBO", "nbo.snapppay.ir — ورود با حساب و OTP خودت، داخل همین برنامه", [b_login, b_nbo, b_full])
        self.nbo_steps = StepList()
        nbo.lay.addWidget(self.nbo_steps)
        self.body.addWidget(nbo)

        # ---- own Google Sheet: the only sheet the app touches
        sh = Card()
        b_open = button("باز کردن شیت من", None, "external")
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://docs.google.com/spreadsheets/d/" + sheets.load()["own_sheet_id"] + "/edit")))
        sh.header("شیت خودت — AutoReview - Results", "تنها شیتی که اپ به آن دست می‌زند؛ شیت‌های تیم‌ها نه خوانده می‌شوند نه نوشته", b_open)
        self.owner_box = QWidget()
        ob = QVBoxLayout(self.owner_box)
        ob.setContentsMargins(0, 0, 0, 0)
        ob.setSpacing(10)
        keyrow = QHBoxLayout()
        b_key = button("1. وارد کردن فایل کلید (Service Account)", "primary", "upload")
        b_key.clicked.connect(self.import_google_key)
        b_keytest = button("2. وصل شدن و آماده‌سازی تب‌ها", None, "check")
        b_keytest.clicked.connect(self.test_direct)
        keyrow.addWidget(b_key)
        keyrow.addWidget(b_keytest)
        keyrow.addStretch(1)
        ob.addLayout(keyrow)
        self.google_email = label("", "caption", wrap=True, selectable=True)
        ob.addWidget(self.google_email)
        ob.addWidget(label("فایل کلید رمزگذاری‌شده با حساب ویندوز خودت ذخیره می‌شود و هیچ‌جا فرستاده نمی‌شود. اپ تب‌های لازم "
                           "(Workflow، Online + Instore، Decisions، Execution، Audit) را اگر نباشند خودش اضافه می‌کند و محتوای "
                           "تب‌های دیگر را تغییر نمی‌دهد.", "muted", wrap=True))
        ob.addWidget(label("قفل شیت: همه‌ی تب‌ها قفل می‌شوند و تیم Instore فقط ستون‌های Instore در تب «Online + Instore» را "
                           "می‌تواند پر کند. خودت (مالک شیت) و اپ همیشه دسترسی دارید؛ ایمیل همکاران Online که باید بقیه‌ی "
                           "شیت را هم ویرایش کنند را اینجا بنویس (با کاما جدا کن):", "muted", wrap=True))
        self.editors = QLineEdit()
        self.editors.setPlaceholderText("name@snapppay.ir, other@snapppay.ir")
        self.editors.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.editors.editingFinished.connect(self._save_editors)
        ob.addWidget(self.editors)
        sh.lay.addWidget(self.owner_box)
        for attr, text, slot in (("workflow_switch", "اتصال دائمی: هر 30 ثانیه گردش کار با شیت همگام شود (تا وقتی اپ باز است)", self._workflow_toggled),
                                 ("auto", "بعد از هر بررسی، نتایج کامل هم به تب Results شیتم اضافه شود", self._auto_toggled)):
            r = QHBoxLayout()
            sw = Switch()
            sw.toggled.connect(slot)
            setattr(self, attr, sw)
            r.addWidget(sw)
            r.addWidget(label(text, "muted"))
            r.addStretch(1)
            sh.lay.addLayout(r)
        self.sheet_steps = StepList()
        sh.lay.addWidget(self.sheet_steps)
        b_alt = button("روش جایگزین: اسکریپت داخل شیت (بدون فایل کلید)", "link")
        b_alt.setCheckable(True)
        sh.lay.addWidget(b_alt, 0, Qt.AlignmentFlag.AlignRight)
        self.alt_box = QWidget()
        al = QVBoxLayout(self.alt_box)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(label(SHEET_STEPS, "muted", wrap=True))
        row = QHBoxLayout()
        b_copy = button("کپی کد اسکریپت", None, "copy")
        b_copy.clicked.connect(self.copy_script)
        row.addWidget(b_copy)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://script.google.com/macros/s/…/exec")
        self.url.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        row.addWidget(self.url, 1)
        b_test = button("تست", None, "check")
        b_test.clicked.connect(self.test_sheet)
        row.addWidget(b_test)
        al.addLayout(row)
        al.addWidget(label("در این روش، تب «Online + Instore» پر نمی‌شود؛ برای گردش کار کامل از فایل کلید استفاده کن.", "caption", wrap=True))
        self.alt_box.setVisible(False)
        b_alt.toggled.connect(self.alt_box.setVisible)
        sh.lay.addWidget(self.alt_box)
        self.body.addWidget(sh)

        # ---- colleague: personal access code from the owner
        self.access_card = Card()
        self.access_card.header("دسترسی همکار", "کد شخصی را از مدیر (mohammadreza.vahab) بگیر؛ فایل کلید لازم نیست")
        self.workspace_url = QLineEdit()
        self.workspace_url.setPlaceholderText("نشانی سرویس که مدیر داده")
        self.workspace_url.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.workspace_token = QLineEdit()
        self.workspace_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.workspace_token.setPlaceholderText("کد دسترسی شخصی")
        self.access_card.lay.addWidget(self.workspace_url)
        self.access_card.lay.addWidget(self.workspace_token)
        b_access = button("فعال‌سازی دسترسی من", "primary", "user")
        b_access.clicked.connect(self.connect_workspace)
        self.access_card.lay.addWidget(b_access, 0, Qt.AlignmentFlag.AlignRight)
        self.body.addWidget(self.access_card)

        # ---- enamad + websites
        web = Card()
        b_web = button("تست اینماد و سایت‌ها", "primary", "globe")
        b_web.clicked.connect(self.test_web)
        web.header("اینماد و سایت‌ها", "اینترنت این سیستم برای بررسی سایت‌ها و استعلام اینماد", b_web)
        self.web_steps = StepList()
        web.lay.addWidget(self.web_steps)
        self.body.addWidget(web)

        # ---- folders
        files = Card()
        files.header("پوشه‌ی داده‌های من", str(user_dir()))
        rf = QHBoxLayout()
        b_data = button("باز کردن پوشه", None, "folder")
        b_data.clicked.connect(lambda: os.startfile(str(user_dir())))
        b_logs = button("پوشه‌ی لاگ‌ها", None, "logs")
        b_logs.clicked.connect(lambda: os.startfile(str(logs_dir())))
        rf.addWidget(b_data)
        rf.addWidget(b_logs)
        rf.addStretch(1)
        files.lay.addLayout(rf)
        self.body.addWidget(files)
        self.body.addStretch(1)
        self._loading = False

    def on_show(self):
        self._loading = True
        cfg = sheets.load()
        self.url.setText(cfg.get("webapp_url", ""))
        self.auto.setChecked(bool(cfg.get("auto_send")))
        self.auto.setEnabled(bool(cfg.get("webapp_url")) or cfg.get('auth_mode') == 'service_account')
        email = cfg.get('service_account_email')
        self.google_email.setText(f"کلید وارد شده: {email}" if email and google_credentials.available()
                                  else "هنوز فایل کلید وارد نشده — همان فایل JSON که از Google Cloud گرفتی")
        direct = self._owner() or google_credentials.available()
        self.owner_box.setVisible(True)                          # trusted colleagues may use the owner's key file too
        self.access_card.setVisible(not direct)
        self.workflow_switch.setChecked(bool(cfg.get("workflow_sync")))
        self.workspace_url.setText(cfg.get('workspace_url',''))
        self.editors.setText(', '.join(cfg.get('sheet_editors') or []))
        self._loading = False

    # ---- CRM
    def test_crm(self):
        self.crm_steps.set_steps([("در حال تست…", None, "")])
        run_bg(lambda _p: crm_sync.check(), self.crm_steps.set_steps,
               lambda e: self.crm_steps.set_steps([("خطا", False, str(e))]))

    # ---- NBO
    def test_nbo(self):
        client = self.shell.nbo_client
        self.nbo_steps.set_steps([("در حال تست…", None, "")])
        steps = []

        def logged(ok):
            steps.append(("ورود به NBO", ok, "نشست فعال است" if ok else "وارد نشده‌ای — «ورود به NBO» را بزن"))
            if not ok:
                self.nbo_steps.set_steps(steps)
                return
            client.probe(probed)

        def probed(st):
            s = st.get("state")
            if s == "done":
                total = st.get("total")
                steps.append(("خواندن فهرست ثبت‌نام‌ها (PENDING)", True, f"{num(total)} درخواست در انتظار" if total is not None else "پاسخ داد"))
            else:
                detail = {"login": "نشست منقضی شده؛ دوباره وارد شو", "http": f"HTTP {st.get('status')}", "network": "شبکه",
                          "timeout": "پاسخ نداد"}.get(s, str(st.get("message") or s))
                steps.append(("خواندن فهرست ثبت‌نام‌ها (PENDING)", False, detail))
            self.nbo_steps.set_steps(steps)
        client.logged_in(logged)

    def test_nbo_export(self):
        client = self.shell.nbo_client
        self.nbo_steps.set_steps([("در حال گرفتن یک خروجی واقعی (PENDING)…", None, "ممکن است یک دقیقه طول بکشد")])

        def done(data, err):
            if err:
                self.nbo_steps.set_steps([("خروجی NBO", False, err)])
                return
            steps = [("خروجی NBO گرفته شد", True, f"{num(len(data) // 1024)} کیلوبایت، فایل Excel")]
            fd, tmp = tempfile.mkstemp(suffix=".xlsx")
            os.write(fd, data)
            os.close(fd)
            try:
                rows = imports.read_export(tmp, "nbo")
                steps.append(("ستون‌ها دقیقاً همان ساختار مورد انتظار است", True, f"{num(len(rows))} ردیف خوانده شد"))
            except Exception as e:
                steps.append(("ستون‌ها دقیقاً همان ساختار مورد انتظار است", False, str(e)))
            finally:
                os.unlink(tmp)
            self.nbo_steps.set_steps(steps)
        client.export(["PENDING"], done)

    # ---- own sheet
    def connect_workspace(self):
        url=self.workspace_url.text().strip()
        if not sheets.valid_webapp_url(url):
            QMessageBox.warning(self,'اتصال همکار','URL سرویس معتبر نیست'); return
        cfg=sheets.load(); cfg['workspace_url']=url
        try:
            if self.workspace_token.text().strip():
                workspace.save_access(self.session.profile['username'],self.workspace_token.text().strip())
            auth=workspace.access()
            if not auth or auth['username'] != workspace.username(self.session.profile['username']):
                raise ValueError('کد دسترسی باید متعلق به همین حساب CRM باشد')
        except Exception:
            QMessageBox.warning(self,'اتصال همکار','کد دسترسی ذخیره نشد یا متعلق به این حساب نیست'); return
        def done(result):
            if result.get('version')!=4:
                QMessageBox.warning(self,'اتصال همکار','نسخه سرویس باید 4 باشد'); return
            cfg.update(auth_mode='workspace',workflow_sync=True)
            sheets.save(cfg); self.workspace_token.clear()
            self.sheet_steps.set_steps([('فضای مشترک',True,workspace.ROLES.get(result['user']['role'],''))])
            self.session.sync_workflow(force=True)
        run_bg(lambda _p:workspace.call('whoami',cfg),done,lambda e:QMessageBox.warning(self,'اتصال همکار',str(e)))

    def import_google_key(self):
        path, _ = QFileDialog.getOpenFileName(self, 'فایل Service Account', '', 'JSON (*.json)')
        if not path: return
        try:
            email = google_credentials.import_file(path)
            cfg = sheets.load()
            cfg.update(auth_mode='service_account', service_account_email=email)
            sheets.save(cfg)
        except Exception:
            QMessageBox.warning(self, 'کلید گوگل', 'فایل معتبر نبود یا ذخیره امن ویندوز انجام نشد؛ جزئیات کلید نمایش داده نمی‌شود.')
            return
        self.on_show()
        self.test_direct()

    def _save_editors(self):
        found = [e.strip() for e in self.editors.text().replace('،', ',').replace(';', ',').split(',') if e.strip()]
        bad = [e for e in found if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e)]
        if bad:
            QMessageBox.warning(self, "قفل شیت", "این ایمیل معتبر نیست: " + "، ".join(bad))
            return
        cfg = sheets.load()
        if cfg.get("sheet_editors") != found:
            cfg["sheet_editors"] = found
            sheets.save(cfg)
            from ...google_sheet import forget_checks
            forget_checks()                     # the next sheet round applies the new list (not half an hour later)

    def _owner(self):
        try:
            return workspace.username(self.session.profile.get("username")) == workspace.ADMIN
        except ValueError:
            return False

    def test_direct(self):
        cfg = sheets.load()
        if cfg.get('auth_mode') != 'service_account' or not google_credentials.available():
            QMessageBox.information(self, 'شیت من', 'اول «وارد کردن فایل کلید» را بزن.')
            return
        self.sheet_steps.set_steps([('در حال وصل شدن به شیت…', None, '')])

        def work(_p):
            from ...google_sheet import Client
            labels = sheets.nbo_labels()
            with Client(cfg['own_sheet_id']) as google:
                info = google.ping()
                created = google.ensure_tabs(list(labels['edit'].values()) + list(labels['cancel'].values()))
                google.lock(cfg.get('sheet_editors') or ())
                google.tidy()
            return info, created

        def ok(res):
            info, created = res
            cfg2 = sheets.load()
            cfg2.update(workflow_sync=True, auto_send=True, sheet_name=info.get('sheet', ''))   # connected = stays connected
            sheets.save(cfg2)
            self.sheet_steps.set_steps([("اتصال به شیت", True, f"«{info.get('sheet', '')}»"),
                                        ("تب‌های گردش کار", True, ("اضافه شد: " + "، ".join(created)) if created else "همه آماده بودند"),
                                        ("قفل شیت", True, "Instore فقط ستون‌های خودش را می‌تواند پر کند"),
                                        ("اتصال دائمی", True, "هر 30 ثانیه، تا وقتی اپ باز است")])
            self.on_show()
            self.session.sync_workflow(force=True)
            self.shell._update_status()

        def bad(e):
            self.sheet_steps.set_steps([("اتصال به شیت", False, str(e))])
        run_bg(work, ok, bad)

    def copy_script(self):
        QGuiApplication.clipboard().setText(sheets.script_code())
        toast(self.window(), "کد اسکریپت کپی شد — در Apps Script شیت خودت بچسبان")

    def test_sheet(self):
        cfg = sheets.load()
        cfg["webapp_url"] = self.url.text().strip()
        sheets.save(cfg)
        self.sheet_steps.set_steps([("در حال تست…", None, "")])

        def ok(data):
            self.sheet_steps.set_steps([("اتصال به شیت", True, f"وصل شد: «{data.get('sheet', '')}»"),
                                        ("گردش کار شیت اختصاصی", data.get("version", 0) >= 3 and data.get("sheet_id") == cfg["own_sheet_id"],
                                         "نسخه 3" if data.get("version", 0) >= 3 else "اسکریپت قدیمی است؛ دوباره کپی و Deploy کن")])
            self.auto.setEnabled(True)
            self.shell._update_status()

        def bad(e):
            self.sheet_steps.set_steps([("اتصال به شیت", False, str(e))])
        run_bg(lambda _p: sheets.ping(cfg), ok, bad)

    def _auto_toggled(self, on):
        if self._loading:
            return
        cfg = sheets.load()
        cfg["auto_send"] = bool(on)
        sheets.save(cfg)
        self.shell._update_status()

    def _workflow_toggled(self, on):
        if self._loading:
            return
        cfg = sheets.load()
        if on and not (sheets.valid_webapp_url(cfg.get("webapp_url", "")) or
                       (cfg.get('auth_mode') == 'service_account' and google_credentials.available())):
            QMessageBox.information(self, "اتصال شیت", "اول فایل کلید را وارد کن و «وصل شدن» را بزن.")
            self.workflow_switch.blockSignals(True)
            self.workflow_switch.setChecked(False)
            self.workflow_switch.blockSignals(False)
            return
        cfg["workflow_sync"] = bool(on)
        sheets.save(cfg)
        self.session.sync_workflow(force=True)

    # ---- enamad + websites
    def test_web(self):
        self.web_steps.set_steps([("در حال تست…", None, "")])

        async def go():
            steps = []
            async with httpx.AsyncClient(timeout=20, headers=sitec.HEADERS) as c:
                info = await enamad_mod.lookup(c, "digikala.com")
                steps.append(("استعلام اینماد (digikala.com)", info.found is True,
                              f"پیدا شد، وضعیت {info.status}، صاحب امتیاز: {info.owner or '—'}" if info.found else (info.error or "پاسخ نداد")))
                fetch = sitec.make_fetch(c)
                home = await fetch("https://www.google.com/")
                steps.append(("باز شدن سایت‌ها", home.ok, "اینترنت در دسترس است" if home.ok else str(home.error)))
            return steps
        run_bg(lambda _p: asyncio.run(go()), self.web_steps.set_steps, lambda e: self.web_steps.set_steps([("خطا", False, str(e))]))
