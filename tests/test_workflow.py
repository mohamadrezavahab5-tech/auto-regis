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


def test_failed_network_does_not_acknowledge_outbox(db):
    imported(db)
    cfg=sheets.load(); cfg.update(workflow_sync=True,webapp_url='https://script.google.com/macros/s/'+'a'*35+'/exec')
    def fail(r): raise httpx.ConnectError('offline',request=r)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(sheets.SheetError): workflow_sync.sync(db,cfg,client)
    assert workflow.pending_count(db)==1


def test_sync_rejects_wrong_sheet_without_uploading(db):
    imported(db)
    cfg=sheets.load(); cfg.update(workflow_sync=True,webapp_url='https://script.google.com/macros/s/'+'a'*35+'/exec')
    seen=[]
    def handle(r):
        seen.append(json.loads(r.content)['action'])
        return httpx.Response(200,json=dict(ok=True,version=3,sheet_id='wrong'))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(sheets.SheetError): workflow_sync.sync(db,cfg,client)
    assert seen==['ping'] and workflow.pending_count(db)==1


def test_sync_acknowledges_only_verified_counts_and_reads_commands(db):
    imported(db)
    cfg=sheets.load(); cfg.update(workflow_sync=True,webapp_url='https://script.google.com/macros/s/'+'a'*35+'/exec')
    def handle(r):
        body=json.loads(r.content)
        if body['action']=='ping': data=dict(version=3,sheet_id=cfg['own_sheet_id'])
        elif body['action']=='workflow_sync': data=dict(cases=len(body['cases']),events=len(body['events']))
        else: data=dict(commands=[])
        return httpx.Response(200,json=dict(ok=True,**data))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert workflow_sync.sync(db,cfg,client)['remaining']==0


def test_script_is_own_sheet_only_and_shared_writer_disabled():
    code=sheets.script_code()
    assert 'openById' not in code and 'ONLINE_INSTORE_ID' not in code
    assert '__OWN_ID__' not in code and '__SECRET__' not in code
    cfg=sheets.load(); cfg['oi']['enabled']=True
    with pytest.raises(sheets.SheetError): sheets.oi_write('r',[],cfg=cfg)
