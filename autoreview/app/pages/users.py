"""Users (owner only): colleagues get a personal, revocable access code with a role - Online team, Instore team or view only.
The service-account key never leaves the owner's PC; colleagues go through the Google-hosted service in the owner's sheet."""
import secrets

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMessageBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ... import google_credentials, sheets, workspace
from ...google_sheet import Client
from ..session import run_bg
from ..theme import C
from ..widgets import Card, StepList, button, label
from .common import ScrollPage


class UsersPage(ScrollPage):
    title = "کاربران"
    subtitle = "دسترسی همکاران به گردش کار — فقط مدیر"

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell = session, shell
        how = Card(soft=True)
        how.header("همکاران چطور وصل می‌شوند", None)
        self.how = StepList()
        self.how.set_steps([
            ("۱. یک بار: «کپی کد سرویس» را بزن و در Apps Script همین شیت بچسبان", None,
             "Deploy ← New deployment ← Web app، با Execute as: Me و Who has access: Anyone. نشانی را به همکاران بده."),
            ("۲. برای هر همکار «افزودن / تغییر دسترسی» را بزن", None, "کد شخصی فقط یک بار نشان داده می‌شود؛ خودت به همکار بده."),
            ("۳. همکار اپ را نصب می‌کند، با CRM خودش وارد می‌شود و نشانی + کد را در «اتصال‌ها» می‌زند", None,
             "نقش Instore فقط نظر Instore ثبت می‌کند؛ قطع دسترسی از درخواست بعدی همکار اعمال می‌شود."),
        ])
        how.lay.addWidget(self.how)
        self.body.addWidget(how)

        card = Card()
        b_add = button("افزودن / تغییر دسترسی", "primary", "user")
        b_add.clicked.connect(self.edit_user)
        b_refresh = button("به‌روزرسانی", None, "refresh")
        b_refresh.clicked.connect(self.refresh)
        b_code = button("کپی کد سرویس", None, "copy")
        b_code.clicked.connect(self.copy_server)
        card.header("همکاران", "از تب Users شیت خودت خوانده می‌شود", [b_add, b_refresh, b_code])
        self.status = label("", "caption", wrap=True)
        card.lay.addWidget(self.status)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["نام کاربری", "نقش", "وضعیت"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 160)
        self.table.setMinimumHeight(260)
        card.lay.addWidget(self.table)
        self.body.addWidget(card)
        self.body.addStretch(1)

    def owner(self):
        try:
            return workspace.username(self.session.profile.get("username")) == workspace.ADMIN
        except ValueError:
            return False

    def on_show(self):
        if self.owner():
            self.refresh()
        else:
            self.status.setText("این بخش فقط برای مدیر اصلی (mohammadreza.vahab) است.")

    def refresh(self):
        if not self.owner():
            return
        cfg = sheets.load()
        if cfg.get("auth_mode") != "workspace" and not google_credentials.available():
            self.status.setText("اول شیت خودت را در «اتصال‌ها» وصل کن (فایل کلید)؛ فهرست همکاران در تب Users همان شیت است.")
            return
        self.status.setText("در حال خواندن…")

        def fetch(_p):
            if cfg.get("auth_mode") == "workspace":
                return workspace.call("list_users", cfg)["users"]
            with Client(cfg["own_sheet_id"]) as google:
                return workspace.users(google)

        def show(users):
            self.table.setRowCount(len(users))
            for i, u in enumerate(users):
                for j, v in enumerate([u["username"], workspace.ROLES.get(u["role"], u["role"]), "فعال" if u["enabled"] else "غیرفعال"]):
                    item = QTableWidgetItem(v)
                    if j == 2:
                        item.setForeground(QColor(C["approve"] if u["enabled"] else C["text3"]))
                    self.table.setItem(i, j, item)
            self.status.setText(f"{len(users)} نفر — قطع دسترسی از درخواست بعدیِ همکار اعمال می‌شود.")

        def failed(e):
            self.status.setText(f"<span style='color:{C['danger']}'>{e}</span>")
        run_bg(fetch, show, failed)

    def copy_server(self):
        if not self.owner():
            return
        QGuiApplication.clipboard().setText(workspace.server_code())
        QMessageBox.information(self, "کد سرویس", "کپی شد. در Apps Script همین شیت بچسبان و به‌صورت Web app منتشر کن "
                                "(Execute as: Me، Who has access: Anyone). این کد فایل کلید ندارد؛ هر درخواست با کد شخصی همکار سنجیده می‌شود.")

    def edit_user(self):
        if not self.owner():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("دسترسی همکار")
        dlg.setMinimumWidth(420)
        form = QFormLayout(dlg)
        name = QLineEdit()
        name.setPlaceholderText("نام کاربری CRM همکار")
        name.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        role = QComboBox()
        for k in ("online", "instore", "viewer"):
            role.addItem(workspace.ROLES[k], k)
        enabled = QCheckBox("فعال")
        enabled.setChecked(True)
        rotate = QCheckBox("کد تازه بده و کد قبلی باطل شود")
        form.addRow("کاربر", name)
        form.addRow("نقش", role)
        form.addRow(enabled)
        form.addRow(rotate)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        controls.button(QDialogButtonBox.StandardButton.Save).setText("ذخیره")
        controls.button(QDialogButtonBox.StandardButton.Cancel).setText("انصراف")
        form.addRow(controls)
        controls.rejected.connect(dlg.reject)

        def save():
            try:
                user = workspace.username(name.text())
            except ValueError as e:
                QMessageBox.warning(dlg, "نام کاربری", str(e))
                return
            values = (user, role.currentData(), enabled.isChecked(), rotate.isChecked())
            cfg, actor = sheets.load(), self.session.profile["username"]
            controls.setEnabled(False)

            def work(_p):
                u, r, on, new = values
                if cfg.get("auth_mode") == "workspace":
                    existing = workspace.call("list_users", cfg)["users"]
                    token = secrets.token_urlsafe(32) if new or not any(x["username"] == u for x in existing) else None
                    args = dict(user=u, role=r, enabled=on)
                    if token:
                        args["token_hash"] = workspace.hash_token(token)
                    workspace.call("set_user", cfg, **args)
                    return token
                with Client(cfg["own_sheet_id"]) as google:
                    return workspace.provision(google, actor, u, r, on, new)

            def complete(token):
                dlg.accept()
                self.refresh()
                if token:                                   # shown once; never logged, never sent anywhere by the app
                    box = QDialog(self)
                    box.setWindowTitle("کد دسترسی شخصی — فقط همین یک بار")
                    v = QVBoxLayout(box)
                    v.addWidget(label(f"این کد را فقط به {user} بده؛ در «اتصال‌ها»ی اپ خودش وارد می‌کند.", wrap=True))
                    field = QLineEdit(token)
                    field.setReadOnly(True)
                    field.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
                    v.addWidget(field)
                    b = button("بستن", "primary")
                    b.clicked.connect(box.accept)
                    v.addWidget(b, 0, Qt.AlignmentFlag.AlignRight)
                    box.exec()

            def failed(e):
                controls.setEnabled(True)
                QMessageBox.warning(dlg, "ثبت دسترسی", str(e))
            run_bg(work, complete, failed)
        controls.accepted.connect(save)
        dlg.exec()
