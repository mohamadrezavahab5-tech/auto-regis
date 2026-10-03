"""The main window: navigation rail on the right (RTL), a top bar with the page title and today's Jalali date, a permanent
dry-run notice, and a status bar that always shows the state of every connection."""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressDialog,
                               QPushButton, QScrollArea, QStackedWidget, QStatusBar, QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import crm_sync, google_credentials, jalali, reference, settings, sheets, updates
from ..version import __version__
from . import icons, theme
from .session import run_bg
from .theme import C
from .web import NboClient, PageRenderer
from .widgets import LiveChart, Pill, label, ltr, toast

# The work, step by step, in the order it happens (owner 2026-10-02: "it is not clear how Workflow and Review differ";
# "there is no clear place to apply all / automatically"). The page titles say the same step.
NAV = [
    ("خلاصه", [("dashboard", "داشبورد", "dashboard"), ("control", "اتاق کنترل", "clock"), ("accuracy", "دقت موتور", "check")]),
    ("کار روزانه", [("review", "دریافت و بررسی", "review"), ("workflow", "وضعیت درخواست‌ها", "list-check"),
                   ("triage", "رسیدگی دستی", "play"), ("execution", "ثبت در NBO", "shield"),
                   ("results", "نتایج بررسی موتور", "results")]),
    ("ابزار", [("search", "جستجو در مرجع", "search"), ("nbo", "NBO", "nbo"), ("crm", "CRM", "crm")]),
    ("مدیریت", [("connections", "اتصال‌ها", "plug"), ("users", "کاربران", "user"), ("logs", "لاگ‌ها", "logs"), ("settings", "تنظیمات", "settings")]),
]


class Shell(QMainWindow):
    signed_out = Signal()

    def __init__(self, session):
        super().__init__()
        self.session = session
        from .execution_control import ExecutionControl
        self.execution = ExecutionControl(session, self)
        self.setWindowTitle("AutoReview — SnappPay")
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
        self.execution.notice.connect(self._execution_notice)
        self.update_info = None
        self._updates = QTimer(self)
        self._updates.timeout.connect(self.check_update)
        self._updates.start(6 * 3600 * 1000)
        QTimer.singleShot(20_000, self.check_update)
        QTimer.singleShot(2500, self._offer_google_key)
        self.go("dashboard")

    def _offer_google_key(self):
        """Sheet not connected yet: the owner's key file (his own download, or the copy he handed to a trusted colleague) is
        found in Downloads / Desktop and connected after one 'yes'. The key is never baked into the installer."""
        cfg = sheets.load()
        if cfg.get("auth_mode") == "workspace" or (cfg.get("auth_mode") == "service_account" and google_credentials.available()):
            return
        found = google_credentials.find_key_file()
        if not found:
            return
        path, email = found
        if QMessageBox.question(self, "اتصال به شیت", f"فایل کلید گوگل پیدا شد ({Path(path).name}، {email}).\n"
                                "با همین به شیت خودت وصل شوم؟ کلید رمزگذاری‌شده روی همین ویندوز ذخیره می‌شود.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            google_credentials.import_file(path)
        except Exception:
            QMessageBox.warning(self, "اتصال به شیت", "فایل کلید وارد نشد؛ از «اتصال‌ها» دوباره امتحان کن.")
            return
        cfg = sheets.load()
        cfg.update(auth_mode="service_account", service_account_email=email)
        sheets.save(cfg)
        self.go("connections")
        self.pages["connections"].test_direct()

    # ---- rail
    def _rail(self):
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(236)
        v = QVBoxLayout(rail)
        v.setContentsMargins(14, 18, 14, 16)
        v.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.wordmark_pixmap(46, "#FFFFFF"))      # SnappPay's wordmark, white on the navy rail
        logo.setToolTip("SnappPay")
        brand.addWidget(logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label("AutoReview", "brand"))
        col.addWidget(label("Online merchant review", "brandSub"))
        brand.addLayout(col, 1)
        v.addLayout(brand)
        v.addSpacing(14)
        self.buttons = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        # the menu scrolls when the window is short, so the account box at the bottom is never pushed out of sight
        nav_holder = QWidget()
        nav_holder.setObjectName("railNav")
        nav_holder.setStyleSheet("QWidget#railNav { background: transparent; }")
        outer, v = v, QVBoxLayout(nav_holder)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
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
        scroll = QScrollArea()
        scroll.setWidget(nav_holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
                             f"QScrollBar:vertical {{ width: 6px; background: transparent; margin: 0; }}"
                             f"QScrollBar::handle:vertical {{ background: {C['rail_active']}; border-radius: 3px; min-height: 24px; }}")
        outer.addWidget(scroll, 1)
        v = outer
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
        self.page_icon = QLabel()
        self.page_icon.setFixedSize(44, 44)
        self.page_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_icon.setStyleSheet(f"background: {C['accent_soft']}; border-radius: 13px;")
        h.addWidget(self.page_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label("", "pageTitle")
        self.subtitle = label("", "pageSub")
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        h.addLayout(col, 1)
        # "open this request, why did it decide that?" from anywhere (owner 2026-10-03): a code or part of a site -> its file
        self.find = QLineEdit()
        self.find.setPlaceholderText("کد درخواست یا سایت…  (Ctrl+K)")
        self.find.setClearButtonEnabled(True)
        self.find.setFixedWidth(250)
        self.find.setToolTip("کد درخواست (با یا بدون SMR-) یا بخشی از آدرس سایت / نام فروشگاه را بنویس و Enter بزن: "
                             "پرونده‌اش باز می‌شود — چه چیزهایی چک شد و چرا این تصمیم گرفته شد.")
        self.find.returnPressed.connect(self._find_request)
        h.addWidget(self.find, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(10)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: (self.find.setFocus(), self.find.selectAll()))
        self.live_chart = LiveChart()
        self.live_chart.setToolTip("کار زنده‌ی ۱۰ دقیقه‌ی اخیر: هر نقطه = ۳۰ ثانیه، بررسی‌های موتور + تغییرهای NBO. "
                                   "فقط وقتی دیده می‌شود که اپ در حال کار است.")
        self.live_chart.setVisible(False)
        h.addWidget(self.live_chart, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(8)
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._live_activity)
        self._live_timer.start(5_000)
        self.session.run_changed.connect(self._live_activity)
        self.mode_chip = QPushButton("")
        self.mode_chip.setProperty("kind", "chip")
        self.mode_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_chip.clicked.connect(lambda: self.go("execution"))
        h.addWidget(self.mode_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(10)
        self.date = label("", "muted")
        h.addWidget(self.date)
        h.addSpacing(6)
        self.theme_button = QPushButton()
        self.theme_button.setProperty("kind", "ghost")
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.setIcon(icons.icon("sun" if theme.MODE == "dark" else "moon", C["text2"], 18))
        self.theme_button.setIconSize(QSize(18, 18))
        self.theme_button.setToolTip("حالت روشن" if theme.MODE == "dark" else "حالت تاریک")
        self.theme_button.clicked.connect(self.toggle_theme)
        h.addWidget(self.theme_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.execution.changed.connect(self._mode_chip)
        self._mode_chip()
        return bar

    def _find_request(self):
        from .pages.case_view import find_requests, open_case
        from .pages.common import nbo_status_fa
        text = self.find.text().strip()
        if not text:
            return
        found = find_requests(self.session, text)
        if not found:
            toast(self, "درخواستی با این کد یا سایت در داده‌ی NBO پیدا نشد", "warn")
            return
        if len(found) == 1:
            open_case(self, found[0][0])
            return
        menu = QMenu(self)
        for smr, site, status in found:
            act = menu.addAction(f"{smr}   •   {site or '—'}   •   {nbo_status_fa(status)}")
            act.triggered.connect(lambda _=False, k=smr: open_case(self, k))
        menu.exec(self.find.mapToGlobal(self.find.rect().bottomLeft()))

    def _live_activity(self):
        """Shows the live chart while something runs, fed with what really happened (one small indexed read)."""
        ex, runner = self.execution, self.session.runner
        reviewing = bool(runner and runner.is_active())
        if ex.batch:
            what = "تمرین همه"
        elif ex.mode.live or ex.actor.busy:
            what = "Autopilot" if ex.mode.live else "در NBO"
        elif reviewing:
            what = "بررسی"
        else:
            what = ""
        if not what:
            if self.live_chart.isVisible():
                self.live_chart.setVisible(False)
            return
        now = datetime.now(timezone.utc)
        span = LiveChart.BUCKET * LiveChart.POINTS
        start = now - timedelta(seconds=span)
        counts = [0] * LiveChart.POINTS
        db = self.session.db()
        try:
            stamps = [r[0] for r in db.execute("SELECT decided_at FROM results WHERE decided_at >= ?",
                                               (start.isoformat(timespec="seconds"),))]
            stamps += [r[0] for r in db.execute("SELECT updated_at FROM nbo_execution WHERE updated_at >= ? AND state IN "
                                                "('SENT','REHEARSED','VERIFIED')", (start.isoformat(timespec="seconds"),))]
        except Exception:                                   # the chart never gets in the way of the work
            stamps = []
        finally:
            db.close()
        newest = int(now.timestamp() // LiveChart.BUCKET)    # buckets sit on the clock, so the line can slide between them
        for at in stamps:
            try:
                bucket = int(datetime.fromisoformat(at).timestamp() // LiveChart.BUCKET)
            except (TypeError, ValueError):
                continue
            i = LiveChart.POINTS - 1 - (newest - bucket)
            if 0 <= i < LiveChart.POINTS:
                counts[i] += 1
        per_min = sum(counts[-2:])                           # the last minute
        self.live_chart.text, self.live_chart.rate = what, f"{per_min} در دقیقه"
        self.live_chart.set_series(counts, (now.timestamp() % LiveChart.BUCKET) / LiveChart.BUCKET)
        if not self.live_chart.isVisible():
            self.live_chart.setVisible(True)

    def _mode_chip(self):
        live = self.execution.mode.live
        sending = bool(self.execution.batch and self.execution.batch.get("real"))
        # The chip is about the AUTOMATIC mode. A bare "Fake" read as "nothing here is real" right after the owner had
        # sent real changes by hand (2026-10-03), so it names what it describes.
        self.mode_chip.setText("ثبت واقعی در جریان است" if sending else
                               "ثبت خودکار: روشن" if live else "ثبت خودکار: خاموش")
        fg, bg = (C["danger"], C["danger_soft"]) if (live or sending) else (C["banner_text"], C["warn_soft"])
        self.mode_chip.setStyleSheet(f"QPushButton {{ color: {fg}; background: {bg}; border: 1px solid {bg}; border-radius: 13px; "
                                     f"padding: 4px 12px; font-weight: 600; }} QPushButton:hover {{ border-color: {fg}; }}")
        self.mode_chip.setToolTip("این نشان فقط ثبتِ خودکار را می‌گوید. «ثبت در NBO» که خودت می‌زنی همیشه واقعی است، "
                                  "چه Autopilot روشن باشد چه خاموش — برای کنترل اجرا کلیک کن")

    def toggle_theme(self):
        """Light <-> dark for the whole app; the palette is read when windows are built, so the app reopens itself."""
        new = "light" if theme.MODE == "dark" else "dark"
        over = settings.load_user()
        over.setdefault("rules", {})["appearance.theme"] = new
        settings.save_user(over)
        if not getattr(sys, "frozen", False):
            toast(self, "حالت جدید با باز کردن دوباره‌ی برنامه اعمال می‌شود")
            return
        r = self.session.runner
        if r and r.is_active():
            toast(self, "بعد از تمام شدن بررسی در جریان، برنامه را دوباره باز کن تا حالت جدید اعمال شود", "info")
            return
        exe = sys.executable
        import subprocess
        # start again a moment after this window has closed (one AutoReview per Windows user)
        subprocess.Popen(["cmd", "/c", "ping", "127.0.0.1", "-n", "3", ">nul", "&", "start", "", exe],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
        self.quit_fully()

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
        sb.addPermanentWidget(label(f"v{__version__}", "caption"))

    def _update_status(self):
        b = self.session
        db = b.db()
        try:
            m_nbo, m_crm = reference.meta(db, "nbo"), reference.meta(db, "crm")
        finally:
            db.close()

        def show(pill, name, meta, busy):
            if busy:
                pill.set(ltr(f"{name} · syncing…"), C["info"], C["info_soft"])
            elif not meta:
                pill.set(ltr(f"{name} · not loaded"), C["danger"], C["danger_soft"])
            else:
                fresh = not reference.is_stale(meta)
                pill.set(ltr(f"{name} · {jalali.ago_en(meta['loaded_at'])}"), C["approve"] if fresh else C["warn"],
                         C["approve_soft"] if fresh else C["warn_soft"])
        show(self.pill_nbo, "NBO", m_nbo, "nbo" in b.busy)
        show(self.pill_crm, "CRM", m_crm, "crm" in b.busy)
        cfg = sheets.load()
        connected = cfg.get("webapp_url") or cfg.get("auth_mode") in ("service_account", "workspace")
        if "sheet" in b.busy or "workflow" in b.busy:
            self.pill_sheet.set(ltr("Sheet · syncing…"), C["info"], C["info_soft"])
        elif not connected:
            self.pill_sheet.set(ltr("Sheet · not connected"), C["text2"], C["surface2"])
        elif not cfg.get("workflow_sync"):
            self.pill_sheet.set(ltr("Sheet · sync off"), C["warn"], C["warn_soft"])
        elif b.workflow_sync_status.startswith("همگام‌سازی ناموفق"):
            self.pill_sheet.set(ltr("Sheet · offline, retrying"), C["danger"], C["danger_soft"])
        else:
            self.pill_sheet.set(ltr("Sheet · synced"), C["approve"], C["approve_soft"])
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
        icon = next((ic for _s, items in NAV for k, _t, ic in items if k == key), "dashboard")
        self.page_icon.setPixmap(icons.pixmap(icon, C["accent_text"], 22))
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

    def _execution_notice(self, text):
        toast(self, text, "warn")
        if not self.isVisible():
            self.tray.showMessage("AutoReview — NBO", text)

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
