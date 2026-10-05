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

        shared = Card()
        reconnect = button('بررسی اتصال مشترک', 'primary', 'refresh')
        reconnect.clicked.connect(self.test_workspace)
        import_key = button('وارد کردن Service Account JSON', None, 'upload')
        import_key.clicked.connect(self.import_workspace_key)
        shared.header('فضای مشترک تیم', 'Google Service Account — ذخیرهٔ رمزگذاری‌شده روی همین ویندوز', [import_key, reconnect])
        self.google_account = label('', 'caption', wrap=True, selectable=True)
        shared.lay.addWidget(self.google_account)
        self.sheet_steps = StepList()
        shared.lay.addWidget(self.sheet_steps)
        self.body.addWidget(shared)
        self.session.workflow_sync_changed.connect(self.on_show)

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
        configured = google_credentials.available()
        self.google_account.setText('کلید رمزگذاری‌شدهٔ این ویندوز موجود است' if configured else 'تنظیم نشده — فایل JSON را وارد کن')
        self.sheet_steps.set_steps([('همگام‌سازی مشترک', None, self.session.workflow_sync_status if configured else 'کلید Google وارد نشده')])

    def import_workspace_key(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Service Account JSON', '', 'JSON (*.json)')
        if not path:
            return
        def done(email):
            self.google_account.setText(email)
            self.test_workspace()
        run_bg(lambda _p: google_credentials.import_file(path), done,
               lambda _e: self.sheet_steps.set_steps([('کلید Google', False, 'فایل یا ذخیرهٔ امن ویندوز معتبر نیست؛ کلید قبلی حفظ شد')]))

    def test_workspace(self):
        self.sheet_steps.set_steps([('فضای مشترک', None, 'در حال بررسی دسترسی خواندن و نوشتن…')])
        def done(result):
            try:
                info = google_credentials.load()
                self.google_account.setText(str(info.get('client_email', '')))
            except Exception:
                self.google_account.setText('Service Account')
            self.sheet_steps.set_steps([('فضای مشترک', True, 'متصل — دسترسی خواندن و نوشتن تأیید شد')])
            self.session.sync_workflow(force=True)
        run_bg(lambda _p: workspace.call('health', force=True), done,
               lambda e: self.sheet_steps.set_steps([('فضای مشترک', False, str(e))]))

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
