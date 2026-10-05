import json
import pytest
from autoreview import execution, sheets, store, workflow, workflow_sync, workspace


def database():
    db = store.connect()
    workflow.ensure(db)
    execution.ensure(db)
    return db


def ready(db):
    workflow.refresh(db, [dict(smr='SMR-123', site='example.test', status='PENDING',
                              has_online='true', has_instore='false')], {'SMR-123'})
    c = workflow.get(db, 'SMR-123')
    workflow.decide(db, c['smr'], 'online', 'APPROVE', 'owner', 'checked', c['revision'])
    workflow.acknowledge(db, workflow.pending(db))
    return workflow.get(db, c['smr'])


def test_atomic_cache_and_pending_changes_survive():
    db = database(); c = ready(db)
    changed = dict(c, site='new.test', revision=c['revision']+1)
    with pytest.raises(ValueError): workspace.cache_snapshot(db, [changed], [['bad']], 1)
    assert workflow.get(db, c['smr']) == c
    workflow.decide(db, c['smr'], 'online', 'REOPEN', 'owner', 'recheck', c['revision'])
    pending = workflow.get(db, c['smr'])
    workspace.cache_snapshot(db, [changed], [], 2)
    assert workflow.get(db, c['smr']) == pending


def test_two_independent_databases_share_backlog():
    a, b = database(), database(); c = ready(a)
    c['suggestion'] = {'action':'APPROVE', 'reason_codes':[], 'decided_at':store.now()}
    workspace.cache_snapshot(a, [c], [], 1); workspace.cache_snapshot(b, [c], [], 1)
    assert workspace.shared_queues(a) == workspace.shared_queues(b)
    assert workspace.shared_reviews(a) == workspace.shared_reviews(b)
    assert len(workspace.shared_queues(b)[0]) == 1


def test_validation_precedes_remote_claim(monkeypatch):
    db = database(); c = ready(db); c['source_loaded_at'] = '2000-01-01T00:00:00+00:00'
    monkeypatch.setattr(workspace, 'call', lambda *a, **k: pytest.fail('remote claim acquired'))
    with pytest.raises(ValueError): workspace.prepare_execution(db, c, {})
    assert not execution.records(db)


def test_lost_claim_response_is_never_executed(monkeypatch):
    db = database(); c = ready(db)
    def lost(*a, **k): raise sheets.SheetError('response lost')
    monkeypatch.setattr(workspace, 'call', lost)
    with pytest.raises(sheets.SheetError): workspace.prepare_execution(db, c, {})
    body, pending = db.execute('SELECT body,pending FROM workspace_execution_outbox').fetchone()
    assert pending == 1 and json.loads(body)['state'] == 'BLOCKED'
    assert execution.records(db)[0]['state'] == 'BLOCKED'


def test_active_claim_skipped_but_recovered_claim_is_uncertain(monkeypatch):
    db = database(); c = ready(db); calls = []
    def remote(action, cfg=None, **body):
        calls.append((action, body)); return {'claim':body.get('claim'), 'ok':True}
    monkeypatch.setattr(workspace, 'call', remote)
    claim = workspace.prepare_execution(db, c, {})['claim']
    workspace.flush_execution(db, {})
    assert len(calls) == 1
    workspace.cache_snapshot(db, [c], [], 1)
    assert execution.records(db)[0]['state'] == 'SENDING'
    workspace.recover_execution(db); workspace.flush_execution(db, {})
    assert calls[-1][1]['claim'] == claim and calls[-1][1]['state'] == 'UNCERTAIN'


def test_terminal_receipt_survives_network_failure(monkeypatch):
    db = database(); c = ready(db)
    workspace.finish_execution(db, c, 'a'*32, 'SENT', 'done')
    def lost(*a, **k): raise sheets.SheetError('offline')
    monkeypatch.setattr(workspace, 'call', lost)
    with pytest.raises(sheets.SheetError): workspace.flush_execution(db, {})
    assert db.execute('SELECT count(*) FROM workspace_execution_outbox').fetchone()[0] == 1
    monkeypatch.setattr(workspace, 'call', lambda *a, **k: {'ok':True})
    workspace.flush_execution(db, {})
    assert db.execute('SELECT count(*) FROM workspace_execution_outbox').fetchone()[0] == 0


def test_changed_generation_preserves_previous_snapshot(monkeypatch):
    db = database(); c = ready(db)
    db.execute("INSERT INTO workflow_meta VALUES('workspace_v6_initialized','1')")
    db.commit()
    def remote(action, cfg=None, **body):
        if action == 'health': return {'server_version':6,'schema_version':6,'sheet_ok':True}
        if action == 'read':
            return {'restart':True} if body['offset'] else {
                'generation':1, 'cases':[dict(c, site='new.test')], 'executions':[], 'next':200}
        raise AssertionError(action)
    monkeypatch.setattr(workspace, 'call', remote)
    with pytest.raises(sheets.SheetError): workflow_sync.sync_workspace(db, {})
    assert workflow.get(db, c['smr']) == c


def test_confirmed_source_conflict_gets_new_id_but_timeout_reuses_id(monkeypatch):
    db = database(); c = ready(db)
    operations = []
    result = ['conflict']
    def remote(action, cfg=None, **body):
        if action == 'health': return dict(server_version=6, schema_version=6, sheet_ok=True)
        if action == 'source_batch':
            operations.append(body['operation_id'])
            if result[0] == 'offline': raise sheets.SheetError('offline')
            return dict(cases=[], rejected=[{'smr': c['smr'], 'status':'conflict'}])
        if action == 'read': return dict(cases=[], executions=[], generation=0, next=None)
        raise AssertionError(action)
    monkeypatch.setattr(workspace, 'call', remote)
    assert workflow_sync.sync_workspace(db, {})['rejected'] == 1
    result[0] = 'offline'
    for _ in range(2):
        with pytest.raises(sheets.SheetError): workflow_sync.sync_workspace(db, {})
    assert operations[0] != operations[1] == operations[2]
    assert workflow.get(db, c['smr']) == c


def test_another_crm_user_cannot_flush_previous_users_execution(monkeypatch):
    from autoreview import crm_sync
    db = database(); c = ready(db)
    monkeypatch.setattr(crm_sync, '_authenticated_identity', {'username':'alice'})
    workspace.finish_execution(db, c, 'a'*32, 'SENT', 'done')
    monkeypatch.setattr(crm_sync, '_authenticated_identity', {'username':'bob'})
    monkeypatch.setattr(workspace, 'call', lambda *a, **k: pytest.fail('wrong actor'))
    workspace.flush_execution(db)
    assert db.execute('SELECT count(*) FROM workspace_execution_outbox').fetchone()[0] == 1
