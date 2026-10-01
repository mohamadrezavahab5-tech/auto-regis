"""'AutoReview.exe --uninstall' - what Windows 'Installed apps > Uninstall' runs.

Removes the shortcuts, the Installed-apps entry and the program folder. The person's own data (database, saved logins,
settings, logs) is kept unless they tick the box - then it is removed too."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox

from .. import winsetup
from . import icons, theme


def _close_other_instances():
    me = os.getpid()
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {winsetup.EXE_NAME}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                       creationflags=winsetup.NO_WINDOW)
    for line in r.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) != me:
            subprocess.run(["taskkill", "/PID", parts[1], "/T", "/F"], capture_output=True, creationflags=winsetup.NO_WINDOW)


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setWindowIcon(icons.app_icon())
    theme.apply(app)
    box = QMessageBox(QMessageBox.Icon.Question, "حذف AutoReview", "AutoReview از این کامپیوتر حذف شود؟",
                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.button(QMessageBox.StandardButton.Yes).setText("حذف")
    box.button(QMessageBox.StandardButton.No).setText("انصراف")
    keep = QCheckBox("داده‌ها، ورودهای ذخیره‌شده و تنظیمات من هم پاک شود")
    box.setCheckBox(keep)
    if box.exec() != QMessageBox.StandardButton.Yes:
        return 0
    _close_other_instances()
    winsetup.remove_shortcuts()
    winsetup.remove_registration()
    winsetup.set_autostart(Path(sys.executable), False)                 # no orphan "start with Windows" entry
    if keep.isChecked():
        shutil.rmtree(winsetup.user_data_dir(), ignore_errors=True)
    if getattr(sys, "frozen", False):
        winsetup.schedule_removal(Path(sys.executable).resolve().parent)
    QMessageBox.information(None, "حذف AutoReview", "AutoReview حذف شد." + ("" if keep.isChecked() else
                            "\nداده‌ها و تنظیمات تو نگه داشته شد و با نصب دوباره برمی‌گردد."))
    return 0
