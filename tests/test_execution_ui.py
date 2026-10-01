import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from autoreview import store, workflow
from autoreview.paths import user_dir
from autoreview.app.execution_control import ExecutionControl
from autoreview.app.pages.execution_page import ExecutionPage


class Session(QObject):
    data_changed = Signal()

    def __init__(self, owner=True):
        super().__init__()
        self.profile = {'username': 'mohammadreza.vahab' if owner else 'colleague'}
        db = self.db()
        workflow.ensure(db)
        db.close()

    def db(self):
        return store.connect(user_dir() / 'ui-test.db')


def test_mode_page_starts_dry_and_displays_readiness():
    app = QApplication.instance() or QApplication([])
    session = Session()
    control = ExecutionControl(session)
    shell = type('Shell', (), {'execution': control})()
    page = ExecutionPage(session, shell)
    page.on_show()
    assert page.mode.currentData() is False
    assert page.mode.isEnabled()
    assert control.readiness in page.readiness.text()
    control.stop()
    page.close()


def test_colleague_cannot_change_execution_mode():
    app = QApplication.instance() or QApplication([])
    session = Session(False)
    control = ExecutionControl(session)
    page = ExecutionPage(session, type('Shell', (), {'execution': control})())
    assert not page.mode.isEnabled()
    control.stop()
    page.close()
