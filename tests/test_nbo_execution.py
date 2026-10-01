import pytest

from autoreview import execution, nbo_execution, store, workflow


@pytest.fixture
def setup_case():
    db = store.connect()
    workflow.ensure(db)
    execution.ensure(db)
    workflow.refresh(db, [dict(smr='SMR-12345', site='example.test', status='PENDING', has_instore='false')], {'SMR-12345'})
    case = workflow.get(db, 'SMR-12345')
    workflow.decide(db, case['smr'], 'online', 'APPROVE', 'tester', 'verified', case['revision'])
    workflow.acknowledge(db, workflow.pending(db))
    yield db, workflow.get(db, case['smr'])
    db.close()


class Backend:
    def __init__(self):
        self.detail = dict(id=123, version=7, requestNumber='SMR-12345', status='PENDING', merchantTypes=['ONLINE'])
        self.writes = []
        self.match = True
        self.lose_response = False
        self.apply_change = True

    def read(self, smr):
        return dict(self.detail)

    def matches_source(self, case, detail):
        return self.match

    def transitions(self):
        return {'COMMERCIAL_IN_PROGRESS': ['COMMERCIAL_APPROVED']}

    def assign(self, body):
        assert body == {'id': 123, 'version': 7}
        self.writes.append(('assign', body))
        self.detail.update(status='COMMERCIAL_IN_PROGRESS', version=8)

    def change(self, body):
        assert body == {'id': 123, 'version': 8, 'status': 'COMMERCIAL_APPROVED'}
        self.writes.append(('approve', body))
        if self.apply_change:
            self.detail.update(status='COMMERCIAL_APPROVED', version=9)
        if self.lose_response:
            raise TimeoutError('response lost')


def test_versions_refreshed_and_result_read_back(setup_case):
    db, case = setup_case
    backend = Backend()
    nbo_execution.approve(db, case, backend, lambda: True)
    assert len(backend.writes) == 2
    assert execution.records(db)[0]['state'] == 'VERIFIED'


def test_dry_run_sends_nothing(setup_case):
    db, case = setup_case
    backend = Backend()
    with pytest.raises(ValueError):
        nbo_execution.approve(db, case, backend, lambda: False)
    assert backend.writes == []


@pytest.mark.parametrize('change', [dict(requestNumber='SMR-99999'), dict(merchantTypes=['ONLINE', 'IN_STORE']), dict(version=None)])
def test_wrong_identity_channel_or_version_refuses(setup_case, change):
    db, case = setup_case
    backend = Backend()
    backend.detail.update(change)
    with pytest.raises(ValueError):
        nbo_execution.approve(db, case, backend, lambda: True)
    assert not backend.writes


def test_changed_source_refuses_before_assignment(setup_case):
    db, case = setup_case
    backend = Backend()
    backend.match = False
    with pytest.raises(ValueError):
        nbo_execution.approve(db, case, backend, lambda: True)
    assert not backend.writes


@pytest.mark.parametrize('lost,apply', [(True, True), (False, False)])
def test_uncertain_response_never_claims_success(setup_case, lost, apply):
    db, case = setup_case
    backend = Backend()
    backend.lose_response, backend.apply_change = lost, apply
    with pytest.raises((TimeoutError, ValueError)):
        nbo_execution.approve(db, case, backend, lambda: True)
    assert execution.records(db)[0]['state'] == 'UNCERTAIN'


def test_kill_switch_after_assignment_prevents_approval(setup_case):
    db, case = setup_case
    backend = Backend()
    with pytest.raises(ValueError):
        nbo_execution.approve(db, case, backend, lambda: len(backend.writes) == 0)
    assert [w[0] for w in backend.writes] == ['assign']
    assert execution.records(db)[0]['state'] == 'UNCERTAIN'
