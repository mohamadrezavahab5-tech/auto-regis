"""Owner provisions per-person access; colleagues use a revocable personal token."""
import secrets

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox,QComboBox,QDialog,QDialogButtonBox,QFormLayout,
    QHBoxLayout,QLineEdit,QMessageBox,QTableWidget,QTableWidgetItem,QVBoxLayout,QWidget)

from ... import sheets, workspace, google_credentials
from ...google_sheet import Client
from ..session import run_bg
from ..widgets import button,label


class UsersPage(QWidget):
    title='کاربران'
    subtitle='مدیریت دسترسی شخصی همکاران توسط ادمین'

    def __init__(self,session,shell):
        super().__init__(); self.session,self.shell=session,shell
        self.setObjectName('page')
        layout=QVBoxLayout(self)
        layout.addWidget(label('هر همکار کد دسترسی مخصوص خودش دارد؛ کلید Service Account فقط روی سیستم مدیر است. '
            'نقش‌ها در سرویس گوگل کنترل می‌شوند. قبل از انتشار سرویس، ورود همکاران فعال نیست.', 'muted',wrap=True))
        self.status=label('','muted',wrap=True); layout.addWidget(self.status)
        row=QHBoxLayout()
        for title,fn in [('به‌روزرسانی فهرست',self.refresh),('ایجاد / تغییر دسترسی',self.edit_user),('کپی کد سرویس کاربران',self.copy_server)]:
            b=button(title,None,'user'); b.clicked.connect(fn); row.addWidget(b)
        layout.addLayout(row)
        self.table=QTableWidget(0,3); self.table.setHorizontalHeaderLabels(['نام کاربری','نقش','وضعیت'])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table,1)

    def owner(self):
        try: return workspace.username(self.session.profile.get('username'))==workspace.ADMIN
        except ValueError: return False

    def on_show(self):
        if self.owner(): self.refresh()
        else: self.status.setText('این بخش فقط برای مدیر اصلی است')

    def refresh(self):
        if not self.owner(): return
        cfg=sheets.load()
        def fetch(_p):
            if cfg.get('auth_mode')=='workspace': return workspace.call('list_users',cfg)['users']
            with Client(cfg['own_sheet_id']) as google: return workspace.users(google)
        def show(users):
            self.table.setRowCount(len(users))
            for i,u in enumerate(users):
                for j,v in enumerate([u['username'],workspace.ROLES.get(u['role'],u['role']),'فعال' if u['enabled'] else 'غیرفعال']):
                    self.table.setItem(i,j,QTableWidgetItem(v))
            self.status.setText('فهرست از شیت خوانده شد. قطع دسترسی در درخواست بعدی همکار اعمال می‌شود.')
        run_bg(fetch,show,lambda e:self.status.setText(str(e)))

    def copy_server(self):
        if not self.owner(): return
        QGuiApplication.clipboard().setText(workspace.server_code())
        QMessageBox.information(self,'سرویس کاربران','کد سرویس کپی شد؛ در Apps Script همین شیت قرار بده و به‌صورت Web app منتشر کن. '
            'Execute as: Me و دسترسی Anyone؛ احراز هویت و نقش هر درخواست با کد شخصی انجام می‌شود. '
            'URL انتشار را در اتصال‌ها وارد کن. کد کپی‌شده فاقد کلید Service Account است.')

    def edit_user(self):
        if not self.owner(): return
        dlg=QDialog(self); dlg.setWindowTitle('دسترسی همکار'); form=QFormLayout(dlg)
        name=QLineEdit(); name.setPlaceholderText('نام کاربری CRM')
        role=QComboBox()
        for k in ('online','instore','viewer'): role.addItem(workspace.ROLES[k],k)
        enabled=QCheckBox('فعال'); enabled.setChecked(True)
        rotate=QCheckBox('صدور کد جدید و باطل‌کردن کد قبلی')
        form.addRow('کاربر',name); form.addRow('نقش',role); form.addRow(enabled); form.addRow(rotate)
        controls=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        def save():
            try: user=workspace.username(name.text())
            except ValueError as e: QMessageBox.warning(dlg,'نام کاربری',str(e)); return
            values=(user,role.currentData(),enabled.isChecked(),rotate.isChecked())
            cfg=sheets.load(); actor=self.session.profile['username']
            controls.setEnabled(False)
            def work(_p):
                u,r,on,new=values
                if cfg.get('auth_mode')=='workspace':
                    existing=workspace.call('list_users',cfg)['users']
                    token=secrets.token_urlsafe(32) if new or not any(x['username']==u for x in existing) else None
                    args=dict(user=u,role=r,enabled=on)
                    if token: args['token_hash']=workspace.hash_token(token)
                    workspace.call('set_user',cfg,**args)
                    return token
                with Client(cfg['own_sheet_id']) as google:
                    return workspace.provision(google,actor,u,r,on,new)
            def complete(token):
                dlg.accept(); self.refresh()
                if token:
                    # Deliberate one-time user handoff; never log or send through chat.
                    box=QDialog(self); box.setWindowTitle('کد دسترسی شخصی — یک‌بار نمایش')
                    v=QVBoxLayout(box); v.addWidget(label('این کد را فقط به '+user+' تحویل بده. در اپ همکار از اتصال‌ها وارد می‌شود.',wrap=True))
                    field=QLineEdit(token); field.setReadOnly(True); field.setLayoutDirection(Qt.LayoutDirection.LeftToRight); v.addWidget(field)
                    b=button('بستن'); b.clicked.connect(box.accept); v.addWidget(b); box.exec()
            def failed(e): controls.setEnabled(True); QMessageBox.warning(dlg,'ثبت دسترسی',str(e))
            run_bg(work,complete,failed)
        controls.accepted.connect(save); controls.rejected.connect(dlg.reject); form.addRow(controls); dlg.exec()
