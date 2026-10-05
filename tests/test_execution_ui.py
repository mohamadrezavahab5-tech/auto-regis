import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from autoreview import store, workflow
from autoreview.paths import user_dir
from autoreview.app.execution_control import ExecutionControl
from autoreview.app.pages.execution_page import ExecutionPage


@pytest.fixture(autouse=True)
def authenticated_crm(monkeypatch):
    from autoreview import crm_sync
    monkeypatch.setattr(crm_sync, '_authenticated_identity', {'username': 'SNAPP\\mohammadreza.vahab', 'user_id': 'test'})


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
    assert "خاموش" in page.mode_text.text() and page.limit.value() == 10
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.No)
    page.switch.setChecked(True)                     # automatic sending asks first; "no" leaves it off
    assert not control.mode.live and not page.switch.isChecked()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.StandardButton.Yes)
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


def test_rehearse_all_goes_through_every_ready_request_and_sums_up(monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    QApplication.instance() or QApplication([])
    session = Session()
    control = ExecutionControl(session)
    cases = [dict(smr=f'SMR-{i}', revision=1) for i in range(3)]
    answers = iter([dict(ok=True, error=''), dict(ok=True, error='needs_assign'), dict(ok=False, error='not_found', message='نبود')])
    seen = []
    monkeypatch.setattr(control, 'ready_cases', lambda allowed=None, **_k: list(cases))
    monkeypatch.setattr(control, 'apply', lambda case, done, rehearsal=False, parent=None: (seen.append((case['smr'], rehearsal)),
                                                                                             done(next(answers))))
    assert control.rehearse_all() == 3
    assert control.rehearse_all() == 'busy'                           # one at a time
    loop = QEventLoop()
    control.notice.connect(lambda _t: loop.quit())
    QTimer.singleShot(20_000, loop.quit)
    loop.exec()
    assert seen == [('SMR-0', True), ('SMR-1', True), ('SMR-2', True)] and control.batch is None
    assert 'تا آخر درست: 1' in control.last_batch and 'تا Assign درست: 1' in control.last_batch and 'خطا: 1' in control.last_batch
    control.stop()


def test_a_real_batch_continues_after_failure_and_is_the_owners(monkeypatch):
    """Owner 2026-10-03: "10 real ones" - a counted batch, not all 800 and not one at a time."""
    from PySide6.QtCore import QEventLoop, QTimer
    QApplication.instance() or QApplication([])
    control = ExecutionControl(Session())
    cases = [dict(smr=f'SMR-{i}', revision=1) for i in range(4)]
    answers = iter([dict(ok=True, error='', sent=True), dict(ok=False, error='not_found', message='نبود'), dict(ok=True, error='', sent=True), dict(ok=True, error='', sent=True)])
    seen = []
    monkeypatch.setattr(control, 'apply', lambda case, done, rehearsal=False, parent=None: (seen.append((case['smr'], rehearsal)),
                                                                                             done(next(answers))))
    monkeypatch.setattr(control, 'may_apply', lambda: False)      # no Online role
    try:
        control.start_batch(cases, rehearsal=False)
        raise AssertionError('a colleague started a real batch')
    except PermissionError:
        pass
    monkeypatch.setattr(control, 'may_apply', lambda: True)       # the owner or an Online colleague
    assert control.start_batch([], rehearsal=False) == 0
    assert control.start_batch(cases, rehearsal=False) == 4
    loop = QEventLoop()
    control.notice.connect(lambda _t: loop.quit())
    QTimer.singleShot(20_000, loop.quit)
    loop.exec()
    assert seen == [('SMR-0', False), ('SMR-1', False), ('SMR-2', False), ('SMR-3', False)] and control.batch is None
    assert '\u062a\u0645\u0627\u0645 \u0634\u062f' in control.last_batch and 'SMR-1' in control.last_batch and ('3 \u0627\u0632 4' in control.last_batch.replace('\u06f3','3').replace('\u06f4','4'))
    control.stop()


def test_ready_cases_by_verdict_day_picked_ids_and_limit(monkeypatch):
    from datetime import date
    from autoreview import execution, workflow
    QApplication.instance() or QApplication([])
    control = ExecutionControl(Session())
    days = {'SMR-1': '2026-10-01T09:00:00+00:00', 'SMR-2': '2026-10-02T09:00:00+00:00', 'SMR-3': '2026-10-03T09:00:00+00:00'}
    cases = [dict(smr=k, revision=1, updated_at=v, online=dict(action='APPROVE', at=v)) for k, v in days.items()]
    monkeypatch.setattr(workflow, 'cases', lambda db: list(cases))
    monkeypatch.setattr(execution, 'target', lambda case: ('APPROVE', ''))
    monkeypatch.setattr(execution, 'eligibility', lambda case, now=None: '')
    monkeypatch.setattr(execution, 'records', lambda db, limit=None: [])
    ids = lambda rows: [c['smr'] for c in rows]
    assert ids(control.ready_cases({'APPROVE'})) == ['SMR-1', 'SMR-2', 'SMR-3']
    assert ids(control.ready_cases({'APPROVE'}, since=date(2026, 10, 2))) == ['SMR-2', 'SMR-3']
    assert ids(control.ready_cases({'APPROVE'}, since=date(2026, 10, 2), until=date(2026, 10, 2))) == ['SMR-2']
    assert ids(control.ready_cases({'APPROVE'}, smrs=['SMR-3', 'SMR-1'])) == ['SMR-1', 'SMR-3']
    assert ids(control.ready_cases({'APPROVE'}, limit=2)) == ['SMR-1', 'SMR-2']
    assert control.ready_cases({'EDIT'}) == []
    control.stop()


def test_filter_selection_never_silently_broadens(monkeypatch):
    from PySide6.QtCore import Qt, QDate
    from autoreview import execution, settings
    QApplication.instance() or QApplication([])
    control = ExecutionControl(Session())
    cases = [dict(smr='SMR-1', revision=1, category='A', channel='online'),
             dict(smr='SMR-2', revision=1, category='B', channel='online')]
    monkeypatch.setattr(control, 'ready_cases', lambda *args, **kwargs: list(cases))
    monkeypatch.setattr(execution, 'target', lambda case: ('APPROVE', ''))
    monkeypatch.setattr(ExecutionPage, '_show_file', lambda self, *args: None)
    writes = []
    monkeypatch.setattr(settings, 'save_user', lambda value: writes.append(value))
    page = ExecutionPage(control.session, type('Shell', (), {'execution': control})())
    page.render()
    assert page._days() == (None, None)
    page.ready_list.item(0).setCheckState(Qt.CheckState.Checked)
    assert [c['smr'] for c in page._chosen()] == ['SMR-1']
    page.category.setCurrentIndex(page.category.findData('B'))
    assert page._chosen() == [] and page._checked == {'SMR-1'}
    page.category.setCurrentIndex(0)
    assert [c['smr'] for c in page._chosen()] == ['SMR-1']
    page._tick_all(False)
    assert page._chosen() == [] and not page.b_apply.isEnabled()
    page.category.setCurrentIndex(page.category.findData('A'))
    cases[:] = [cases[1]]
    page.render()
    assert page.category.currentData() == 'A' and page._shown == []
    page.kind.setCurrentIndex(page.kind.findData('EDIT'))
    assert writes == []
    page.period.setCurrentIndex(page.period.findData('custom'))
    page.date_range.start.setDate(QDate(2026, 10, 3))
    page.date_range.end.setDate(QDate(2026, 10, 1))
    assert page._days() is None and not page.b_apply.isEnabled()
    control.stop()
    page.close()


def test_batch_continues_after_individual_request_failure(monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    QApplication.instance() or QApplication([])
    control = ExecutionControl(Session())
    cases = [dict(smr=f'SMR-{i}', revision=1) for i in range(3)]
    seen = []
    answers = iter([dict(ok=False, error='not_found', message='درخواست پیدا نشد'),
                    dict(ok=False, error='sent_unconfirmed', message='check NBO'),
                    dict(ok=True, sent=True)])
    monkeypatch.setattr(control, 'apply', lambda case, done, rehearsal=False: (seen.append(case['smr']), done(next(answers))))
    assert control.start_batch(cases, rehearsal=False) == 3
    loop = QEventLoop()
    control.notice.connect(lambda _t: loop.quit())
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert seen == ['SMR-0', 'SMR-1', 'SMR-2'] and control.batch is None
    assert 'تمام شد' in control.last_batch
    control.stop()


@pytest.mark.parametrize('error', ['load_failed', 'script', 'login', 'timeout'])
def test_real_batch_stops_on_systemic_nbo_errors(monkeypatch, error):
    from PySide6.QtCore import QEventLoop, QTimer
    QApplication.instance() or QApplication([])
    control = ExecutionControl(Session())
    cases = [dict(smr=f'SMR-{i}', revision=1) for i in range(2)]
    seen = []

    def apply(case, done, rehearsal=False, parent=None):
        seen.append(case['smr'])
        done(dict(ok=False, error=error, message='خرابی سیستمی NBO'))

    monkeypatch.setattr(control, 'apply', apply)
    assert control.start_batch(cases, rehearsal=False) == 2
    loop = QEventLoop()
    control.notice.connect(lambda _t: loop.quit())
    QTimer.singleShot(10_000, loop.quit)
    loop.exec()
    assert seen == ['SMR-0'] and control.batch is None
    assert 'متوقف شد' in control.last_batch
    control.stop()


def test_autopilot_only_takes_online_cases(monkeypatch):
    QApplication.instance() or QApplication([])
    control = ExecutionControl(Session())
    control.mode.live = True
    seen = []
    monkeypatch.setattr(control, 'ready_cases', lambda: [
        dict(smr='SMR-BOTH', channel='both'), dict(smr='SMR-UNKNOWN'), dict(smr='SMR-ONLINE', channel='online')])
    monkeypatch.setattr(control, 'apply', lambda case, done: seen.append(case['smr']))
    control._auto_tick()
    assert seen == ['SMR-ONLINE']
    monkeypatch.setattr(control, 'ready_cases', lambda: [dict(smr='SMR-BOTH', channel='both')])
    control._auto_tick()
    assert seen == ['SMR-ONLINE']
    control.stop()


@pytest.mark.parametrize('error', ['load_failed', 'script', 'login', 'timeout'])
def test_autopilot_stops_after_systemic_nbo_error(monkeypatch, error):
    QApplication.instance() or QApplication([])
    control = ExecutionControl(Session())
    control.mode.live = True
    monkeypatch.setattr(control, 'ready_cases', lambda: [dict(smr='SMR-ONLINE', channel='online')])
    monkeypatch.setattr(control, 'apply', lambda case, done: done(
        dict(ok=False, error=error, message='خرابی سیستمی NBO')))
    control._auto_tick()
    assert not control.mode.live
    assert 'خرابی سیستمی NBO' in control.last_stop
    control.stop()


def test_autopilot_keeps_running_after_request_level_error(monkeypatch):
    QApplication.instance() or QApplication([])
    control = ExecutionControl(Session())
    control.mode.live = True
    monkeypatch.setattr(control, 'ready_cases', lambda: [dict(smr='SMR-ONLINE', channel='online')])
    monkeypatch.setattr(control, 'apply', lambda case, done: done(
        dict(ok=False, error='not_found', message='درخواست پیدا نشد')))
    control._auto_tick()
    assert control.mode.live
    control.stop()
