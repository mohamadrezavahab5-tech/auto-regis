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


def test_mode_page_starts_dry_and_displays_readiness(monkeypatch):
    QApplication.instance() or QApplication([])
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'information', lambda *a, **k: None)
    session = Session()
    control = ExecutionControl(session)
    page = ExecutionPage(session, type('Shell', (), {'execution': control})())
    page.on_show()
    assert not page.switch.isChecked() and page.switch.isEnabled()
    assert "Change Status" in page.readiness.text()
    page.switch.setChecked(True)                     # the owner may switch live on (it never survives a restart)
    assert control.mode.live and page.switch.isChecked()
    assert not ExecutionControl(session).mode.live
    control.stop()
    page.close()


def test_colleague_cannot_change_execution_mode():
    QApplication.instance() or QApplication([])
    session = Session(False)
    control = ExecutionControl(session)
    page = ExecutionPage(session, type('Shell', (), {'execution': control})())
    page.on_show()
    assert not page.switch.isEnabled()
    control.stop()
    page.close()


def test_the_wheel_does_not_change_a_number_box_nobody_clicked():
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QScrollArea, QSpinBox, QVBoxLayout, QWidget
    from autoreview.app.widgets import WheelGuard
    app = QApplication.instance() or QApplication([])
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    try:
        from PySide6.QtWidgets import QLineEdit
        area = QScrollArea(); inner = QWidget(); lay = QVBoxLayout(inner); box = QSpinBox(); box.setValue(10)
        other = QLineEdit(); lay.addWidget(other); lay.addWidget(box); area.setWidget(inner); area.show()
        other.setFocus(); app.processEvents()
        assert not box.hasFocus()                                 # like a settings page: the person is elsewhere
        ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(box, ev)
        assert box.value() == 10                                  # not focused: the page scrolls, the value stays
        assert box.focusPolicy() == Qt.FocusPolicy.StrongFocus
    finally:
        app.removeEventFilter(guard)
