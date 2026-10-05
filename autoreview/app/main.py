"""Entry point of the desktop app.

Sign-in (or a remembered sign-in, re-checked against CRM) -> main window. One window per Windows user: starting the app a
second time brings the running window to the front. Any unexpected error is written to the log and shown in plain Persian
instead of closing the app silently."""
import ctypes
import getpass
import os
import sys
import traceback

# Keep the hidden NBO worker's short waits from becoming minute-long background ticks.
_flags = os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS', '')
if '--disable-background-timer-throttling' not in _flags.split():
    os.environ['QTWEBENGINE_CHROMIUM_FLAGS'] = (_flags + ' --disable-background-timer-throttling').strip()

from PySide6.QtCore import Qt, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import crm_sync, google_credentials, logs, profile, sheets, workspace
from ..paths import APP_ID
from . import icons, theme

log = logs.get("app")


class App:
    def __init__(self, app):
        self.app = app
        self.login = None
        self.shell = None
        self.background = "--background" in sys.argv

    # ---- sign-in
    def start(self):
        prof = profile.load()
        if prof and prof.get("remember") and crm_sync.have_credentials():
            self._splash_check(prof)
        else:
            self.show_login()

    def _splash_check(self, prof):
        """A remembered sign-in is proven again (CRM WhoAmI) before the window opens; a refused one goes to the login form."""
        from .session import run_bg
        from .dialogs import LoginWindow
        self.login = LoginWindow("در حال بررسی ورود ذخیره‌شده…")
        self.login.go.setEnabled(False)
        self.login.show()

        def ok(_who):
            self.login.close()
            self.open_shell(prof, None)

        def bad(e):
            self.login.go.setEnabled(True)
            self.login.error.setText("ورود ذخیره‌شده دیگر معتبر نیست؛ دوباره وارد شو." if isinstance(e, crm_sync.CrmAuthError)
                                     else "به CRM وصل نشد (شبکه / VPN). دوباره وارد شو.")
            self.login.signed_in.connect(self._signed_in)
        run_bg(lambda _p: crm_sync.whoami(), ok, bad)

    def show_login(self, message=""):
        from .dialogs import LoginWindow
        self.login = LoginWindow(message)
        self.login.signed_in.connect(self._signed_in)
        self.login.show()

    def _signed_in(self, prof, password, remember):
        saved = profile.save(prof["username"], prof["display_name"], remember)
        log.info("signed in as %s (remember=%s)", prof["username"], remember)
        if self.login:
            self.login.close()
        self.open_shell(saved, password)

    def open_shell(self, prof, password):
        identity = crm_sync.authenticated_identity()
        if not identity:
            self.show_login('ورود CRM باید دوباره تأیید شود')
            return
        prof = dict(prof, username=identity['username'], crm_authenticated=True)
        self._show_shell(prof, password)

    def _show_shell(self, prof, password):
        from .session import Session
        from .shell import Shell
        self.shell = Shell(Session(prof, crm_password=password))
        self.shell.signed_out.connect(self.sign_out)
        if "--background" in sys.argv and self.background:
            self.background = False                        # started with Windows: stay next to the clock, keep syncing
        else:
            self.shell.show()

    def sign_out(self):
        if QMessageBox.question(self.shell, "خروج", "از حساب خارج شوی؟ ورود ذخیره‌شده‌ی CRM هم از این کامپیوتر پاک می‌شود.") \
                != QMessageBox.StandardButton.Yes:
            return
        self.shell.session.disconnect_workspace()
        crm_sync.forget_credentials()
        profile.clear()
        log.info("signed out")
        self.shell._quitting = True                        # a real close, not 'hide next to the clock'
        self.shell.close()
        self.shell = None
        self.show_login()

    def front(self):
        w = self.shell or self.login
        if w:
            w.showNormal()
            w.raise_()
            w.activateWindow()

    def on_quit(self):
        prof = profile.load()
        if prof and not prof.get("remember"):
            crm_sync.forget_credentials()                      # not remembered: the login does not outlive the session


def _excepthook(exc_type, exc, tb):
    log.error("unexpected error: %s", "".join(traceback.format_exception(exc_type, exc, tb)))
    try:
        QMessageBox.critical(None, "خطای پیش‌بینی‌نشده", f"یک خطا رخ داد و در لاگ ثبت شد:\n{exc}\n\nبرنامه می‌تواند ادامه دهد.")
    except Exception:
        pass


def main():
    if "--uninstall" in sys.argv:
        from .uninstall import run as uninstall
        return uninstall()
    logs.setup()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)     # own taskbar icon/grouping
    except (AttributeError, OSError):
        pass
    # Widgets draw through RHI from the start. Otherwise the first tab that holds a browser (NBO, CRM, manual review)
    # makes Qt switch the window to RHI by destroying and recreating it: the window vanishes and comes back
    # (owner 2026-10-03; reproduced: one PlatformSurface destroy/create on first showing a QWebEngineView).
    os.environ.setdefault("QT_WIDGETS_RHI", "1")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("AutoReview")
    app.setOrganizationName("SnappPay")
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setWindowIcon(icons.app_icon())
    theme.apply(app)
    from .widgets import WheelGuard
    app._wheel_guard = WheelGuard(app)                  # kept alive with the app
    app.installEventFilter(app._wheel_guard)

    # one instance per Windows user
    key = f"{APP_ID}-{getpass.getuser()}"
    probe = QLocalSocket()
    probe.connectToServer(key)
    if probe.waitForConnected(300):
        probe.write(b"front")
        probe.flush()
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(key)
    server = QLocalServer()
    server.listen(key)

    sys.excepthook = _excepthook
    ctl = App(app)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), ctl.front()))
    app.aboutToQuit.connect(ctl.on_quit)
    log.info("app started")
    QTimer.singleShot(0, ctl.start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
