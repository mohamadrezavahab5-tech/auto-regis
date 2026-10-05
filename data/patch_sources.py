from pathlib import Path

R = Path(r"C:\AutoReview")


def sub(rel, old, new):
    p = R / rel
    t = p.read_text(encoding="utf-8")
    assert old in t, (rel, old[:60])
    p.write_text(t.replace(old, new, 1), encoding="utf-8")


# ---------- pipeline: both approved sets are mandatory, and the run records which sets it checked ----------
sub("autoreview/pipeline.py", '''def duplicate_decision(reasons):
    code = reason_code(reasons, "DUPLICATE_REQUEST")
    if code is None:
        return rulesmod.Decision(rulesmod.MANUAL, notes=["duplicate found but the NBO cancel reason is not unambiguous"], trace=["DUPLICATE"])
    return rulesmod.Decision(rulesmod.CANCEL, ["DUPLICATE_REQUEST"], [code], ["a request with the same website is already approved"],
                             ["FAIL DUPLICATE_REQUEST -> CANCEL"])''',
    '''def duplicate_decision(reasons, info=None):
    where = ""
    if info:
        where = " (" + " / ".join(x for x in (("NBO" if info.get("in_nbo") else ""), ("CRM" if info.get("in_crm") else "")) if x) + ": " + ", ".join(info.get("related", [])[:3]) + ")"
    code = reason_code(reasons, "DUPLICATE_REQUEST")
    if code is None:
        return rulesmod.Decision(rulesmod.MANUAL, notes=["duplicate found but the NBO cancel reason is not unambiguous" + where], trace=["DUPLICATE"])
    return rulesmod.Decision(rulesmod.CANCEL, ["DUPLICATE_REQUEST"], [code], ["the same website is already approved" + where],
                             ["FAIL DUPLICATE_REQUEST -> CANCEL"])''')
sub("autoreview/pipeline.py", '''        if self._thread and self._thread.is_alive():
            raise RuntimeError("a run is already in progress")
        self._stop.clear()''', '''        if self._thread and self._thread.is_alive():
            raise RuntimeError("a run is already in progress")
        if not approved_nbo or not approved_crm:            # NBO and CRM hold different requests: a duplicate can hide in either one
            raise ValueError("both the NBO and the CRM approved sets are required for the duplicate check")
        self._stop.clear()''')
sub("autoreview/pipeline.py", '''        already = store.done_ids(db, run_id)''', '''        with db:
            store.log(db, None, "RUN_SOURCES", {"run": run_id, "nbo_approved": len(approved_nbo), "crm_approved": len(approved_crm)})
        already = store.done_ids(db, run_id)''')
sub("autoreview/pipeline.py", '''decision, ev = duplicate_decision(self.reasons), {"duplicate_of": dupes[row["smr"]]["related"]}''',
    '''decision, ev = duplicate_decision(self.reasons, dupes[row["smr"]]), {"duplicate_of": dupes[row["smr"]]["related"]}''')

# ---------- store: read back which sources a run checked ----------
p = R / "autoreview" / "store.py"
t = p.read_text(encoding="utf-8")
t += '''

def run_sources(db, run_id):
    """-> {'nbo_approved': n, 'crm_approved': m} recorded when the run started, or None."""
    for (detail,) in db.execute("SELECT detail FROM audit WHERE stage = 'RUN_SOURCES' ORDER BY id DESC"):
        d = json.loads(detail)
        if d.get("run") == run_id:
            return d
    return None
'''
p.write_text(t, encoding="utf-8")

# ---------- export: say which sources were checked ----------
sub("autoreview/export.py", "def write_xlsx(path, results: list, dry_run: bool = True):", "def write_xlsx(path, results: list, dry_run: bool = True, sources: dict = None):")
sub("autoreview/export.py", '''    if dry_run:
        ws.write(8, 0, "حالت آزمایشی: هیچ تغییری در NBO اعمال نشده است.")''', '''    if dry_run:
        ws.write(8, 0, "حالت آزمایشی: هیچ تغییری در NBO اعمال نشده است.")
    if sources:
        ws.write(9, 0, "تکراری‌ها با هر دو منبع بررسی شد — NBO: %s تاییدشده، CRM: %s تاییدشده" % (sources.get("nbo_approved"), sources.get("crm_approved")))''')

# ---------- UI: start only with both sources ----------
sub("autoreview/ui.py", 'self.crm_note = "CRM: بارگذاری نشده (تکراری‌های CRM بررسی نمی‌شوند)"', 'self.crm_note = "CRM: بارگذاری نشده (لازم است)"')
sub("autoreview/ui.py", '''        for w in (self.b_start, self.b_pause, self.b_stop):
            ctl.addWidget(w)
        ctl.addStretch(1); ctl.addWidget(self.b_export)''', '''        self.sources_hint = QLabel(""); self.sources_hint.setObjectName("muted")
        for w in (self.b_start, self.b_pause, self.b_stop):
            ctl.addWidget(w)
        ctl.addSpacing(12); ctl.addWidget(self.sources_hint)
        ctl.addStretch(1); ctl.addWidget(self.b_export)''')
sub("autoreview/ui.py", '''    def sync_buttons(self):
        st = self.runner.progress.state if self.runner else "idle"
        active = st in ("running", "paused", "stopping")
        self.b_start.setEnabled(not active and bool(self.rows_all))''', '''    def sources_missing(self):
        """NBO and CRM hold different requests, so a duplicate can hide in either: both must be loaded before a run."""
        miss = []
        if not (self.rows_all and self.approved_nbo):
            miss.append("NBO")
        if not self.approved_crm:
            miss.append("CRM")
        return miss

    def sync_buttons(self):
        st = self.runner.progress.state if self.runner else "idle"
        active = st in ("running", "paused", "stopping")
        miss = self.sources_missing()
        self.sources_hint.setText("NBO ✓ و CRM ✓ — تکراری‌ها با هر دو بررسی می‌شود" if not miss else "برای شروع هر دو منبع لازم است — کم: " + "، ".join(miss))
        self.b_start.setEnabled(not active and not miss)''')
sub("autoreview/ui.py", '''        if not self.approved_crm and QMessageBox.question(self, "CRM بارگذاری نشده",
                "تکراری‌های CRM بررسی نمی‌شوند (فقط تکراری‌های NBO). ادامه بدهم؟") != QMessageBox.Yes:
            return
''', '''        miss = self.sources_missing()
        if miss:
            QMessageBox.warning(self, "منبع ناقص", "NBO و CRM درخواست‌های متفاوتی دارند و تکراری می‌تواند در هر کدام باشد؛ هر دو لازم است.\\nهنوز بارگذاری نشده: " + "، ".join(miss))
            return
''')
sub("autoreview/ui.py", '''        if not ok:
            QMessageBox.warning(self, "دریافت از CRM", text)
''', '''        self.sync_buttons()
        if not ok:
            QMessageBox.warning(self, "دریافت از CRM", text)
''')
sub("autoreview/ui.py", '''        self.crm_label.setText(f"CRM: {Path(f).name} ({len(ok)} تاییدشده)")
''', '''        self.crm_label.setText(f"CRM: {Path(f).name} ({len(ok)} تاییدشده)")
        self.sync_buttons()
''')
sub("autoreview/ui.py", '''        res = store.results_of(db, run) if run else []
        db.close()''', '''        res = store.results_of(db, run) if run else []
        sources = store.run_sources(db, run) if run else None
        db.close()''')
sub("autoreview/ui.py", "            export.write_xlsx(f, res)", "            export.write_xlsx(f, res, sources=sources)")

# ---------- tests ----------
sub("tests/test_pipeline.py", '''r.start(rows, approved_nbo=[{"id": "OLD", "site": "dup.ir"}], run_id="t1")''',
    '''r.start(rows, approved_nbo=[{"id": "OLD", "site": "dup.ir"}], approved_crm=[{"id": "MRG-9", "site": "crm-only.ir"}], run_id="t1")''')
sub("tests/test_pipeline.py", '''    r.start([row(str(i), "shop.ir") for i in range(50)], run_id="t2")''',
    '''    r.start([row(str(i), "shop.ir") for i in range(50)], approved_nbo=[{"id": "X", "site": "x.ir"}], approved_crm=[{"id": "Y", "site": "y.ir"}], run_id="t2")''')
t = (R / "tests" / "test_pipeline.py").read_text(encoding="utf-8")
t += '''

def test_a_run_needs_both_nbo_and_crm_and_a_crm_only_duplicate_is_caught(tmp_path):
    import pytest
    r = Runner(tmp_path / "t.db", client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError):
        r.start([row("A", "shop.ir")], approved_nbo=[{"id": "X", "site": "x.ir"}], approved_crm=[], run_id="t3")
    with pytest.raises(ValueError):
        r.start([row("A", "shop.ir")], approved_nbo=[], approved_crm=[{"id": "Y", "site": "y.ir"}], run_id="t3")
    r.start([row("A", "shop.ir")], approved_nbo=[{"id": "X", "site": "x.ir"}], approved_crm=[{"id": "MRG-5", "site": "https://www.shop.ir/"}], run_id="t4")
    r.join(60)
    db = store.connect(tmp_path / "t.db")
    got = store.results_of(db, "t4")[0]
    assert got["action"] == "CANCEL" and "CRM" in got["notes"][0] and "MRG-5" in got["notes"][0]
    assert store.run_sources(db, "t4") == {"run": "t4", "nbo_approved": 1, "crm_approved": 1}
'''
(R / "tests" / "test_pipeline.py").write_text(t, encoding="utf-8")
print("patched")
