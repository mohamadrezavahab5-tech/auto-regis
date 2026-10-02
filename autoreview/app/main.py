"""Entry point of the desktop app.

Sign-in (or a remembered sign-in, re-checked against CRM) -> main window. One window per Windows user: starting the app a
second time brings the running window to the front. Any unexpected error is written to the log and shown in plain Persian
instead of closing the app silently."""
import ctypes
import getpass
import sys
import traceback

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
        try:
            is_owner = workspace.username(prof['username']) == workspace.ADMIN
        except ValueError:
            is_owner = False
        cfg = sheets.load()
        # Owner 2026-10-02: a few trusted colleagues may connect with the owner's key file (he hands it over himself);
        # without it they use the personal access code.
        has_key = google_credentials.available() or bool(google_credentials.find_key_file())
        if cfg.get('auth_mode') == 'workspace' or not (is_owner or has_key):
            self._workspace_login(prof, password)
            return
        self._show_shell(prof, password)

    def _workspace_login(self, prof, password):
        from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox
        from .session import run_bg
        cfg = sheets.load()
        dialog = QDialog(); dialog.setWindowTitle('دسترسی شخصی AutoReview')
        form = QFormLayout(dialog)
        url = QLineEdit(cfg.get('workspace_url', ''))
        token = QLineEdit(); token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('نشانی سرویس مدیر', url); form.addRow('کد دسترسی (در صورت ذخیره‌شدن خالی بماند)', token)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(controls)
        def connect():
            if not sheets.valid_webapp_url(url.text().strip()):
                QMessageBox.warning(dialog, 'دسترسی', 'نشانی سرویس مدیر معتبر نیست'); return
            cfg['workspace_url'] = url.text().strip()
            try:
                if token.text().strip(): workspace.save_access(prof['username'], token.text().strip())
                auth = workspace.access()
                if not auth or auth['username'] != workspace.username(prof['username']): raise ValueError()
            except Exception:
                QMessageBox.warning(dialog, 'دسترسی', 'کد دسترسی مخصوص همین حساب را از مدیر بگیر'); return
            controls.setEnabled(False)
            def done(result):
                if result.get('version') != 4:
                    failed('نسخه سرویس با اپ سازگار نیست'); return
                cfg.update(auth_mode='workspace', workflow_sync=True)
                sheets.save(cfg)
                prof['workspace_role'] = result['user']['role']
                dialog.accept()
                self._show_shell(prof, password)
            def failed(error):
                controls.setEnabled(True); QMessageBox.warning(dialog, 'دسترسی', str(error))
            run_bg(lambda _p: workspace.call('whoami', cfg), done, failed)
        controls.accepted.connect(connect); controls.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.show_login('ورود به فضای مشترک کامل نشد')

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
