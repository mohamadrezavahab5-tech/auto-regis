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
        self.props={}
        self.devmeta=[]

    def handle(self, req):
        from urllib.parse import unquote
        if req.method=='GET' and '/values/' not in req.url.path:
            def props(n,v):
                p={'sheetId':self.ids[n],'title':n,'gridProperties':{'rowCount':1000,'columnCount':len(v[0])}}
                for k,val in self.props.get(self.ids[n],{}).items():
                    if isinstance(val,dict): p.setdefault(k,{}).update(val)
                    else: p[k]=val
                return p
            return httpx.Response(200,json={'spreadsheetId':'x'*30,'properties':{'title':'test'},'developerMetadata':self.devmeta,
                'sheets':[{'properties':props(n,v),
                           'protectedRanges':[p for p in self.protections if p['range']['sheetId']==self.ids[n]]} for n,v in self.tabs.items()]})
        if req.method=='GET':
            name=unquote(req.url.path.split('/values/')[1]).split('!')[0].strip("'")
            return httpx.Response(200,json={'values':self.tabs[name]})
        self.writes+=1
        if self.fail: return httpx.Response(503,json={})
        data=json.loads(req.content)
        for request in data['requests']:                      # like Google: a cell past the grid's last column is refused
            u=request.get('updateCells')
            if u:
                name=next(n for n,i in self.ids.items() if i==u['start']['sheetId'])
                width=len(self.tabs[name][0])
                grow=sum(r['appendDimension']['length'] for r in data['requests'] if r.get('appendDimension',{}).get('sheetId')==u['start']['sheetId']
                         and r['appendDimension']['dimension']=='COLUMNS')
                if u['start'].get('columnIndex',0)+max(len(x['values']) for x in u['rows'])>width+grow:
                    return httpx.Response(400,json={'error':{'message':'GridCoordinate.columnIndex is after last column in grid'}})
        self.requests += data['requests']
        for request in data['requests']:
            a=request.get('appendDimension')
            if a and a['dimension']=='COLUMNS':
                name=next(n for n,i in self.ids.items() if i==a['sheetId'])
                for row in self.tabs[name]: row.extend(['']*a['length'])
            if 'addProtectedRange' in request:
                p=dict(request['addProtectedRange']['protectedRange']); p['protectedRangeId']=len(self.protections)+100+self.writes*1000
                self.protections.append(p)
            if 'deleteProtectedRange' in request:
                gone=request['deleteProtectedRange']['protectedRangeId']
                self.protections=[p for p in self.protections if p['protectedRangeId']!=gone]
            props=request.get('updateSheetProperties',{}).get('properties')
            if props:
                mine=self.props.setdefault(props['sheetId'],{})
                for k,v in props.items():
                    if k=='sheetId': continue
                    if isinstance(v,dict): mine.setdefault(k,{}).update(v)
                    else: mine[k]=v
            if 'createDeveloperMetadata' in request:
                m=dict(request['createDeveloperMetadata']['developerMetadata']); m['metadataId']=len(self.devmeta)+1
                self.devmeta.append(m)
            if 'updateDeveloperMetadata' in request:
                u=request['updateDeveloperMetadata']; wanted=u['dataFilters'][0]['developerMetadataLookup']['metadataId']
                for m in self.devmeta:
                    if m['metadataId']==wanted: m.update(u['developerMetadata'])
            if 'sortRange' in request:
                s=request['sortRange']; r=s['range']; key=s['sortSpecs'][0]['dimensionIndex']
                name=next(n for n,i in self.ids.items() if i==r['sheetId']); rows=self.tabs[name]
                part=rows[r['startRowIndex']:r['endRowIndex']]
                part.sort(key=lambda row: (str(row[key]) if key<len(row) else '')=='' and (1,'') or (0,str(row[key])))
                rows[r['startRowIndex']:r['endRowIndex']]=part
            add=request.get('addSheet')
            if add:
                name=add['properties']['title']; self.tabs[name]=[['']*26]; self.ids[name]=max(self.ids.values())+1
            u=request.get('updateCells')
            if not u: continue
            start=u['start']; name=next(n for n,i in self.ids.items() if i==start['sheetId'])
            target=self.tabs[name]; col=start.get('columnIndex',0)
            for k,r in enumerate(u['rows']):
                row=start['rowIndex']+k
                while len(target)<=row: target.append(['']*len(target[0]))
                for j,cell in enumerate(r['values']):
                    while len(target[row])<=col+j: target[row].append('')
                    if 'userEnteredValue' not in cell: continue          # a header note, not a value
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
        assert set(c.ensure_tabs(['دلیل الف']))=={gs.OI_TAB,gs.EXEC_TAB,gs.UPD_TAB,gs.LEGAL_TAB,gs.REPORT_TAB}
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
        assert row[0]=='SMR-1' and row[4]=='تایید قرارداد' and row[12]==gs.OI_STATE_TEXT['WAIT_INSTORE']
        row[8]='تایید قرارداد'; row[11]='سارا'                       # the Instore team fills its own cells
        (smr,cells),=c.instore_entries()
        assert workflow.sheet_verdict(db,smr,'instore',cells,{'edit':{},'cancel':{}})=='ثبت شد'
        c.write_online_instore([workflow.sheet_row(workflow.get(db,'SMR-1'))])
        assert row[8]=='تایید قرارداد' and row[11]=='سارا' and row[12]==gs.OI_STATE_TEXT['READY']
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


def test_legal_tab_lists_crm_company_requests_and_never_writes_the_team_columns():
    gs._SORTED.clear()
    b=full_book()
    rows=[dict(caseid=f'MRG-{i}',status='در دست بررسی تیم فروش',site='a.ir',brand='ب',created_on=f'2026-10-0{i+1}T10:00:00Z',
               modified_on='m1') for i in range(3)]
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        tab=b.tabs[gs.LEGAL_TAB]
        assert c.write_legal(rows)==3 and c.legal_pending==3
        assert [r[0] for r in tab[1:4]]==['MRG-2','MRG-1','MRG-0']             # newest first
        next(r for r in tab if r[0]=='MRG-2')[7]='تایید'                        # the Legal checker's verdict
        gs._SORTED.clear()
        assert c.write_legal(rows)==0 and c.legal_pending==2
        assert [r[0] for r in tab[1:4]]==['MRG-1','MRG-0','MRG-2']             # a checked request goes down
        rows[1]=dict(rows[1],status='لغو درخواست',modified_on='m2')
        gs._SORTED.clear()
        assert c.write_legal(rows)==0 and c.legal_pending==1
        assert [r[0] for r in tab[1:4]]==['MRG-0','MRG-1','MRG-2']             # closed in CRM: below the open one
        assert c.write_legal(rows+[dict(rows[0],caseid='MRG-9')],limit=1)==1
    assert next(r for r in tab if r[0]=='MRG-1')[4]=='لغو درخواست'
    assert next(r for r in tab if r[0]=='MRG-2')[7]=='تایید'                    # the checker's cell moved with its row


def test_legal_rows_come_from_crm_company_requests_in_the_chosen_statuses():
    from autoreview import reference
    db=store.connect(); reference.ensure(db)
    rows=[dict(caseid='MRG-1',status='در دست بررسی تیم فروش',person_company='حقوقی',created_on='2'),
          dict(caseid='MRG-2',status='در دست بررسی تیم فروش',person_company='حقیقی',created_on='1'),
          dict(caseid='MRG-3',status='لغو درخواست',person_company='حقوقی',created_on='3')]
    reference.upsert_crm(db,[dict(dict.fromkeys(('site','brand','store_type','modified_on'),''),**r) for r in rows],'test',full=True)
    got=reference.legal_rows(db,{'legal':{'crm_statuses':['در دست بررسی تیم فروش']}})
    assert [r['caseid'] for r in got]==['MRG-1']
    assert [r['caseid'] for r in reference.legal_rows(db,{'legal':{'crm_statuses':'all'}})]==['MRG-1','MRG-3']


def test_people_see_three_tabs_the_rest_is_hidden_not_deleted():
    b=full_book(); b.tabs['My notes']=[['x']]; b.ids['My notes']=98; b.tabs['Guide']=[['x']]; b.ids['Guide']=97
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        assert c.tidy()>0
        assert c.tidy()==0                                       # already tidy: nothing sent
    hidden={n for n,i in b.ids.items() if b.props.get(i,{}).get('hidden')}
    assert hidden=={'Decisions','Audit','Updates','Results','Manual queue','Guide'}      # Execution is shown: what was sent to NBO
    assert [b.props[b.ids[n]]['index'] for n in (gs.OI_TAB,'Workflow',gs.EXEC_TAB,gs.LEGAL_TAB)]==[0,1,2,3]
    assert set(b.tabs)>=hidden                                   # nothing deleted


def _both(db, verdicts, when='2026-10-01T08:00:00+00:00'):
    """verdicts: {smr: engine action or None} - all loaded at once (a refresh without a request means it left NBO)."""
    workflow.refresh(db,[dict(smr=smr,site=smr.lower()+'.ir',has_instore='true') for smr in verdicts],set(verdicts))
    for smr,action in verdicts.items():
        if action:
            workflow.suggest(db,smr,dict(action=action,reason_codes=['X'] if action!='APPROVE' else [],notes=[],decided_at=when),
                             engine_counts=True)


def test_online_instore_rows_needing_the_instore_team_come_first_with_their_cells():
    gs._SORTED.clear()
    b=full_book()
    db=store.connect(); workflow.ensure(db)
    # SMR-B: nobody looked yet (waits for Online); SMR-C: Online asks for an edit (applied without Instore);
    # SMR-A: Online approved (Instore's turn)
    _both(db,{'SMR-B':None,'SMR-C':'EDIT','SMR-A':'APPROVE'})
    rows=lambda: [workflow.sheet_row(c) for c in workflow.cases(db)]
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        c.write_online_instore(rows())
        tab=b.tabs[gs.OI_TAB]
        assert [r[0] for r in tab[1:4]]==['SMR-A','SMR-C','SMR-B']
        assert tab[1][12]==gs.OI_STATE_TEXT['WAIT_INSTORE'] and tab[3][12]==gs.OI_STATE_TEXT['WAIT_ONLINE']
        next(r for r in tab if r[0]=='SMR-B')[10]='یادداشت Instore'            # typed in a row that is not its turn yet
        _both(db,{'SMR-B':'APPROVE','SMR-C':'EDIT','SMR-A':'APPROVE'})       # now Online approved SMR-B too
        c.write_online_instore(rows())                  # sorted a moment ago: the order waits (people are typing)
        assert [r[0] for r in tab[1:4]]==['SMR-A','SMR-C','SMR-B']
        gs._SORTED.clear()
        c.write_online_instore(rows())
        assert [r[0] for r in tab[1:4]][:2]==['SMR-A','SMR-B'] or [r[0] for r in tab[1:4]][:2]==['SMR-B','SMR-A']
        assert next(r for r in tab if r[0]=='SMR-B')[10]=='یادداشت Instore'     # the team's cell moved with its request
    db.close()


def test_a_sheet_from_the_previous_version_gets_only_the_new_column_titles():
    b=full_book()
    old=gs.OI_HEAD[:-1]
    b.tabs[gs.OI_TAB]=[old+['']*(26-len(old)), ['SMR-1','a.ir']+['']*24]
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        c.ensure_tabs([])
        assert b.tabs[gs.OI_TAB][0][:len(gs.OI_HEAD)]==gs.OI_HEAD
        assert c.read(gs.OI_TAB,gs.OI_HEAD)[0][:2]==['SMR-1','a.ir']
    b.tabs[gs.OI_TAB][0][3]='something else'
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        with pytest.raises(gs.GoogleSheetError): c.ensure_tabs([])               # a reshaped tab still stops everything


def test_the_look_is_applied_once_per_version():
    b=full_book()
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        c.ensure_tabs([])
        assert c.style() is True
        sent=[next(iter(r)) for r in b.requests]
        assert sent.count('addChart')==4 and sent.count('addBanding')==4
        rules=[r['addConditionalFormatRule']['rule'] for r in b.requests if 'addConditionalFormatRule' in r]
        assert any('=LEFT($O2,1)="1"' in v['userEnteredValue'] for r in rules for v in r['booleanRule']['condition']['values'])
        n=len(b.requests)
        assert c.style() is False and len(b.requests)==n                         # already styled: nothing sent


def test_report_numbers_follow_the_app_logic():
    from autoreview import sheet_report, rules as R
    db=store.connect(); workflow.ensure(db)
    from autoreview import execution; execution.ensure(db)
    _both(db,{'SMR-A':'APPROVE','SMR-C':'EDIT','SMR-B':None})
    store.start_run(db,'r1',3)
    store.save_result(db,'r1',dict(smr='SMR-A',site='a.ir'),R.Decision('APPROVE'),{})
    store.save_result(db,'r1',dict(smr='SMR-C',site='c.ir'),R.Decision('EDIT',reason_codes=['MISSING_LICENSE']),{})
    store.save_result(db,'r1',dict(smr='SMR-X',site='x.ir'),R.Decision('MANUAL',notes=['internal error: boom'],trace=['ERROR']),{})
    data=sheet_report.report(db,legal_pending=12)
    now=dict(data['now'])
    assert now['نوبت تیم Instore']==1 and now['آماده‌ی اعمال در NBO']==1 and now['منتظر بررسی موتور']==1
    assert now['Legal بررسی‌نشده']==12
    label,counts,applied=data['periods'][0]
    assert counts=={'APPROVE':1,'EDIT':1,'CANCEL':0,'MANUAL':0}                 # the internal error is not a decision
    grid=sheet_report.grid(data)
    assert len(grid)==sheet_report.ROW['note']+1 and all(len(r)==sheet_report.WIDTH for r in grid)
    b=full_book()
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        c.ensure_tabs([])
        c.write_report(grid)
    assert b.tabs[gs.REPORT_TAB][0][0]=='گزارش AutoReview' and b.tabs[gs.REPORT_TAB][sheet_report.ROW['now_values']][0]==1
    db.close()


def test_a_tab_made_exactly_as_wide_as_its_old_header_grows_for_the_new_column():
    b=full_book()
    b.tabs['Workflow']=[gs.WORKFLOW_HEAD[:-1]]                       # 17 columns, no spare one (the live sheet)
    with gs.Client('x'*30,transport=httpx.MockTransport(b.handle)) as c:
        c.ensure_tabs([])
        assert b.tabs['Workflow'][0]==gs.WORKFLOW_HEAD
        assert any(r.get('appendDimension',{}).get('dimension')=='COLUMNS' for r in b.requests)
