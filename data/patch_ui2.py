from pathlib import Path

p = Path(r"C:\AutoReview\autoreview\ui.py")
t = p.read_text(encoding="utf-8")


def sub(old, new):
    global t
    assert old in t, old
    t = t.replace(old, new, 1)


sub("from . import backlog, crm_sync, export, imports, store", "from . import backlog, crm_sync, export, imports, nbo_session, store")
sub("    crm_done = Signal(str, bool)", "    crm_done = Signal(str, bool)\n    nbo_done = Signal(str, bool)")
sub("        self.bridge.crm_done.connect(self.on_crm_done)", "        self.bridge.crm_done.connect(self.on_crm_done)\n        self.bridge.nbo_done.connect(self.on_nbo_done)")
sub('''        b_nbo = QPushButton("انتخاب فایل NBO"); b_nbo.clicked.connect(self.pick_nbo)
        r1.addWidget(b_nbo); r1.addWidget(self.nbo_label); r1.addStretch(1)''',
    '''        b_nbo = QPushButton("دریافت خودکار از NBO"); b_nbo.clicked.connect(self.fetch_nbo)
        b_nbo_login = QPushButton("ورود به NBO"); b_nbo_login.clicked.connect(self.nbo_login)
        b_nbo_file = QPushButton("فایل NBO (جایگزین)"); b_nbo_file.clicked.connect(self.pick_nbo)
        for w in (b_nbo, b_nbo_login, b_nbo_file, self.nbo_label):
            r1.addWidget(w)
        r1.addStretch(1)''')

sub("    def plan_batches(self):", '''    # ---- NBO (automatic)
    def nbo_login(self):
        QMessageBox.information(self, "ورود به NBO", "یک پنجره‌ی Chrome باز می‌شود. خودت نام کاربری، رمز و کد OTP را وارد کن؛ بعد از ورود خودکار بسته می‌شود.\\nاپ رمز را نمی‌بیند و ذخیره نمی‌کند.")
        self.nbo_label.setText("NBO: منتظر ورود…")

        def work():
            try:
                nbo_session.login_interactive()
                self.bridge.nbo_done.emit("NBO: ورود انجام شد", True)
            except Exception as e:
                self.bridge.nbo_done.emit(f"NBO: {e}", False)
        threading.Thread(target=work, daemon=True).start()

    def fetch_nbo(self):
        if not nbo_session.have_session():
            self.nbo_login(); return
        self.nbo_label.setText("NBO: در حال دریافت خروجی…")
        rules = load_json("rules.json")
        pending = list(rules["backlog"]["statuses"]) + (list(rules["backlog"].get("optional_statuses", [])) if self.with_cip.isChecked() else [])
        approved = list(rules["approved_statuses"]["nbo"])

        def work():
            try:
                d = data_dir()
                pend_file = nbo_session.download_export(pending, d / "nbo_pending.xlsx")
                appr_file = nbo_session.download_export(approved, d / "nbo_approved.xlsx")
                self.rows_all = imports.read_export(pend_file, "nbo")
                self.approved_nbo = [{"id": r["smr"], "site": r["site"]} for r in imports.read_export(appr_file, "nbo")]
                self.bridge.nbo_done.emit(f"NBO: {len(self.rows_all)} در انتظار، {len(self.approved_nbo)} تاییدشده", True)
            except nbo_session.NboLoginRequired as e:
                self.bridge.nbo_done.emit(f"NBO: {e}", False)
            except Exception as e:
                self.bridge.nbo_done.emit(f"NBO: {e}", False)
        threading.Thread(target=work, daemon=True).start()

    def on_nbo_done(self, text, ok):
        self.nbo_label.setText(text)
        if ok:
            self.plan_batches()
        else:
            QMessageBox.warning(self, "NBO", text)

    def plan_batches(self):''')
p.write_text(t, encoding="utf-8")
print("ok")
