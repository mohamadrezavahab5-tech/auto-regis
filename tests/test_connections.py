from types import SimpleNamespace
import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QLabel
from autoreview.app.pages.connections import ConnectionsPage

class Session(QObject):
    workflow_sync_changed = Signal()
    workflow_sync_status = 'connected'
    def sync_workflow(self, **kwargs): self.called = kwargs

def test_fresh_coworker_has_service_account_import_and_no_url(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance() or QApplication([])
    from autoreview.app.pages import connections
    monkeypatch.setattr(connections.workspace, 'call', lambda *a, **k: {'ok':True})
    monkeypatch.setattr(connections, 'run_bg', lambda work, done, error: done(work(None)))
    session = Session()
    page = ConnectionsPage(session, SimpleNamespace(relogin=lambda: None))
    page.on_show()
    assert not page.findChildren(QLineEdit)
    reconnect = next(b for b in page.findChildren(QPushButton) if b.text() == 'بررسی اتصال مشترک')
    assert any('Service Account JSON' in b.text() for b in page.findChildren(QPushButton))
    reconnect.click()
    assert session.called == {'force': True}
    page.close()


@pytest.mark.parametrize('code', ['GOOGLE_CREDENTIALS_MISSING', 'SHEET_ACCESS_DENIED', 'GOOGLE_API_ERROR'])
def test_connection_errors_are_clear_and_never_trigger_sync(monkeypatch, code):
    from autoreview.app.pages import connections
    from autoreview.workspace_google import WorkspaceError, MESSAGES
    app = QApplication.instance() or QApplication([])
    session = Session()
    monkeypatch.setattr(connections, 'run_bg', lambda work, done, error: error(WorkspaceError(code)))
    page = ConnectionsPage(session, SimpleNamespace(relogin=lambda: None))
    page.test_workspace()
    assert any(MESSAGES[code] in label.text() for label in page.sheet_steps.findChildren(QLabel))
    assert not hasattr(session, 'called')
    page.close()
