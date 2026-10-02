import json

import httpx
import pytest

from autoreview import google_sheet as gs, workflow, store


class Book:
    def __init__(self):
        self.tabs = {'Workflow':[gs.WORKFLOW_HEAD], 'Decisions':[gs.COMMAND_HEAD],
                     'Audit':[gs.EVENT_HEAD], 'Results':[gs.RESULT_HEAD], 'Manual queue':[gs.MANUAL_HEAD]}
        self.ids={n:i+1 for i,n in enumerate(self.tabs)}
        self.writes=0
        self.requests=[]
        self.fail=False
        self.protections=[]

    def handle(self, req):
        from urllib.parse import unquote
        if req.method=='GET' and '/values/' not in req.url.path:
            return httpx.Response(200,json={'spreadsheetId':'x'*30,'properties':{'title':'test'},
                'sheets':[{'properties':{'sheetId':self.ids[n],'title':n,'gridProperties':{'rowCount':1000,'columnCount':len(v[0])}},
                           'protectedRanges':[p for p in self.protections if p['range']['sheetId']==self.ids[n]]} for n,v in self.tabs.items()]})
        if req.method=='GET':
            name=unquote(req.url.path.split('/values/')[1]).split('!')[0].strip("'")
            return httpx.Response(200,json={'values':self.tabs[name]})
        self.writes+=1
        if self.fail: return httpx.Response(503,json={})
        data=json.loads(req.content)
        self.requests += data['requests']
        for request in data['requests']:
            if 'addProtectedRange' in request:
                p=dict(request['addProtectedRange']['protectedRange']); p['protectedRangeId']=len(self.protections)+100+self.writes*1000
                self.protections.append(p)
            if 'deleteProtectedRange' in request:
                gone=request['deleteProtectedRange']['protectedRangeId']
                self.protections=[p for p in self.protections if p['protectedRangeId']!=gone]
            add=request.get('addSheet')
            if add:
                name=add['properties']['title']; self.tabs[name]=[['']*26]; self.ids[name]=max(self.ids.values())+1
            u=request.get('updateCells')
            if not u: continue
            start=u['start']; name=next(n for n,i in self.ids.items() if i==start['sheetId'])
            target=self.tabs[name]; row=start['rowIndex']; col=start.get('columnIndex',0)
            while len(target)<=row: target.append(['']*len(target[0]))
            for j,cell in enumerate(u['rows'][0]['values']):
                target[row][col+j]=next(iter(cell['userEnteredValue'].values()))
        return httpx.Response(200,json={})


@pytest.fixture
def book(): return Book()


def payload():
    db=store.connect(); workflow.ensure(db)
    workflow.refresh(db,[dict(smr='SMR-12345',site='=HYPERLINK("bad")',has_instore='true')],{'SMR-12345'})
    result=workflow.pending(db); db.close(); return result


def test_projection_and_audit_retry_deduplicate(book):
    p=payload()
    with gs.Client('x'*30,transport=httpx.MockTransport(book.handle)) as c:
        c.sync(p); c.sync(p)
    assert len(book.tabs['Workflow'])==2 and len(book.tabs['Audit'])==2
    assert book.tabs['Workflow'][1][2]=='=HYPERLINK("bad")'
    assert book.tabs['Workflow'][1][12]=='منتظر نظر Online'


def test_failed_atomic_write_can_be_retried(book):
    p=payload(); book.fail=True
    with gs.Client('x'*30,transport=httpx.MockTransport(book.handle)) as c:
        with pytest.raises(gs.GoogleSheetError): c.sync(p)
        assert len(book.tabs['Workflow'])==1
        book.fail=False; c.sync(p)
    assert len(book.tabs['Audit'])==2


def test_foreign_writer_and_newer_sheet_revision_fail_closed(book):
    p=payload()
    with gs.Client('x'*30,transport=httpx.MockTransport(book.handle)) as c:
        c.sync(p)
        p['device_id']='other'
        with pytest.raises(gs.GoogleSheetError): c.sync(p)
        p['device_id']=book.tabs['Workflow'][1][16]
        book.tabs['Workflow'][1][13]=999
        with pytest.raises(gs.GoogleSheetError): c.sync(p)


def test_command_capture_freezes_explicit_submission_and_ack(book):
    blank=['']*13
    row=['','SMR-12345','instore','APPROVE','','verified','person',1,False,'','','','']
    book.tabs['Decisions'] += [blank,row]
    with gs.Client('x'*30,transport=httpx.MockTransport(book.handle)) as c:
        assert c.commands()['commands']==[]
        row[8]=True
        first=c.commands()['commands'][0]
        assert first['team']=='instore' and first['revision']==1
        row[3]='CANCEL'  # edits AFTER explicit submission do not rewrite a captured command
        assert c.commands()['commands'][0]==first
        c.ack([dict(command_id=first['command_id'],status='accepted',error='')])
        assert c.commands()['commands']==[]


def test_result_history_replay_does_not_duplicate_manual_queue(book):
    r=dict(smr='SMR-1',site='s',category='c',action='MANUAL',action_fa='دستی',codes='',reasons_fa='',notes_fa='check',decided_at='2026-10-01T00:00:00Z')
    with gs.Client('x'*30,transport=httpx.MockTransport(book.handle)) as c:
        assert c.append_run('run1',[r])['appended']==1
        assert c.append_run('run1',[r])['duplicate']
    assert len(book.tabs['Manual queue'])==2


def test_schema_change_blocks_writes(book):
    book.tabs['Workflow'][0]=['changed']+gs.WORKFLOW_HEAD[1:]
    with gs.Client('x'*30,transport=httpx.MockTransport(book.handle)) as c:
        with pytest.raises(gs.GoogleSheetError): c.sync(payload())
    assert book.writes==0


def full_book():
    b=Book()
    for name,head in gs.TABS.items():
        b.tabs.setdefault(name,[list(head)]); b.ids.setdefault(name,max(b.ids.values())+1)
    return b


def test_missing_tabs_are_created_with_dropdowns_and_existing_ones_left_alone():
    b=Book()
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        assert set(c.ensure_tabs(['دلیل الف']))=={gs.OI_TAB,gs.EXEC_TAB,gs.UPD_TAB}
        assert c.ensure_tabs([])==[]
    assert b.tabs[gs.OI_TAB][0][:len(gs.OI_HEAD)]==gs.OI_HEAD
    rules=[r['setDataValidation'] for r in b.requests if 'setDataValidation' in r]
    assert any(v['userEnteredValue']=='تایید قرارداد' for r in rules for v in r['rule']['condition'].get('values',[]))
    assert not any(r['range']['sheetId']==b.ids['Workflow'] for r in rules)      # untouched: it already had its header


def test_a_reshaped_tab_stops_setup():
    b=full_book(); b.tabs[gs.OI_TAB][0]=['something else']
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        with pytest.raises(gs.GoogleSheetError): c.ensure_tabs([])


def test_online_instore_tab_writes_only_app_columns_and_reads_the_instore_ones():
    b=full_book()
    db=store.connect(); workflow.ensure(db)
    workflow.refresh(db,[dict(smr='SMR-1',site='a.ir',has_instore='true')],{'SMR-1'})
    workflow.suggest(db,'SMR-1',dict(action='APPROVE',reason_codes=[],notes=[],decided_at='2026-10-01T08:00:00+00:00'),engine_counts=True)
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        assert c.write_online_instore([workflow.sheet_row(workflow.get(db,'SMR-1'))])==1
        row=b.tabs[gs.OI_TAB][1]
        assert row[0]=='SMR-1' and row[4]=='تایید قرارداد' and row[12]=='منتظر نظر Instore'
        row[8]='تایید قرارداد'; row[11]='سارا'                       # the Instore team fills its own cells
        (smr,cells),=c.instore_entries()
        assert workflow.sheet_verdict(db,smr,'instore',cells,{'edit':{},'cancel':{}})=='ثبت شد'
        c.write_online_instore([workflow.sheet_row(workflow.get(db,'SMR-1'))])
        assert row[8]=='تایید قرارداد' and row[11]=='سارا' and row[12].startswith('آماده تأیید')
        writes=b.writes
        c.write_online_instore([workflow.sheet_row(workflow.get(db,'SMR-1'))])
        assert b.writes==writes                                       # nothing changed: nothing written
    db.close()


def test_execution_receipts_are_upserted_per_revision():
    b=full_book()
    rec=dict(smr='SMR-1',revision=3,label='آماده',updated_at='t1',detail='')
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        c.upsert_execution([rec]); c.upsert_execution([dict(rec,label='در NBO تأیید شد',updated_at='t2')])
    assert len(b.tabs[gs.EXEC_TAB])==2 and b.tabs[gs.EXEC_TAB][1][2]=='در NBO تأیید شد'


def test_releases_are_published_once_and_read_back():
    b=full_book()
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        c.publish_release('1.2.0','https://drive.google.com/file/d/'+'a'*30+'/view','f'*64,'notes','owner')
        with pytest.raises(gs.GoogleSheetError): c.publish_release('1.2.0','https://x','f'*64,'','owner')
        assert c.releases()[0]['version']=='1.2.0'


def test_only_real_service_account_keys_are_found(tmp_path):
    from autoreview import google_credentials as gc
    (tmp_path / 'other.json').write_text('{"type": "authorized_user"}', encoding='utf-8')
    (tmp_path / 'broken.json').write_text('{', encoding='utf-8')
    assert gc.find_key_file([tmp_path]) is None
    key = dict(type='service_account', private_key='-----BEGIN PRIVATE KEY-----x', client_email='a@b.iam.gserviceaccount.com',
               token_uri='https://oauth2.googleapis.com/token')
    (tmp_path / 'k.json').write_text(json.dumps(key), encoding='utf-8')
    assert gc.find_key_file([tmp_path]) == (str(tmp_path / 'k.json'), 'a@b.iam.gserviceaccount.com')


def test_a_rate_limit_is_waited_out_not_reported(monkeypatch):
    b = full_book()
    monkeypatch.setattr(gs, 'RETRY_WAITS', (0,))
    calls = []

    def handle(req):
        calls.append(req.method)
        if len(calls) == 1:
            return httpx.Response(429, json={'error': {'message': 'Quota exceeded'}})
        return b.handle(req)
    with gs.Client('x' * 30, transport=httpx.MockTransport(handle)) as c:
        assert c.ping()['sheet'] == 'test'
    assert len(calls) == 2


def test_audit_dedup_reads_only_the_id_column(book):
    p = payload()
    seen = []

    def handle(req):
        seen.append(str(req.url))
        return book.handle(req)
    with gs.Client('x' * 30, transport=httpx.MockTransport(handle)) as c:
        c.sync(p)
    assert any("Audit'!A1:A" in u.replace('%27', "'").replace('%21', '!').replace('%3A', ':') for u in seen)


def covered(b, tab, row, col):
    sid=b.ids[tab]
    for p in b.protections:
        r=p['range']
        if r['sheetId']!=sid: continue
        if r.get('startRowIndex',0)<=row<r.get('endRowIndex',10**6) and r.get('startColumnIndex',0)<=col<r.get('endColumnIndex',10**6):
            return p
    return None


def test_the_sheet_is_locked_except_the_instore_cells():
    b=full_book(); b.tabs['Notes of mine']=[['x']]; b.ids['Notes of mine']=99
    b.protections.append({'protectedRangeId':7,'range':{'sheetId':b.ids[gs.OI_TAB],'startColumnIndex':0,'endColumnIndex':7},
                          'warningOnly':True,'description':'AutoReview این ستون‌ها را پر می‌کند'})
    b.protections.append({'protectedRangeId':8,'range':{'sheetId':b.ids['Workflow']},'description':'owner rule'})
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        assert c.lock(['Online@SnappPay.ir']) is True
        for col in range(7,12):                                     # Instore team: their own columns stay open
            assert covered(b,gs.OI_TAB,5,col) is None
            assert covered(b,gs.OI_TAB,0,col)                       # ... but not their header
        for col in (0,4,6,12,13,20):
            assert covered(b,gs.OI_TAB,5,col)
        for tab in ('Workflow','Decisions','Audit','Notes of mine'):
            assert covered(b,tab,3,2)
        ours=[p for p in b.protections if p['description'].startswith('AutoReview')]
        assert all(not p['warningOnly'] and p['editors']['users']==['online@snapppay.ir'] for p in ours)
        assert not any(p['protectedRangeId']==7 for p in b.protections)        # the old warning is replaced
        assert any(p['protectedRangeId']==8 for p in b.protections)            # the owner's own protection is untouched
        writes=b.writes
        assert c.lock(['online@snapppay.ir']) is False and b.writes==writes  # nothing to change: nothing sent
        assert c.lock(['online@snapppay.ir','new@snapppay.ir']) is True
        assert len([p for p in b.protections if p['description'].startswith('AutoReview')])==len(ours)
