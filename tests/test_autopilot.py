import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoreview import reference, settings  # noqa: E402
from autoreview.app.session import Session  # noqa: E402


def make_session():
    QApplication.instance() or QApplication([])
    return Session({"username": "SNAPP\tester", "display_name": "tester"})


def nbo_row(smr, instore="false"):
    return dict(smr=smr, status="PENDING", site=f"{smr.lower()}.ir", ownership="INDIVIDUAL", has_online="true",
                has_instore=instore, created_at="1405/07/09")


def test_autopilot_waits_for_crm_then_reviews_only_new_requests(monkeypatch):
    s = make_session()
    started = []
    monkeypatch.setattr(s, "start_run", lambda rows, kind, label=None: started.append((kind, [r["smr"] for r in rows])))
    db = s.db()
    try:
        reference.import_nbo(db, [nbo_row("SMR-1"), nbo_row("SMR-2", instore="true")], "test")
    finally:
        db.close()
    assert s.autopilot_run() == "no_crm" and not started                 # duplicates need both approved sets
    db = s.db()
    try:
        reference.upsert_crm(db, [], "test", full=True)
    finally:
        db.close()
    assert s.autopilot_run() == 2
    assert started == [("auto", ["SMR-1", "SMR-2"])]


def test_autopilot_switch_off(monkeypatch):
    s = make_session()
    settings.save_user({"rules": {"automation.autopilot": False}})
    monkeypatch.setattr(s, "start_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start")))
    assert s.autopilot_run() == "off"
