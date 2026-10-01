"""AutoReview-Setup.exe - a Persian, right-to-left installer. Per-user install (no administrator prompt).

Install = unpack the program into %LOCALAPPDATA%\\Programs\\AutoReview, add Start-menu (and optionally desktop) shortcuts and
the 'Installed apps' entry (with uninstall). Running it again over an existing install updates the program files; the
person's data and settings live elsewhere and are never touched.
Silent mode for automated checks:  --silent [--dir PATH] [--no-shortcuts] [--no-registry] [--no-launch]"""
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QStackedWidget, QVBoxLayout, QWidget)

from autoreview import winsetup
from autoreview.app import icons, theme
from autoreview.app.widgets import button, label
from autoreview.version import __version__


def payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent / "build"))
    return base / "payload.zip"


def install(target: Path, shortcuts=True, desktop=True, registry=True, progress=None):
    size_kb = winsetup.extract(payload_path(), target, progress)
    exe = target / winsetup.EXE_NAME
    if not exe.exists():
        raise RuntimeError("پرونده‌ی برنامه در بسته‌ی نصب پیدا نشد.")
    if shortcuts:
        paths = winsetup.shortcut_paths()
        winsetup.create_shortcut(paths["start"], exe)
        if desktop:
            winsetup.create_shortcut(paths["desktop"], exe)
    if registry:
        winsetup.write_registration(target, __version__, size_kb)
    return exe


def silent(args):
    target = Path(args[args.index("--dir") + 1]) if "--dir" in args else winsetup.default_install_dir()
    log = Path(os.environ.get("TEMP", ".")) / "AutoReview-Setup.log"
    try:
        if winsetup.app_running():
            winsetup.close_app()
        exe = install(target, shortcuts="--no-shortcuts" not in args, registry="--no-registry" not in args)
        log.write_text(f"ok {exe}\n", encoding="utf-8")
        if "--no-launch" not in args:
            subprocess.Popen([str(exe)], cwd=str(target), close_fds=True)
        return 0
    except Exception:
        log.write_text(traceback.format_exc(), encoding="utf-8")
        return 1


class _Bridge(QObject):
    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str)


class Setup(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"نصب AutoReview {__version__}")
        self.setWindowIcon(icons.app_icon())
        self.setObjectName("canvas")
        self.setFixedSize(780, 500)
        self.existing = winsetup.read_registration()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        side = QFrame()
        side.setObjectName("rail")
        side.setFixedWidth(250)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(28, 36, 28, 28)
        logo = QLabel()
        logo.setPixmap(icons.logo_pixmap(56))
        sv.addWidget(logo)
        sv.addWidget(label("AutoReview", "brand"))
        sv.addWidget(label("بررسی خودکار ثبت‌نام‌های آنلاین", "railFootText", wrap=True))
        sv.addStretch(1)
        sv.addWidget(label(f"نسخه {__version__}", "brandSub"))
        outer.addWidget(side)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self._welcome())
        self.stack.addWidget(self._progress())
        self.stack.addWidget(self._done())
        self.bridge = _Bridge()
        self.bridge.progress.connect(lambda f: self.bar.setValue(int(f * 1000)))
        self.bridge.done.connect(self._finished)
        self.bridge.failed.connect(self._failed)

    def _page(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(34, 34, 34, 28)
        v.setSpacing(12)
        return w, v

    def _welcome(self):
        w, v = self._page()
        update = bool(self.existing)
        v.addWidget(label("به‌روزرسانی AutoReview" if update else "نصب AutoReview", "pageTitle"))
        if update:
            text = (f"نسخه‌ی {self.existing.get('DisplayVersion', '')} روی این کامپیوتر نصب است و به نسخه‌ی {__version__} به‌روز می‌شود. "
                    "داده‌ها، ورودها و تنظیمات تو دست نمی‌خورد.")
        else:
            text = ("برنامه فقط برای حساب ویندوز خودت نصب می‌شود (بدون نیاز به مدیر سیستم). "
                    "بعد از نصب، با نام کاربری و رمز CRM خودت وارد شو.")
        v.addWidget(label(text, "muted", wrap=True))
        v.addSpacing(8)
        v.addWidget(label("محل نصب", "h3"))
        row = QHBoxLayout()
        default = Path(self.existing["InstallLocation"]) if update and self.existing.get("InstallLocation") else winsetup.default_install_dir()
        self.path = QLineEdit(str(default))
        self.path.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.path.setReadOnly(True)
        row.addWidget(self.path, 1)
        change = button("تغییر…")
        change.clicked.connect(self._pick)
        change.setEnabled(not update)
        row.addWidget(change)
        v.addLayout(row)
        self.desktop = QCheckBox("میان‌بر روی دسکتاپ")
        self.desktop.setChecked(True)
        v.addWidget(self.desktop)
        v.addWidget(label("میان‌بر منوی Start و ورودی «Installed apps» (برای حذف) همیشه ساخته می‌شود.", "caption", wrap=True))
        v.addStretch(1)
        row2 = QHBoxLayout()
        row2.addStretch(1)
        cancel = button("انصراف")
        cancel.clicked.connect(self.close)
        go = button("به‌روزرسانی" if update else "نصب", "primary")
        go.setMinimumWidth(130)
        go.clicked.connect(self._start)
        row2.addWidget(cancel)
        row2.addWidget(go)
        v.addLayout(row2)
        return w

    def _progress(self):
        w, v = self._page()
        v.addWidget(label("در حال نصب…", "pageTitle"))
        self.step = label("باز کردن بسته‌ی برنامه", "muted")
        v.addWidget(self.step)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        v.addWidget(self.bar)
        v.addStretch(1)
        return w

    def _done(self):
        w, v = self._page()
        self.done_title = label("نصب شد", "pageTitle")
        v.addWidget(self.done_title)
        v.addWidget(label("AutoReview از منوی Start (و دسکتاپ) در دسترس است. برای حذف: Settings ← Apps ← Installed apps.", "muted", wrap=True))
        self.launch = QCheckBox("الان AutoReview را باز کن")
        self.launch.setChecked(True)
        v.addWidget(self.launch)
        v.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        fin = button("پایان", "primary")
        fin.setMinimumWidth(130)
        fin.clicked.connect(self._close_done)
        row.addWidget(fin)
        v.addLayout(row)
        return w

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "محل نصب", str(Path(self.path.text()).parent))
        if d:
            self.path.setText(str(Path(d) / "AutoReview"))

    def _start(self):
        if winsetup.app_running():
            if QMessageBox.question(self, "AutoReview باز است", "برای نصب باید AutoReview بسته شود. ببندمش و ادامه بدهم؟") \
                    != QMessageBox.StandardButton.Yes:
                return
            winsetup.close_app()
        target = Path(self.path.text())
        desktop = self.desktop.isChecked()
        self.stack.setCurrentIndex(1)

        def work():
            try:
                exe = install(target, desktop=desktop, progress=self.bridge.progress.emit)
                self.bridge.done.emit(exe)
            except Exception as e:
                self.bridge.failed.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, exe):
        self.exe = exe
        self.stack.setCurrentIndex(2)

    def _failed(self, msg):
        QMessageBox.critical(self, "نصب انجام نشد", f"نصب کامل نشد:\n{msg}")
        self.stack.setCurrentIndex(0)

    def _close_done(self):
        if self.launch.isChecked():
            subprocess.Popen([str(self.exe)], cwd=str(Path(self.exe).parent), close_fds=True)
        self.close()


def main():
    args = sys.argv[1:]
    if "--silent" in args:
        return silent(args)
    app = QApplication(sys.argv)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    theme.apply(app)
    w = Setup()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
