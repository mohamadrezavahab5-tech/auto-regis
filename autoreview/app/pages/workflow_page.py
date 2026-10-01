"""Human Online/Instore decisions, shared with the owner's sheet."""
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget)

from ... import sheets, workflow, workspace
from ..session import run_bg
from ...texts import ACTION_FA
from ..widgets import button, label, num


class WorkflowPage(QWidget):
    title = 'گردش کار'
    subtitle = 'نظر دو تیم، تصمیم دستی و وضعیت همگام‌سازی با شیت اختصاصی'

    def __init__(self, session, shell):
        super().__init__()
        self.session, self.shell = session, shell
        self.setObjectName('page')
        layout = QVBoxLayout(self)
        layout.addWidget(label('پیشنهاد موتور با تأیید انسانی جداست. برای درخواست مشترک، نظر تأیید هر دو تیم لازم است. '
                               'هیچ‌یک از این دکمه‌ها وضعیت NBO را تغییر نمی‌دهد.', 'muted', wrap=True))
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('جستجو در کد درخواست یا وب‌سایت')
        self.search.textChanged.connect(self.render)
        bar.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.addItem('همه', '')
        for k, v in workflow.STATES.items(): self.filter.addItem(v, k)
        self.filter.currentIndexChanged.connect(self.render)
        bar.addWidget(self.filter)
        b = button('همگام‌سازی اکنون', None, 'refresh')
        b.clicked.connect(lambda: session.sync_workflow(force=True))
        bar.addWidget(b)
        layout.addLayout(bar)
        self.status = label('', 'muted', wrap=True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(['کد درخواست','مسیر','پیشنهاد موتور','نظر Online','نظر Instore','وضعیت','نسخه','وب‌سایت'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i,w in enumerate((125,115,130,100,100,235,65)): self.table.setColumnWidth(i,w)
        self.table.itemSelectionChanged.connect(self.selection)
        layout.addWidget(self.table, 1)
        self.details = label('', 'muted', wrap=True, selectable=True)
        layout.addWidget(self.details)
        controls = QHBoxLayout()
        for team, text in [('online','ثبت نظر Online'), ('instore','ثبت نظر Instore')]:
            b = button(text, 'primary' if team=='online' else None, 'check')
            b.clicked.connect(lambda _=False, t=team: self.decide(t))
            controls.addWidget(b)
        b = button('باز کردن در NBO', None, 'nbo')
        b.clicked.connect(self.open_nbo)
        controls.addWidget(b)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.rows = []
        session.data_changed.connect(self.on_show)
        session.workflow_sync_changed.connect(self.show_status)

    def on_show(self):
        db = self.session.db()
        try: self.rows = workflow.cases(db)
        finally: db.close()
        self.render()
        self.show_status()

    def show_status(self):
        db = self.session.db()
        try: pending = workflow.pending_count(db)
        finally: db.close()
        self.status.setText(f"{self.session.workflow_sync_status} — {num(pending)} پرونده منتظر ارسال")

    def render(self, *_):
        previous = self.selected()
        q, state = self.search.text().strip().lower(), self.filter.currentData()
        self.visible_rows = [r for r in self.rows if (not state or r['state']==state) and
                             (not q or q in (r['smr']+' '+r.get('site','')).lower())]
        self.table.setRowCount(0)
        for i,r in enumerate(self.visible_rows):
            self.table.insertRow(i)
            values = [r['smr'], 'Online + Instore' if r['channel']=='both' else 'Online',
                      ACTION_FA.get((r.get('suggestion') or {}).get('action'), 'بررسی‌نشده'),
                      ACTION_FA.get((r.get('online') or {}).get('action'), 'منتظر'),
                      ACTION_FA.get((r.get('instore') or {}).get('action'), 'منتظر' if r['channel']=='both' else 'لازم نیست'),
                      r['state_fa'], str(r['revision']), r.get('site','')]
            for j,value in enumerate(values): self.table.setItem(i,j,QTableWidgetItem(value))
        if previous:
            for i,r in enumerate(self.visible_rows):
                if r['smr']==previous['smr']: self.table.selectRow(i); break

    def selected(self):
        i = self.table.currentRow()
        visible = getattr(self, 'visible_rows', [])
        return visible[i] if 0 <= i < len(visible) else None

    def selection(self):
        r = self.selected()
        if not r: self.details.setText('یک درخواست را انتخاب کن'); return
        parts = []
        for team in ('online','instore'):
            decision = r.get(team)
            if decision: parts.append(f"{team}: {decision['actor']} — {decision['note']} — {decision['at']}")
        self.details.setText(html.escape(' | '.join(parts) or 'هنوز نظر انسانی ثبت نشده است'))

    def open_nbo(self):
        r = self.selected()
        if r: self.shell.open_in_nbo(r['smr'])

    def decide(self, team):
        r = self.selected()
        if not r: return
        if not r['active'] or (team=='instore' and r['channel']!='both'):
            QMessageBox.information(self,'گردش کار','این درخواست برای این تصمیم در صف فعال نیست.'); return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{r['smr']} — نظر {team}")
        form = QFormLayout(dlg)
        action = QComboBox()
        action.addItem('انتخاب تصمیم…','')
        for key in ('APPROVE','EDIT','CANCEL','MANUAL','REOPEN'):
            action.addItem(ACTION_FA.get(key,'بازگشایی و حذف تأیید قبلی'),key)
        reason = QComboBox()
        labels = sheets.nbo_labels()
        def fill_reasons(*_):
            reason.clear(); reason.addItem('انتخاب دلیل…','')
            values = labels.get(str(action.currentData()).lower(),{})
            for code,text in values.items(): reason.addItem(text,code)
            reason.setEnabled(bool(values))
        action.currentIndexChanged.connect(fill_reasons)
        fill_reasons()
        note = QTextEdit(); note.setPlaceholderText('توضیح بررسی یا مرجع نظر تیم؛ الزامی')
        note.setMaximumHeight(100)
        form.addRow('تصمیم',action); form.addRow('دلیل NBO',reason); form.addRow('توضیح',note)
        form.addRow(label('ثبت‌کننده: '+self.session.user_label()+' — این ثبت، تغییر وضعیت در NBO نیست.',wrap=True))
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        def save():
            if sheets.load().get('auth_mode') == 'workspace':
                buttons.setEnabled(False)
                choice, explanation, code = action.currentData(), note.toPlainText(), reason.currentData() or ''
                def complete(result):
                    db=self.session.db()
                    try: workspace.cache_cases(db,[result['case']])
                    finally: db.close()
                    dlg.accept(); self.session.data_changed.emit()
                def failed(error):
                    buttons.setEnabled(True)
                    QMessageBox.warning(dlg,'ثبت تصمیم',str(error))
                run_bg(lambda _p: workspace.decide(r['smr'],team,choice,explanation,
                       r['revision'],code), complete, failed)
                return
            db = self.session.db()
            try:
                workflow.decide(db,r['smr'],team,action.currentData(),self.session.user_label(),
                                note.toPlainText(),r['revision'],reason.currentData() or '',labels)
            except ValueError as e:
                QMessageBox.warning(dlg,'ثبت تصمیم',str(e)); return
            finally: db.close()
            dlg.accept(); self.session.data_changed.emit(); self.session.sync_workflow()
        buttons.accepted.connect(save); buttons.rejected.connect(dlg.reject)
        form.addRow(buttons); dlg.exec()
