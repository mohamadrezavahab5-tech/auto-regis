import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from autoreview import reference, settings  # noqa: E402
from autoreview.app.session import Session  # noqa: E402


@pytest.fixture(autouse=True)
def authenticated_crm(monkeypatch):
    from autoreview import crm_sync
    monkeypatch.setattr(crm_sync, '_authenticated_identity', {'username': 'tester', 'user_id': 'test'})


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
    s._refresh_workflow_now()
    db = s.db()
    try:
        reference.upsert_crm(db, [], "test", full=True)
    finally:
        db.close()
    assert s.autopilot_run() == 2
    assert started == [("auto", ["SMR-2", "SMR-1"])]           # Online + Instore first: the Instore team waits for it


def test_autopilot_switch_off(monkeypatch):
    s = make_session()
    settings.save_user({"rules": {"automation.autopilot": False}})
    monkeypatch.setattr(s, "start_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not start")))
    assert s.autopilot_run() == "off"


def test_manual_results_can_be_reviewed_again_unless_a_person_decided():
    from autoreview import rules as R, store, workflow
    s = make_session()
    db = s.db()
    try:
        reference.import_nbo(db, [nbo_row("SMR-7"), nbo_row("SMR-8"), nbo_row("SMR-9")], "test")
        store.start_run(db, "r1", 3)
        for smr, action in (("SMR-7", "MANUAL"), ("SMR-8", "MANUAL"), ("SMR-9", "APPROVE")):
            store.save_result(db, "r1", dict(smr=smr, site=f"{smr.lower()}.ir"), R.Decision(action), {})
    finally:
        db.close()
    s._refresh_workflow_now()
    db = s.db()
    try:
        case = workflow.get(db, "SMR-8")
        workflow.decide(db, "SMR-8", "online", "APPROVE", "tester", "checked by hand", case["revision"],
                        labels={"edit": {}, "cancel": {}})
    finally:
        db.close()
    assert [r["smr"] for r in s.manual_again_rows()] == ["SMR-7"]


def test_a_refresh_round_without_answer_is_given_up_after_a_while(monkeypatch):
    from autoreview.app.automatic import AutomaticSources
    s = make_session()
    auto = AutomaticSources(s, nbo_client=None)
    auto.timer.stop()
    started = []
    monkeypatch.setattr(auto, "_nbo_round", lambda sess, wait: started.append(wait))
    monkeypatch.setattr(s, "refresh_crm", lambda **kw: None)
    auto.nbo_active, auto.nbo_started = True, __import__("time").monotonic() - 3600   # no answer for an hour
    auto.next_nbo = 0.0
    auto.tick()
    assert started and auto.nbo_active is False
