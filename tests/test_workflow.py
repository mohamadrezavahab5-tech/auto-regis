import json

import httpx
import pytest

from autoreview import store, workflow, workflow_sync, sheets

LABELS = {'edit': {'FIX': 'اصلاح'}, 'cancel': {'DUP': 'تکراری'}}


@pytest.fixture
def db():
    conn = store.connect()
    workflow.ensure(conn)
    yield conn
    conn.close()


def imported(db, both=False):
    row = dict(smr='SMR-12345', site='example.test', category='test', status='PENDING',
               ownership='INDIVIDUAL', has_online='true', has_instore='true' if both else 'false')
    workflow.refresh(db, [row], {row['smr']})
    return row


def decide(db, team='online', action='APPROVE', **overrides):
    args = dict(smr='SMR-12345', team=team, action=action, actor='tester', note='verified fixture',
                expected_revision=workflow.get(db,'SMR-12345')['revision'], labels=LABELS)
    args.update(overrides)
    return workflow.decide(db, **args)


def test_online_only_human_approval_becomes_ready_not_applied(db):
    imported(db)
    assert workflow.get(db,'SMR-12345')['state']=='WAIT_ONLINE'
    assert decide(db)['state']=='READY'
    assert 'اعمال نشده' in workflow.get(db,'SMR-12345')['state_fa']


@pytest.mark.parametrize('first,second',[('online','instore'),('instore','online')])
def test_both_channels_need_both_approvals_in_either_order(db, first, second):
    imported(db,True)
    assert decide(db,first)['state'] != 'READY'
    assert decide(db,second)['state']=='READY'
    assert decide(db,first,'REOPEN')['state'] != 'READY'


def test_machine_approval_is_never_human_approval(db):
    imported(db)
    workflow.suggest(db,'SMR-12345',dict(action='APPROVE',reason_codes=[],notes=[]))
    assert workflow.get(db,'SMR-12345')['state']=='WAIT_ONLINE'


def test_disagreement_requires_human_resolution(db):
    imported(db,True)
    decide(db)
    assert decide(db,'instore','CANCEL',reason='DUP')['state']=='CONFLICT'


@pytest.mark.parametrize('bad', [dict(actor=''),dict(note=''),dict(expected_revision=0),
    dict(team='instore'),dict(action='CANCEL'),dict(action='EDIT',reason='unknown')])
def test_bad_or_stale_decisions_do_not_write(db,bad):
    imported(db)
    before=workflow.get(db,'SMR-12345')
    with pytest.raises(ValueError): decide(db,**bad)
    assert workflow.get(db,'SMR-12345')==before


def test_changed_source_resets_approvals_and_disappeared_case_is_inactive(db):
    row=imported(db,True)
    decide(db); decide(db,'instore')
    row['site']='changed.test'
    workflow.refresh(db,[row],{row['smr']})
    case=workflow.get(db,row['smr'])
    assert case['online'] is None and case['instore'] is None
    workflow.refresh(db,[],set())
    assert workflow.get(db,row['smr'])['state']=='OUT_OF_SCOPE'
    with pytest.raises(ValueError): decide(db)


def test_a_request_taken_in_nbo_is_not_closed(db):
    row = imported(db)
    row['status'] = 'COMMERCIAL_IN_PROGRESS'                 # someone picked it up in NBO: out of the app's queue, not decided
    workflow.refresh(db, [row], set(), approved_statuses=('COMMERCIAL_APPROVED',))
    assert workflow.get(db, row['smr'])['state'] == 'OUT_OF_SCOPE'
    row['status'] = 'REQUIRED_EDITING'
    workflow.refresh(db, [row], set(), approved_statuses=('COMMERCIAL_APPROVED',))
    assert workflow.get(db, row['smr'])['state'] == 'DONE_CLOSED'

def test_identical_reference_does_not_generate_events(db):
    row=imported(db)
    before=workflow.pending(db)
    workflow.refresh(db,[row],{row['smr']})
    assert workflow.pending(db)==before


def test_changed_recommendation_reopens_online_decision(db):
    imported(db)
    workflow.suggest(db,'SMR-12345',dict(action='APPROVE',reason_codes=[],notes=[]))
    decide(db)
    workflow.suggest(db,'SMR-12345',dict(action='MANUAL',reason_codes=[],notes=['new evidence']))
    assert workflow.get(db,'SMR-12345')['online'] is None


def test_old_sync_ack_does_not_drop_new_decision(db):
    imported(db)
    sent=workflow.pending(db)
    decide(db)
    workflow.acknowledge(db,sent)
    assert workflow.pending_count(db)==1
    assert len(workflow.pending(db)['events'])==1


def command(db, **extra):
    return dict(command_id='test-command',smr='SMR-12345',team='online',action='APPROVE',
                actor='sheet reviewer',note='checked',revision=workflow.get(db,'SMR-12345')['revision'],**extra)


def test_command_replay_after_lost_receipt_is_idempotent(db):
    imported(db)
    cmd=command(db)
    first=workflow.apply_command(db,cmd,LABELS)
    revision=workflow.get(db,'SMR-12345')['revision']
    assert first['status']=='accepted'
    assert workflow.apply_command(db,cmd,LABELS)==first
    assert workflow.get(db,'SMR-12345')['revision']==revision


def test_rejected_command_is_saved_and_does_not_apply_later(db):
    imported(db)
    cmd=command(db); cmd['revision']=0
    assert workflow.apply_command(db,cmd,LABELS)['status']=='rejected'
    cmd['revision']=1
    assert workflow.apply_command(db,cmd,LABELS)['status']=='rejected'
    assert workflow.get(db,'SMR-12345')['online'] is None


def test_outbox_survives_database_restart(tmp_path):
    p=tmp_path/'db.sqlite'
    conn=store.connect(p); workflow.ensure(conn); imported(conn); sent=workflow.pending(conn); conn.close()
    conn=store.connect(p); workflow.ensure(conn)
    assert workflow.pending(conn)==sent
    conn.close()


def test_failed_network_does_not_acknowledge_outbox(db, monkeypatch):
    from autoreview import workspace
    imported(db)
    before = workflow.pending(db)
    def fail(*args, **kwargs): raise sheets.SheetError('offline', 'GOOGLE_API_ERROR')
    monkeypatch.setattr(workspace, 'health', fail)
    with pytest.raises(sheets.SheetError): workflow_sync.sync(db)
    assert workflow.pending(db) == before


def test_sync_rejects_wrong_sheet_without_uploading(db, monkeypatch):
    from autoreview import workspace_google, crm_sync
    imported(db)
    monkeypatch.setattr(crm_sync, '_authenticated_identity', {'username':'alice'})
    monkeypatch.setattr(workspace_google, 'get_backend', lambda cfg: workspace_google.Backend(cfg['own_sheet_id']))
    cfg = sheets.load(); cfg['own_sheet_id'] = 'wrong'
    with pytest.raises(sheets.SheetError) as exc: workflow_sync.sync(db, cfg)
    assert exc.value.code == 'SHEET_NOT_FOUND' and workflow.pending_count(db) == 1


def test_sync_acknowledges_only_verified_receipts(db, monkeypatch):
    from autoreview import workspace
    imported(db)
    def remote(action, cfg=None, **payload):
        if action == 'health': return dict(server_version=6,schema_version=6,sheet_ok=True)
        if action == 'source_batch': return dict(cases=[{'smr':c['smr']} for c in payload['cases']],rejected=[])
        if action == 'audit_batch': return dict(event_ids=[e['event_id'] for e in payload['events']])
        if action == 'read': return dict(generation=1,cases=[],executions=[],next=None)
        raise AssertionError(action)
    monkeypatch.setattr(workspace, 'call', remote)
    assert workflow_sync.sync(db)['remaining'] == 0


# ---- owner rules 2026-10-01 ------------------------------------------------------------------------------------------------
def test_engine_verdict_counts_as_online_when_switched_on(db):
    imported(db)
    workflow.suggest(db, 'SMR-12345', dict(action='APPROVE', reason_codes=[], notes=[]), engine_counts=True)
    case = workflow.get(db, 'SMR-12345')
    assert case['state'] == 'READY' and case['online']['source'] == 'engine'


def test_engine_approval_on_a_two_team_request_waits_for_instore(db):
    imported(db, both=True)
    workflow.suggest(db, 'SMR-12345', dict(action='APPROVE', reason_codes=[], notes=[]), engine_counts=True)
    assert workflow.get(db, 'SMR-12345')['state'] == 'WAIT_INSTORE'
    assert decide(db, 'instore')['state'] == 'READY'


def test_manual_engine_result_always_waits_for_a_person(db):
    imported(db)
    workflow.suggest(db, 'SMR-12345', dict(action='MANUAL', reason_codes=[], notes=[]), engine_counts=True)
    assert workflow.get(db, 'SMR-12345')['state'] == 'MANUAL' and workflow.get(db, 'SMR-12345')['online'] is None
    assert decide(db)['state'] == 'READY'                       # a person settles it


def test_a_person_overrides_the_engine_and_reopen_is_not_refilled(db):
    imported(db)
    workflow.suggest(db, 'SMR-12345', dict(action='APPROVE', reason_codes=[], notes=[]), engine_counts=True)
    assert decide(db, action='CANCEL', reason='DUP')['state'] == 'CANCEL'
    decide(db, action='REOPEN')
    workflow.adopt_engine_verdicts(db, True)
    workflow.suggest(db, 'SMR-12345', dict(action='APPROVE', reason_codes=[], notes=[]), engine_counts=True)
    assert workflow.get(db, 'SMR-12345')['state'] == 'WAIT_ONLINE'


def test_switching_the_engine_policy_fills_and_withdraws_only_engine_verdicts(db):
    imported(db)
    workflow.suggest(db, 'SMR-12345', dict(action='APPROVE', reason_codes=[], notes=[]))
    assert workflow.adopt_engine_verdicts(db, True) == 1 and workflow.get(db, 'SMR-12345')['state'] == 'READY'
    assert workflow.adopt_engine_verdicts(db, False) == 1 and workflow.get(db, 'SMR-12345')['state'] == 'WAIT_ONLINE'
    decide(db)
    assert workflow.adopt_engine_verdicts(db, False) == 0 and workflow.get(db, 'SMR-12345')['online']['source'] == 'human'


def test_approved_in_nbo_is_kept_as_done_with_its_verdicts(db):
    row = imported(db)
    decide(db)
    row['status'] = 'COMMERCIAL_APPROVED'
    workflow.refresh(db, [row], set(), approved_statuses=['COMMERCIAL_APPROVED'])
    case = workflow.get(db, 'SMR-12345')
    assert case['state'] == 'DONE_APPROVED' and case['online']['action'] == 'APPROVE'
    row['status'] = 'CANCELLED'
    workflow.refresh(db, [row], set(), approved_statuses=['COMMERCIAL_APPROVED'])
    assert workflow.get(db, 'SMR-12345')['state'] == 'DONE_CLOSED'


def test_coming_back_into_the_queue_starts_over(db):
    row = imported(db)
    decide(db)
    row['status'] = 'REQUIRED_EDITING'
    workflow.refresh(db, [row], set())
    row['status'] = 'PENDING'
    workflow.refresh(db, [row], {row['smr']})
    case = workflow.get(db, 'SMR-12345')
    assert case['state'] == 'WAIT_ONLINE' and case['online'] is None


def test_assignment_in_nbo_keeps_verdicts(db):
    row = imported(db)
    decide(db)
    row['status'] = 'COMMERCIAL_IN_PROGRESS'
    workflow.refresh(db, [row], {row['smr']})
    assert workflow.get(db, 'SMR-12345')['state'] == 'READY'


def test_sheet_commands_accept_persian_words_and_no_revision(db):
    imported(db, both=True)
    cmd = dict(command_id='c1', smr='SMR-12345', team='حضوری', action='لغو', reason='تکراری', note='x', actor='y', revision='')
    assert workflow.apply_command(db, cmd, LABELS)['status'] == 'accepted'
    case = workflow.get(db, 'SMR-12345')
    assert case['instore']['action'] == 'CANCEL' and case['instore']['reason'] == 'DUP'


def test_instore_cells_in_the_owner_sheet_apply_once_edit_and_withdraw(db):
    imported(db, both=True)
    decide(db)
    cells = dict(result='تایید قرارداد', reason='', note='', actor='سارا', date='1405/07/09')
    assert workflow.sheet_verdict(db, 'SMR-12345', 'instore', cells, LABELS) == 'ثبت شد'
    revision = workflow.get(db, 'SMR-12345')['revision']
    assert workflow.get(db, 'SMR-12345')['state'] == 'READY'
    assert workflow.sheet_verdict(db, 'SMR-12345', 'instore', cells, LABELS) == 'ثبت شد'
    assert workflow.get(db, 'SMR-12345')['revision'] == revision          # same cells: nothing applied again
    bad = dict(cells, result='نیاز به ادیت', reason='چیز ناشناخته')
    assert workflow.sheet_verdict(db, 'SMR-12345', 'instore', bad, LABELS).startswith('رد شد')
    assert workflow.get(db, 'SMR-12345')['state'] == 'READY'
    assert workflow.sheet_verdict(db, 'SMR-12345', 'instore', dict(cells, result=''), LABELS) == 'نظر برداشته شد'
    assert workflow.get(db, 'SMR-12345')['state'] == 'WAIT_INSTORE'


def test_old_instore_cells_are_not_reused_after_the_request_changed(db):
    row = imported(db, both=True)
    cells = dict(result='تایید قرارداد', reason='', note='', actor='', date='')
    workflow.sheet_verdict(db, 'SMR-12345', 'instore', cells, LABELS)
    row['site'] = 'changed.test'
    workflow.refresh(db, [row], {row['smr']})
    assert 'دوباره' in workflow.sheet_verdict(db, 'SMR-12345', 'instore', cells, LABELS)
    assert workflow.get(db, 'SMR-12345')['instore'] is None


def _result(db, run, action, trace, smr='SMR-12345'):
    from autoreview import rules as R
    store.save_result(db, run, dict(smr=smr, site='example.test'), R.Decision(action, trace=trace), {})


def test_a_half_finished_review_still_becomes_the_online_verdict_and_errors_are_taken_again(db):
    imported(db, both=True)
    store.start_run(db, 'r1', 1)
    _result(db, 'r1', 'APPROVE', ['PASS'])                  # the run was interrupted: nobody called suggest
    assert workflow.get(db, 'SMR-12345')['online'] is None
    assert workflow.reconcile_suggestions(db, store.latest_results(db), engine_counts=True) == 1
    assert workflow.state(workflow.get(db, 'SMR-12345')) == 'WAIT_INSTORE'
    assert workflow.reconcile_suggestions(db, store.latest_results(db), engine_counts=True) == 0
    _result(db, 'r2', 'MANUAL', ['ERROR'])                  # an internal error is not a review
    assert 'SMR-12345' in store.latest_states(db) and store.latest_states(db)['SMR-12345'][0] == 'APPROVE'
    assert store.latest_results(db)['SMR-12345']['action'] == 'APPROVE'


def test_a_review_from_before_a_reset_is_not_applied(db):
    imported(db)
    _result(db, 'r1', 'APPROVE', ['PASS'])
    changed = dict(smr='SMR-12345', site='other.test', category='test', status='PENDING', ownership='INDIVIDUAL',
                   has_online='true', has_instore='false')
    import time; time.sleep(1.1)
    workflow.refresh(db, [changed], {'SMR-12345'})          # the site changed: verdicts start over
    assert workflow.reconcile_suggestions(db, store.latest_results(db), engine_counts=True) == 0
    # ... and the queue takes it again instead of counting the old review (live 2026-10-03: stuck 'waiting for Online')
    assert 'SMR-12345' in store.latest_states(db)
    assert 'SMR-12345' not in workflow.current_reviews(db, store.latest_states(db))
    time.sleep(1.1)
    _result(db, 'r2', 'EDIT', ['FAIL'])                      # the fresh review counts again
    assert workflow.current_reviews(db, store.latest_states(db))['SMR-12345'][0] == 'EDIT'
    assert workflow.reconcile_suggestions(db, store.latest_results(db), engine_counts=True) == 1


def test_a_locked_database_is_waited_for(monkeypatch):
    import sqlite3
    calls = []
    def write():
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError('database is locked')
        return 'ok'
    monkeypatch.setattr(store.time, 'sleep', lambda s: None)
    assert store._when_unlocked(write) == 'ok' and len(calls) == 3


def test_always_manual_categories_take_back_the_engines_approval(db):
    """Owner 2026-10-03: gold, education, health / tourism / leisure services are always reviewed by a person."""
    from autoreview import settings
    rules = settings.load_rules()
    assert 'خدمات سلامت' in rules['category_groups']['special'] and 'special' in rules['manual_category_groups']
    rows = [dict(smr='SMR-1', site='a.ir', category='خدمات سلامت', status='PENDING', has_instore='false'),
            dict(smr='SMR-2', site='b.ir', category='مد و پوشاک', status='PENDING', has_instore='false'),
            dict(smr='SMR-3', site='c.ir', category='آموزشی', status='PENDING', has_instore='false')]
    workflow.refresh(db, rows, {'SMR-1', 'SMR-2', 'SMR-3'})
    for smr, action in (('SMR-1', 'APPROVE'), ('SMR-2', 'APPROVE'), ('SMR-3', 'EDIT')):
        workflow.suggest(db, smr, dict(action=action, reason_codes=['X'] if action == 'EDIT' else [], notes=[],
                                       decided_at='2026-10-03T08:00:00+00:00'), engine_counts=True)
    assert workflow.hold_manual_categories(db, rules) == 1
    assert workflow.state(workflow.get(db, 'SMR-1')) == 'MANUAL'           # the approval was the engine's: a person decides
    assert workflow.state(workflow.get(db, 'SMR-2')) == 'READY'            # an ordinary category is untouched
    assert workflow.state(workflow.get(db, 'SMR-3')) == 'EDIT'             # a clear edit stays the rules' decision
    assert workflow.hold_manual_categories(db, rules) == 0                 # once


def test_workflow_preserves_identity_and_ownership_fields_for_shared_review(db):
    row = dict(smr='SMR-777', site='shop.test', category='مد و پوشاک', status='PENDING',
               ownership='INDIVIDUAL', has_online='true', has_instore='false',
               created_at='2026-10-06T10:00:00+00:00',
               account_holder='محمد رضا وهاب', owner_name='محمدرضا', owner_family='وهاب')
    workflow.refresh(db, [row], {'SMR-777'})
    case = workflow.get(db, 'SMR-777')
    assert case['ownership'] == 'INDIVIDUAL'
    assert case['source_created_at'] == row['created_at']
    assert case['account_holder'] == row['account_holder']
    assert case['owner_name'] == row['owner_name']
    assert case['owner_family'] == row['owner_family']

    from autoreview import workspace
    online, _unused, both = workspace.shared_queues(db)
    assert not both and len(online) == 1
    shared = online[0]
    assert shared['ownership'] == 'INDIVIDUAL'
    assert shared['created_at'] == row['created_at']
    assert shared['account_holder'] == row['account_holder']
    assert shared['owner_name'] == row['owner_name']
    assert shared['owner_family'] == row['owner_family']


def test_shared_queue_name_fields_are_enough_for_name_rule(db):
    row = dict(smr='SMR-778', site='shop.test', category='مد و پوشاک', status='PENDING',
               ownership='INDIVIDUAL', has_online='true', has_instore='false',
               account_holder='محمدرضا وهاب', owner_name='محمد رضا', owner_family='وهاب')
    workflow.refresh(db, [row], {'SMR-778'})
    from autoreview import workspace
    from autoreview.facts import registrant_name
    from autoreview.normalize import names_equal
    shared = workspace.shared_queues(db)[0][0]
    assert shared['account_holder']
    assert registrant_name(shared)
    assert names_equal(registrant_name(shared), shared['account_holder']) is True
