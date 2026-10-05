from pathlib import Path

p = Path(r"C:\AutoReview\autoreview\ui.py")
t = p.read_text(encoding="utf-8")

style = '''
STYLE = """
* { font-family: 'Segoe UI', 'Tahoma'; font-size: 13px; color: #1e2433; }
QMainWindow, QDialog { background: #f3f5fa; }
QFrame#card { background: #ffffff; border: 1px solid #e3e7f0; border-radius: 14px; }
QLabel#title { font-size: 20px; font-weight: 700; }
QLabel#subtitle { color: #6b7590; }
QLabel#banner { background: #fff6dd; border: 1px solid #f0d98c; border-radius: 10px; padding: 9px 12px; color: #6b5300; }
QLabel#muted { color: #6b7590; }
QPushButton { background: #ffffff; border: 1px solid #d5daea; border-radius: 9px; padding: 8px 14px; }
QPushButton:hover { background: #eef1fb; border-color: #b9c2e6; }
QPushButton:focus { border: 2px solid #4f46e5; }
QPushButton:disabled { color: #a3abc2; background: #f3f5fa; }
QPushButton#primary { background: #4f46e5; color: #ffffff; border: none; font-weight: 600; padding: 9px 22px; }
QPushButton#primary:hover { background: #4338ca; }
QPushButton#primary:disabled { background: #c7c9f0; color: #ffffff; }
QPushButton#danger { color: #b42318; border-color: #f2c4c0; }
QSpinBox { background: #ffffff; border: 1px solid #d5daea; border-radius: 8px; padding: 5px 8px; min-width: 70px; }
QCheckBox { spacing: 8px; }
QProgressBar { background: #e6e9f4; border: none; border-radius: 6px; max-height: 12px; text-align: center; color: transparent; }
QProgressBar::chunk { background: #4f46e5; border-radius: 6px; }
QTableWidget { background: #ffffff; alternate-background-color: #f8f9fd; border: none; gridline-color: transparent; selection-background-color: #dfe3fb; selection-color: #1e2433; }
QHeaderView::section { background: #eef1f9; border: none; border-bottom: 1px solid #dde2f0; padding: 9px 10px; font-weight: 600; color: #46506b; }
QFrame#tile { border-radius: 12px; }
QLabel#tileNum { font-size: 26px; font-weight: 700; }
QLabel#tileCap { font-size: 12px; }
"""

TILES = (("APPROVE", "تایید", "#e3f4e7", "#166534"), ("EDIT", "نیاز به اصلاح", "#fff3d6", "#8a5a00"),
         ("CANCEL", "لغو", "#fbe1e1", "#9b1c1c"), ("MANUAL", "بررسی دستی", "#e6e9f6", "#3b4470"))
'''
t = t.replace("\n\nclass Bridge(QObject):", style + "\n\nclass Bridge(QObject):", 1)

a = t.index("        root = QWidget(); self.setCentralWidget(root)")
b = t.index("        self.timer = QTimer(self)")
new = '''        self.setStyleSheet(STYLE)
        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root); lay.setContentsMargins(22, 18, 22, 18); lay.setSpacing(14)

        head = QHBoxLayout()
        col = QVBoxLayout(); col.setSpacing(2)
        ttl = QLabel("AutoReview"); ttl.setObjectName("title")
        sub = QLabel("بررسی خودکار ثبت‌نام‌های آنلاین — پس‌زمینه، بدون باز شدن مرورگر"); sub.setObjectName("subtitle")
        col.addWidget(ttl); col.addWidget(sub)
        head.addLayout(col); head.addStretch(1)
        b_settings = QPushButton("تنظیم حداقل محصول"); b_settings.clicked.connect(self.open_settings)
        head.addWidget(b_settings)
        lay.addLayout(head)
        banner = QLabel("حالت آزمایشی: اپ فقط می‌خواند و پیشنهاد می‌دهد. هیچ تغییری در NBO یا شیت‌ها اعمال نمی‌شود.")
        banner.setObjectName("banner")
        lay.addWidget(banner)

        def card():
            f = QFrame(); f.setObjectName("card")
            v = QVBoxLayout(f); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(10)
            return f, v

        # --- sources
        c1, v1 = card()
        r1 = QHBoxLayout()
        self.nbo_label = QLabel("فایل خروجی NBO: انتخاب نشده"); self.nbo_label.setObjectName("muted")
        b_nbo = QPushButton("انتخاب فایل NBO"); b_nbo.clicked.connect(self.pick_nbo)
        r1.addWidget(b_nbo); r1.addWidget(self.nbo_label); r1.addStretch(1)
        r2 = QHBoxLayout()
        self.crm_label = QLabel(self.crm_note); self.crm_label.setObjectName("muted")
        b_crm = QPushButton("دریافت خودکار از CRM"); b_crm.clicked.connect(self.fetch_crm)
        b_login = QPushButton("ورود به CRM"); b_login.clicked.connect(self.crm_login)
        b_file = QPushButton("فایل CRM (جایگزین)"); b_file.clicked.connect(self.pick_crm_file)
        for w in (b_crm, b_login, b_file, self.crm_label):
            r2.addWidget(w)
        r2.addStretch(1)
        v1.addLayout(r1); v1.addLayout(r2)
        lay.addWidget(c1)

        # --- batch + controls
        c2, v2 = card()
        opt = QHBoxLayout()
        self.size = QSpinBox(); self.size.setRange(10, 2000); self.size.setValue(int(load_json("rules.json")["backlog"]["batch_size"]))
        self.batch_no = QSpinBox(); self.batch_no.setRange(1, 1)
        self.with_cip = QCheckBox("شامل COMMERCIAL_IN_PROGRESS")
        self.plan = QLabel(""); self.plan.setObjectName("muted")
        for w in (QLabel("اندازه‌ی دسته"), self.size, QLabel("دسته‌ی شماره"), self.batch_no, self.with_cip):
            opt.addWidget(w)
        opt.addSpacing(12); opt.addWidget(self.plan); opt.addStretch(1)
        self.size.valueChanged.connect(self.plan_batches); self.with_cip.toggled.connect(self.plan_batches)
        ctl = QHBoxLayout()
        self.b_start = QPushButton("شروع بررسی"); self.b_start.setObjectName("primary"); self.b_start.clicked.connect(self.start)
        self.b_pause = QPushButton("توقف موقت"); self.b_pause.clicked.connect(self.toggle_pause)
        self.b_stop = QPushButton("توقف"); self.b_stop.setObjectName("danger"); self.b_stop.clicked.connect(self.stop)
        self.b_export = QPushButton("خروجی Excel"); self.b_export.clicked.connect(self.export_xlsx)
        for w in (self.b_start, self.b_pause, self.b_stop):
            ctl.addWidget(w)
        ctl.addStretch(1); ctl.addWidget(self.b_export)
        v2.addLayout(opt); v2.addLayout(ctl)
        lay.addWidget(c2)

        # --- progress + tiles
        c3, v3 = card()
        self.counts = QLabel(""); self.counts.setObjectName("muted")
        self.bar = QProgressBar(); self.bar.setTextVisible(False)
        tiles = QHBoxLayout(); tiles.setSpacing(12)
        self.tile_nums = {}
        for key, cap, bg, fg in TILES:
            tf = QFrame(); tf.setObjectName("tile"); tf.setStyleSheet("QFrame#tile{background:%s;}" % bg)
            tv = QVBoxLayout(tf); tv.setContentsMargins(14, 10, 14, 10); tv.setSpacing(0)
            num = QLabel("0"); num.setObjectName("tileNum"); num.setStyleSheet("color:%s;" % fg)
            c = QLabel(cap); c.setObjectName("tileCap"); c.setStyleSheet("color:%s;" % fg)
            tv.addWidget(num); tv.addWidget(c)
            self.tile_nums[key] = num
            tiles.addWidget(tf)
        v3.addWidget(self.counts); v3.addWidget(self.bar); v3.addLayout(tiles)
        lay.addWidget(c3)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["کد درخواست", "وب‌سایت", "دسته‌بندی", "تصمیم", "دلیل (کد NBO)", "توضیح"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((130, 220, 160, 110, 300)):
            self.table.setColumnWidth(i, w)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table, 1)
        self.status = QLabel(""); self.status.setObjectName("muted"); lay.addWidget(self.status)

'''
t = t[:a] + new + t[b:]

old_counts = 'self.counts.setText(f"{STATE_FA.get(p.state, p.state)} — {p.done} از {p.total}   |   تایید {c[\'APPROVE\']}   اصلاح {c[\'EDIT\']}   لغو {c[\'CANCEL\']}   دستی {c[\'MANUAL\']}")'
assert old_counts in t
t = t.replace(old_counts, 'self.counts.setText(f"{STATE_FA.get(p.state, p.state)} — {p.done} از {p.total} درخواست بررسی شد")\n        for k, lab in self.tile_nums.items():\n            lab.setText(str(c.get(k, 0)))')
old_item = 'it = QTableWidgetItem(txt); it.setBackground(QColor(COLORS.get(r["action"], "#ffffff"))); it.setForeground(QColor("#111"))'
assert old_item in t
t = t.replace(old_item, 'it = QTableWidgetItem(txt)\n                    if j == 3:\n                        it.setBackground(QColor(COLORS.get(r["action"], "#ffffff"))); it.setForeground(QColor("#1e2433"))')
t = t.replace("self.resize(1180, 760)", "self.resize(1220, 820)")
p.write_text(t, encoding="utf-8")
print("patched")
