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
        self.fail=False

    def handle(self, req):
        from urllib.parse import unquote
        if req.method=='GET' and '/values/' not in req.url.path:
            return httpx.Response(200,json={'spreadsheetId':'x'*30,'properties':{'title':'test'},
                'sheets':[{'properties':{'sheetId':self.ids[n],'title':n,'gridProperties':{'rowCount':1000,'columnCount':len(v[0])}}} for n,v in self.tabs.items()]})
        if req.method=='GET':
            name=unquote(req.url.path.split('/values/')[1]).split('!')[0].strip("'")
            return httpx.Response(200,json={'values':self.tabs[name]})
        self.writes+=1
        if self.fail: return httpx.Response(503,json={})
        data=json.loads(req.content)
        for request in data['requests']:
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
