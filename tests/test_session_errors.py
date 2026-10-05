from threading import Lock

from PySide6.QtCore import QObject

from autoreview.app import session as session_module
from autoreview.app.session import Session


def bare_session():
    session = Session.__new__(Session)
    QObject.__init__(session)
    session.profile = {}
    session._refresh_lock = Lock()
    session.workflow_sync_status = "آخرین اتصال موفق: 10:00"
    session.workflow_refresh_error = ""
    return session


def test_workflow_refresh_failure_is_not_reported_as_success(monkeypatch):
    session = bare_session()
    callbacks = []
    success, failures, changed = [], [], []
    monkeypatch.setattr(session_module.sheets, "load", lambda: {})
    monkeypatch.setattr(session_module, "run_bg", lambda work, done, failed: callbacks.append((done, failed)))
    session.data_changed.connect(lambda: changed.append(True))

    session.refresh_workflow(
        then=lambda: success.append(True),
        on_fail=lambda error: failures.append(str(error)),
    )
    callbacks[0][1](RuntimeError("local rebuild failed"))

    assert not success
    assert failures == ["local rebuild failed"]
    assert session.workflow_refresh_error == "local rebuild failed"
    assert changed


def test_successful_workflow_refresh_clears_previous_stale_report(monkeypatch):
    session = bare_session()
    session.workflow_refresh_error = "previous failure"
    callbacks, success = [], []
    monkeypatch.setattr(session_module.sheets, "load", lambda: {})
    monkeypatch.setattr(session_module, "run_bg", lambda work, done, failed: callbacks.append(done))

    session.refresh_workflow(then=lambda: success.append(True))
    callbacks[0]()

    assert session.workflow_refresh_error == ""
    assert success == [True]
