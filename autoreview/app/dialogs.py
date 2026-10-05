"""Sign-in windows.

App sign-in = the person's own CRM (domain) login, proven against CRM itself. Anyone with CRM access can use the app on
their own PC; nothing is shared between people. Too many wrong passwords in a row are stopped locally for a minute, so the
app can never be the reason a domain account gets locked.
NBO sign-in = NBO's own login page shown inside the app; the window closes by itself once NBO says the person is in."""
import time

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from .. import crm_sync, logs
from ..version import __version__
from . import icons
from .session import run_bg
from .theme import C
from .web import LOGGED_IN_JS, NBO_LOGIN, Page, nbo_profile
from .widgets import button, label

log = logs.get("auth")


class LoginWindow(QWidget):
    signed_in = Signal(dict, str, bool)          # profile, password (kept in RAM only), remember

    def __init__(self, message=""):
        super().__init__()
        self.setWindowTitle("ورود — AutoReview")
        self.setWindowIcon(icons.app_icon())
        self.setObjectName("canvas")
        self.resize(980, 640)
        self._fails = []
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QFrame()
        side.setObjectName("rail")
        side.setFixedWidth(400)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(40, 48, 40, 40)
        sv.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(icons.wordmark_pixmap(72, "#FFFFFF"))           # SnappPay's wordmark on the navy panel
        sv.addWidget(logo)
        sv.addWidget(label("AutoReview", "brand"))
        t = label("بررسی خودکار ثبت‌نام‌های آنلاین — اینماد، سایت، تکراری‌ها و قواعد تیم، با دلیل دقیق NBO برای هر تصمیم.", "railFootText", wrap=True)
        sv.addWidget(t)
        sv.addStretch(1)
        for line in ("هر چه مطمئن نیست، به بررسی دستی می‌رود.", "هیچ تغییری در NBO بدون تأیید تو انجام نمی‌شود.",
                     "نتایج و گزارش فعالیت با فضای مشترک تیم همگام می‌شوند."):
            row = QHBoxLayout()
            ic = QLabel()
            ic.setPixmap(icons.pixmap("check", "#7DBBFD", 16))
            row.addWidget(ic)
            row.addWidget(label(line, "railFootText", wrap=True), 1)
            sv.addLayout(row)
        sv.addWidget(label(f"v{__version__}", "brandSub"))
        outer.addWidget(side)

        main = QWidget()
        mv = QVBoxLayout(main)
        mv.setContentsMargins(64, 64, 64, 64)
        mv.addStretch(1)
        card = QFrame()
        card.setProperty("card", "true")
        card.setMaximumWidth(420)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(30, 28, 30, 28)
        cv.setSpacing(12)
        cv.addWidget(label("ورود", "pageTitle"))
        cv.addWidget(label("با نام کاربری و رمز CRM خودت (همان حساب دامنه‌ی SNAPP).", "muted", wrap=True))
        cv.addSpacing(6)
        cv.addWidget(label("نام کاربری", "h3"))
        self.user = QLineEdit()
        self.user.setPlaceholderText("مثلاً m.rezaei")
        self.user.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        stored = crm_sync.stored_username()
        if stored:
            self.user.setText(stored.split("\\")[-1])
        cv.addWidget(self.user)
        cv.addWidget(label("رمز عبور", "h3"))
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        eye = self.pw.addAction(icons.icon("lock", C["text3"], 16), QLineEdit.ActionPosition.TrailingPosition)
        eye.setToolTip("نمایش / پنهان کردن رمز")
        eye.triggered.connect(lambda: self.pw.setEchoMode(QLineEdit.EchoMode.Normal if self.pw.echoMode() == QLineEdit.EchoMode.Password
                                                          else QLineEdit.EchoMode.Password))
        cv.addWidget(self.pw)
        self.remember = QCheckBox("مرا روی این کامپیوتر به خاطر بسپار")
        self.remember.setChecked(True)
        cv.addWidget(self.remember)
        self.error = label(message, wrap=True)
        self.error.setStyleSheet(f"color: {C['danger']};")
        cv.addWidget(self.error)
        self.go = button("ورود", "primary")
        self.go.setMinimumHeight(40)
        self.go.clicked.connect(self.submit)
        cv.addWidget(self.go)
        mv.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        mv.addStretch(1)
        outer.addWidget(main, 1)
        self.pw.returnPressed.connect(self.submit)
        self.user.returnPressed.connect(lambda: self.pw.setFocus())
        (self.pw if stored else self.user).setFocus()

    def submit(self):
        now = time.time()
        self._fails = [t for t in self._fails if now - t < 300]
        if len(self._fails) >= 3:
            wait = int(60 - (now - self._fails[-1]))
            if wait > 0:
                self.error.setText(f"چند بار رمز اشتباه بود. برای اینکه حساب قفل نشود {wait} ثانیه صبر کن.")
                return
        user, pw = self.user.text().strip(), self.pw.text()
        if not user or not pw:
            self.error.setText("نام کاربری و رمز را وارد کن.")
            return
        self.error.setText("")
        self.go.setEnabled(False)
        self.go.setText("در حال بررسی با CRM…")

        def ok(profile):
            self.go.setEnabled(True)
            self.go.setText("ورود")
            self.signed_in.emit(profile, pw, self.remember.isChecked())

        def bad(e):
            self.go.setEnabled(True)
            self.go.setText("ورود")
            if isinstance(e, crm_sync.CrmAuthError):
                self._fails.append(time.time())
                self.error.setText("نام کاربری یا رمز را CRM نپذیرفت.")
            elif isinstance(e, crm_sync.CrmUnreachable) or not crm_sync.server_reachable():
                self.error.setText("به CRM وصل نشد. اتصال شبکه / VPN را بررسی کن و دوباره بزن.")
            else:
                self.error.setText(f"ورود ممکن نشد: {e}")
            log.warning("sign-in failed for %s: %s", user, type(e).__name__)
        run_bg(lambda _p: crm_sync.login(user, pw), ok, bad)


class NboLoginDialog(QDialog):
    """NBO's own login page; closes by itself once the person is in."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ورود به NBO")
        self.resize(1000, 760)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(10)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap("shield", C["accent"], 22))
        head.addWidget(ic)
        head.addWidget(label("نام کاربری، رمز و کد پیامک (OTP) را خودت در صفحه‌ی خود NBO وارد کن. برنامه رمزت را نمی‌بیند؛ "
                             "بعد از ورود، این پنجره خودش بسته می‌شود.", "muted", wrap=True), 1)
        v.addLayout(head)
        self.view = QWebEngineView()
        self.view.setPage(Page(nbo_profile(), self.view))
        v.addWidget(self.view, 1)
        self.state = label("", "caption")
        v.addWidget(self.state)
        self.view.load(QUrl(NBO_LOGIN))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)
        self._timer.start(1000)

    def _check(self):
        self.view.page().runJavaScript(LOGGED_IN_JS, 0, self._result)

    def _result(self, ok):
        if ok and "/login" not in self.view.url().path():
            self._timer.stop()
            self.state.setText("وارد شدی ✓")
            log.info("signed in to NBO")
            QTimer.singleShot(700, self.accept)
