"""Connections: prove every link works, step by step, with real numbers - CRM, NBO (including a real export whose column
headers are verified), the person's Google Sheet (guided setup) and the shared Online-Instore sheet (structure + dropdown
values checked before anything may be written), enamad and plain websites."""
import asyncio
import os
import tempfile

import httpx
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QMessageBox

from ... import crm_sync, imports, sheets
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


SHEET_STEPS = ("۱. شیت خودت را باز کن (همان «AutoReview - Results») و از منوی «Extensions» گزینه‌ی «Apps Script» را بزن.\n"
               "۲. هر چه در ویرایشگر هست پاک کن، دکمه‌ی «کپی کد اسکریپت» را بزن، آنجا بچسبان و «Save» را بزن.\n"
               "۳. دکمه‌ی «Deploy» و بعد «New deployment» را بزن و نوع را «Web app» بگذار.\n"
               "۴. در «Execute as» گزینه‌ی «Me» و در «Who has access» گزینه‌ی «Anyone» را انتخاب کن و «Deploy» را بزن.\n"
               "۵. گوگل اجازه می‌خواهد: حساب خودت را انتخاب کن و «Allow» را بزن (اسکریپت مال خودت است).\n"
               "۶. «Web app URL» را کپی کن، در کادر زیر بچسبان و «تست» را بزن.")


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

        # ---- own Google Sheet
        sh = Card()
        sh.header("شیت گوگل خودت", "همه‌ی نتایج کامل، صف دستی و خلاصه‌ی هر اجرا در شیت خودت ثبت می‌شود")
        sh.lay.addWidget(label(SHEET_STEPS, "muted", wrap=True))
        row = QHBoxLayout()
        b_copy = button("کپی کد اسکریپت", None, "copy")
        b_copy.clicked.connect(self.copy_script)
        row.addWidget(b_copy)
        b_open = button("باز کردن شیت من", None, "external")
        b_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://docs.google.com/spreadsheets/u/0/")))
        row.addWidget(b_open)
        row.addStretch(1)
        sh.lay.addLayout(row)
        row2 = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://script.google.com/macros/s/…/exec")
        self.url.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        row2.addWidget(self.url, 1)
        b_test = button("تست", "primary", "check")
        b_test.clicked.connect(self.test_sheet)
        row2.addWidget(b_test)
        sh.lay.addLayout(row2)
        row3 = QHBoxLayout()
        self.auto = Switch()
        self.auto.toggled.connect(self._auto_toggled)
        row3.addWidget(self.auto)
        row3.addWidget(label("بعد از هر بررسی، نتایج خودکار به شیتم ارسال شود", "muted"))
        row3.addStretch(1)
        sh.lay.addLayout(row3)
        self.sheet_steps = StepList()
        sh.lay.addWidget(self.sheet_steps)
        self.body.addWidget(sh)

        # ---- Online-Instore
        oi = Card()
        b_desc = button("بررسی ساختار شیت", "primary", "list-check")
        b_desc.clicked.connect(self.describe_oi)
        oi.header("شیت Online-Instore (مشترک با تیم)", "فقط ۴ ستون گروه Online در تب Pending، فقط ردیف‌های خالی — با مقدارهای کشویی خود شیت", b_desc)
        r = QHBoxLayout()
        self.oi_switch = Switch()
        self.oi_switch.setEnabled(False)
        self.oi_switch.toggled.connect(self._oi_toggled)
        r.addWidget(self.oi_switch)
        self.oi_switch_text = label("ثبت در Online-Instore — اول «بررسی ساختار شیت» را بزن", "muted")
        r.addWidget(self.oi_switch_text)
        r.addStretch(1)
        oi.lay.addLayout(r)
        r2 = QHBoxLayout()
        r2.addWidget(label("کلمه‌ی «نشانگر» اتوماسیون (وقتی کد نتوانست تصمیم بگیرد):", "muted"))
        self.marker = QComboBox()
        self.marker.setMinimumWidth(220)
        self.marker.currentIndexChanged.connect(self._marker_changed)
        r2.addWidget(self.marker)
        r2.addStretch(1)
        oi.lay.addLayout(r2)
        self.oi_steps = StepList()
        oi.lay.addWidget(self.oi_steps)
        self.body.addWidget(oi)

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
        self.auto.setEnabled(bool(cfg.get("webapp_url")))
        self.oi_switch.setChecked(bool(cfg["oi"].get("enabled")))
        self._fill_marker(cfg["oi"].get("marker", ""), [])
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
                                        ("دسترسی Online-Instore در اسکریپت", bool(data.get("online_instore")),
                                         "فعال" if data.get("online_instore") else "اسکریپت قدیمی است؛ دوباره کپی و Deploy کن")])
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

    # ---- Online-Instore
    def describe_oi(self):
        cfg = sheets.load()
        if not cfg.get("webapp_url"):
            QMessageBox.information(self, "Online-Instore", "اول شیت خودت را وصل کن؛ اسکریپت شیت تو با حساب خودت Online-Instore را می‌خواند.")
            return
        self.oi_steps.set_steps([("در حال خواندن ساختار Online-Instore…", None, "")])

        def ok(d):
            L = d["layout"]
            names = L.get("names", [])
            cols = "، ".join(f"{col_letter(L[k])} «{n}»" for k, n in zip(("date", "result", "edit", "cancel"), names))
            problems = sheets.oi_problems(d, cfg)
            steps = [("ستون‌های گروه Online پیدا شد", True, cols),
                     ("ستون Case ID", True, col_letter(L["caseCol"])),
                     ("ردیف‌ها", True, f"{num(d['rows'])} ردیف، {num(d['empty_count'])} ردیفِ منتظر بررسی ما"),
                     ("مقدارهای کشویی «بررسی قرارداد»", bool((d.get("allowed") or {}).get("result")),
                      "، ".join((d.get("allowed") or {}).get("result") or []) or "فهرست ندارد")]
            if problems:
                steps += [("ناسازگاری", False, p) for p in problems]
            else:
                steps.append(("همه‌ی مقدارهایی که اپ می‌نویسد در فهرست‌های خود شیت هست", True, ""))
            self.oi_steps.set_steps(steps)
            self._fill_marker(cfg["oi"].get("marker", ""), d.get("seen", {}).get("result", []))
            self.oi_switch.setEnabled(not problems)
            self.oi_switch_text.setText("ثبت در Online-Instore" if not problems else "ثبت در Online-Instore — اول ناسازگاری‌ها رفع شود")

        def bad(e):
            self.oi_steps.set_steps([("خواندن Online-Instore", False, str(e))])
        run_bg(lambda _p: sheets.oi_describe(cfg), ok, bad)

    def _fill_marker(self, current, seen):
        self._loading = True
        self.marker.clear()
        self.marker.addItem("— هیچ (دستی‌ها خالی بمانند) —", "")
        options = [v for v in seen if v not in sheets.load()["oi"]["result_values"].values()]
        if current and current not in options:
            options.insert(0, current)
        for v in options:
            self.marker.addItem(v, v)
        i = self.marker.findData(current)
        self.marker.setCurrentIndex(max(0, i))
        self._loading = False

    def _marker_changed(self, _i):
        if self._loading:
            return
        cfg = sheets.load()
        cfg["oi"]["marker"] = self.marker.currentData() or ""
        sheets.save(cfg)

    def _oi_toggled(self, on):
        if self._loading:
            return
        cfg = sheets.load()
        if on and QMessageBox.question(self, "Online-Instore", "از این به بعد، بعد از هر بررسیِ صف «آنلاین + حضوری»، نتیجه‌ها (با تأیید تو) "
                                       "فقط در ۴ ستون خالیِ گروه Online نوشته می‌شود. روشن شود؟") != QMessageBox.StandardButton.Yes:
            self._loading = True
            self.oi_switch.setChecked(False)
            self._loading = False
            return
        cfg["oi"]["enabled"] = bool(on)
        sheets.save(cfg)

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
