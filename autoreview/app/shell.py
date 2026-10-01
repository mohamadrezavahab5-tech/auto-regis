"""The main window: navigation rail on the right (RTL), a top bar with the page title and today's Jalali date, a permanent
dry-run notice, and a status bar that always shows the state of every connection."""
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QProgressDialog,
                               QPushButton, QStackedWidget, QStatusBar, QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import crm_sync, jalali, reference, settings, sheets, updates
from ..version import __version__
from . import icons
from .session import run_bg
from .theme import C
from .web import NboClient, PageRenderer
from .widgets import Pill, label, toast

NAV = [
    ("کار", [("dashboard", "داشبورد", "dashboard"), ("review", "بررسی", "review"), ("results", "نتایج", "results"),
             ("workflow", "گردش کار", "list-check"), ("execution", "کنترل اجرا", "shield"),
             ("search", "جستجو در مرجع", "search")]),
    ("سامانه‌ها", [("nbo", "NBO", "nbo"), ("crm", "CRM", "crm")]),
    ("مدیریت", [("connections", "اتصال‌ها", "plug"), ("users", "کاربران", "user"), ("logs", "لاگ‌ها", "logs"), ("settings", "تنظیمات", "settings")]),
]


class Shell(QMainWindow):
    signed_out = Signal()

    def __init__(self, session):
        super().__init__()
        self.session = session
        from .execution_control import ExecutionControl
        self.execution = ExecutionControl(session, self)
        self.setWindowTitle("AutoReview")
        self.setWindowIcon(icons.app_icon())
        self.resize(1360, 860)
        self.setMinimumSize(1100, 700)
        root = QWidget()
        root.setObjectName("canvas")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._rail())
        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(26, 18, 26, 14)
        v.setSpacing(14)
        v.addWidget(self._topbar())
        v.addWidget(self._banner())
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        h.addWidget(content, 1)
        self._status()

        self.nbo_client = NboClient(self)                      # hidden page in the person's NBO session (export, checks)
        from .automatic import AutomaticSources
        self.automatic = AutomaticSources(session, self.nbo_client, self)
        session.renderer = PageRenderer(self)                  # hidden browser for the 'second look' at JavaScript pages
        from .pages import build_pages
        self.pages = build_pages(session, self)
        for key, page in self.pages.items():
            self.stack.addWidget(page)
        for i, (key, _) in enumerate(self.buttons.items(), 1):
            if i <= 9:
                QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda k=key: self.go(k))
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_current)
        session.busy_changed.connect(lambda *_: self._update_status())
        session.data_changed.connect(self._update_status)
        session.workflow_sync_changed.connect(self._update_status)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(30_000)
        self._tick()
        self._quitting = False
        self._told_tray = False
        self._make_tray()
        self.update_info = None
        self._updates = QTimer(self)
        self._updates.timeout.connect(self.check_update)
        self._updates.start(6 * 3600 * 1000)
        QTimer.singleShot(20_000, self.check_update)
        self.go("dashboard")

    # ---- rail
    def _rail(self):
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(236)
        v = QVBoxLayout(rail)
        v.setContentsMargins(14, 18, 14, 16)
        v.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(38))
        brand.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label("AutoReview", "brand"))
        col.addWidget(label("بررسی خودکار ثبت‌نام‌های آنلاین", "brandSub"))
        brand.addLayout(col, 1)
        v.addLayout(brand)
        v.addSpacing(14)
        self.buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for section, items in NAV:
            v.addWidget(label(section, "railSection"))
            for key, text, ic in items:
                b = QPushButton(text)
                b.setObjectName("railItem")
                b.setCheckable(True)
                b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
                b.setIcon(icons.icon(ic, C["rail_text"], 18))
                b.setIconSize(QSize(18, 18))
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=key: self.go(k))
                group.addButton(b)
                v.addWidget(b)
                self.buttons[key] = b
        v.addStretch(1)
        foot = QFrame()
        foot.setObjectName("railFoot")
        f = QVBoxLayout(foot)
        f.setContentsMargins(12, 10, 12, 10)
        f.setSpacing(6)
        row = QHBoxLayout()
        av = QLabel()
        av.setPixmap(icons.pixmap("user", C["rail_text"], 18))
        row.addWidget(av)
        name = label(self.session.user_label(), "railFootText")
        name.setWordWrap(True)
        row.addWidget(name, 1)
        f.addLayout(row)
        out = QPushButton("خروج از حساب")
        out.setObjectName("railItem")
        out.setIcon(icons.icon("logout", C["rail_text"], 16))
        out.clicked.connect(self.signed_out.emit)
        f.addWidget(out)
        v.addWidget(foot)
        return rail

    # ---- top
    def _topbar(self):
        bar = QFrame()
        bar.setObjectName("topbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label("", "pageTitle")
        self.subtitle = label("", "pageSub")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        h.addLayout(col, 1)
        self.date = label("", "muted")
        h.addWidget(self.date)
        return bar

    def _banner(self):
        b = QFrame()
        b.setObjectName("banner")
        h = QHBoxLayout(b)
        h.setContentsMargins(14, 8, 14, 8)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("shield", "#8A5A00", 18))
        h.addWidget(ic)
        self.mode_banner = label(self.execution.summary, "bannerText", wrap=True)
        self.execution.changed.connect(lambda: self.mode_banner.setText(self.execution.summary))
        h.addWidget(self.mode_banner, 1)
        control_button = QPushButton("کنترل اجرا")
        control_button.clicked.connect(lambda: self.go("execution"))
        h.addWidget(control_button)
        return b

    def _status(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.pill_crm, self.pill_nbo, self.pill_sheet = Pill("CRM"), Pill("NBO"), Pill("شیت")
        for p in (self.pill_crm, self.pill_nbo, self.pill_sheet):
            sb.addPermanentWidget(p)
        self.update_button = QPushButton("")
        self.update_button.setProperty("kind", "chip")
        self.update_button.setVisible(False)
        self.update_button.clicked.connect(self.start_update)
        sb.addPermanentWidget(self.update_button)
        sb.addPermanentWidget(label(f"نسخه {jalali.fa_digits(__version__)}", "caption"))

    def _update_status(self):
        b = self.session
        db = b.db()
        try:
            m_nbo, m_crm = reference.meta(db, "nbo"), reference.meta(db, "crm")
        finally:
            db.close()

        def show(pill, name, meta, busy):
            if busy:
                pill.set(f"{name}: در حال دریافت…", C["info"], C["info_soft"])
            elif not meta:
                pill.set(f"{name}: بارگذاری نشده", C["danger"], C["danger_soft"])
            else:
                fresh = not reference.is_stale(meta)
                pill.set(f"{name}: {jalali.ago(meta['loaded_at'])}", C["approve"] if fresh else C["warn"],
                         C["approve_soft"] if fresh else C["warn_soft"])
        show(self.pill_nbo, "NBO", m_nbo, "nbo" in b.busy)
        show(self.pill_crm, "CRM", m_crm, "crm" in b.busy)
        cfg = sheets.load()
        connected = cfg.get("webapp_url") or cfg.get("auth_mode") in ("service_account", "workspace")
        if "sheet" in b.busy or "workflow" in b.busy:
            self.pill_sheet.set("شیت: در حال همگام‌سازی…", C["info"], C["info_soft"])
        elif not connected:
            self.pill_sheet.set("شیت: وصل نشده", C["text2"], C["surface2"])
        elif not cfg.get("workflow_sync"):
            self.pill_sheet.set("شیت: وصل — همگام‌سازی خاموش", C["warn"], C["warn_soft"])
        elif b.workflow_sync_status.startswith("همگام‌سازی ناموفق"):
            self.pill_sheet.set("شیت: قطع — تلاش دوباره", C["danger"], C["danger_soft"])
        else:
            self.pill_sheet.set("شیت: همگام", C["approve"], C["approve_soft"])
        self.pill_sheet.setToolTip(b.workflow_sync_status)

    def _tick(self):
        now = datetime.now()
        self.date.setText(f"{jalali.long_date(now)}  •  {jalali.fa_digits(now.strftime('%H:%M'))}")
        self._update_status()

    # ---- navigation
    def go(self, key):
        page = self.pages.get(key)
        if page is None:
            return
        self.buttons[key].setChecked(True)
        self.stack.setCurrentWidget(page)
        self.title.setText(page.title)
        self.subtitle.setText(page.subtitle)
        if hasattr(page, "on_show"):
            page.on_show()

    def _refresh_current(self):
        page = self.stack.currentWidget()
        if hasattr(page, "on_show"):
            page.on_show()

    # ---- shared actions used by several pages
    def nbo_login(self) -> bool:
        from .dialogs import NboLoginDialog
        ok = NboLoginDialog(self).exec() == NboLoginDialog.DialogCode.Accepted
        if ok:
            self.nbo_client.reload()
            toast(self, "وارد NBO شدی")
        return ok

    def relogin(self):
        from PySide6.QtWidgets import QInputDialog, QLineEdit
        user = crm_sync.stored_username() or self.session.profile.get("username", "")
        pw, ok = QInputDialog.getText(self, "ورود دوباره به CRM", f"رمز CRM برای {user}:", QLineEdit.EchoMode.Password)
        if not ok or not pw:
            return

        def done(profile):
            self.session.crm_password = pw
            toast(self, "ورود CRM تایید شد")

        def failed(e):
            QMessageBox.warning(self, "CRM", str(e))
        run_bg(lambda _p: crm_sync.login(user, pw), done, failed)

    def open_in_nbo(self, smr):
        self.go("nbo")
        self.pages["nbo"].open_smr(smr)

    # ---- always on: next to the clock, so the sheet stays in sync while the window is closed
    def _make_tray(self):
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        menu = QMenu(self)
        menu.addAction("باز کردن AutoReview", self.bring_front)
        menu.addAction("همگام‌سازی با شیت", lambda: self.session.sync_workflow(force=True))
        menu.addSeparator()
        menu.addAction("خروج کامل", self.quit_fully)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("AutoReview — در حال همگام‌سازی با شیت")
        self.tray.activated.connect(lambda reason: self.bring_front()
                                    if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)
                                    else None)
        self.tray.show()

    def bring_front(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_fully(self):
        self._quitting = True
        self.close()

    # ---- updates (published by the owner in the 'Updates' tab of his sheet)
    def check_update(self, manual=False):
        def done(rel):
            if rel and updates.is_newer(rel["version"]):
                self.update_info = rel
                self.update_button.setText(f"نسخه‌ی {jalali.fa_digits(rel['version'])} آماده است — به‌روزرسانی")
                self.update_button.setVisible(True)
                if not manual and not self.isVisible():
                    self.tray.showMessage("AutoReview", f"نسخه‌ی {rel['version']} آماده است.")
            elif manual:
                toast(self, "آخرین نسخه را داری")

        def failed(e):
            if manual:
                QMessageBox.warning(self, "به‌روزرسانی", f"بررسی نسخه‌ی جدید انجام نشد:\n{e}")
        run_bg(lambda _p: updates.latest(), done, failed)

    def start_update(self):
        rel = self.update_info
        if not rel:
            return
        if not updates.can_self_update():
            QMessageBox.information(self, "به‌روزرسانی", "این نسخه از روی کد اجرا شده؛ به‌روزرسانی فقط روی برنامه‌ی نصب‌شده کار می‌کند.")
            return
        r = self.session.runner
        if r and r.is_active():
            QMessageBox.information(self, "به‌روزرسانی", "یک بررسی در حال اجراست؛ بعد از تمام شدنش به‌روزرسانی کن.")
            return
        notes = (rel.get("notes") or "").strip()
        if QMessageBox.question(self, "به‌روزرسانی AutoReview",
                                f"نسخه‌ی {rel['version']} دانلود، امضایش بررسی و نصب می‌شود؛ برنامه چند ثانیه بسته و دوباره باز می‌شود. "
                                f"داده‌ها و تنظیمات دست نمی‌خورد.\n\n{notes}") != QMessageBox.StandardButton.Yes:
            return
        dlg = QProgressDialog("در حال دانلود نسخه‌ی جدید…", "", 0, 1000, self)
        dlg.setWindowTitle("به‌روزرسانی")
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)

        def progress(p):
            done, total = p
            dlg.setValue(int(1000 * done / total) if total else 0)
            dlg.setLabelText(f"در حال دانلود… {jalali.fa_digits(done // (1024 * 1024))} مگابایت")

        def downloaded(path):
            dlg.close()
            try:
                updates.install(path, Path(sys.executable).parent, os.getpid())
            except Exception as e:
                QMessageBox.warning(self, "به‌روزرسانی", str(e))
                return
            self.quit_fully()

        def failed(e):
            dlg.close()
            QMessageBox.warning(self, "به‌روزرسانی", f"به‌روزرسانی انجام نشد (چیزی نصب نشد):\n{e}")
        run_bg(lambda p: updates.download(rel, p), downloaded, failed, progress)

    def closeEvent(self, e):
        keep = settings.load_rules().get("automation", {}).get("keep_in_tray", True)
        if keep and not self._quitting and QSystemTrayIcon.isSystemTrayAvailable():
            e.ignore()
            self.hide()
            if not self._told_tray:
                self._told_tray = True
                self.tray.showMessage("AutoReview", "کنار ساعت ویندوز ماند و با شیت همگام است. خروج کامل: راست‌کلیک روی آیکون.")
            return
        r = self.session.runner
        if r and r.is_active():
            if QMessageBox.question(self, "بستن برنامه", "یک بررسی در حال اجراست. متوقفش کنم و ببندم؟") != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            r.stop()
            r.join(20)
        self.automatic.stop()
        self.execution.stop()
        self.tray.hide()
        super().closeEvent(e)
