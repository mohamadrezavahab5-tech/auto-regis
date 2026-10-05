import copy
import json
import re
import uuid

import httpx
import pytest

from autoreview import crm_sync, google_credentials, sheets, workspace
from autoreview import workspace_google as wg


class Book:
    """Sheets transport fake: atomic batches and appendCells, no business rules."""
    def __init__(self):
        self.tabs = {}
        self.requests = []
        self.fail = None
        self.before_append = None

    def request(self, method, suffix='', **kwargs):
        self.requests.append((method, suffix, copy.deepcopy(kwargs)))
        if self.fail:
            raise wg.WorkspaceError(self.fail)
        if method == 'GET' and not suffix:
            return {'spreadsheetId': wg.EXPECTED_SHEET_ID,
                    'sheets': [{'properties': {'title': name, 'sheetId': tab['id'],
                     'gridProperties': {'rowCount': 1000, 'columnCount': 20}}} for name, tab in self.tabs.items()]}
        if method == 'GET':
            result = []
            for range_ in kwargs['params']['ranges']:
                name, cells = range_.split('!'); name = name.strip("'")
                data = self.tabs[name]['rows']
                if cells == '1:1': values = data[:1]
                else:
                    match = re.fullmatch(r'([A-Z]+)(\d+):([A-Z]+)(\d*)', cells)
                    left, start, right, end = match.groups()
                    values = [r[ord(left)-65:ord(right)-64] for r in data[int(start)-1:int(end) if end else None]]
                result.append({'values': copy.deepcopy(values)})
            return {'valueRanges': result}
        requests = kwargs['json']['requests']
        if any('appendCells' in r for r in requests) and self.before_append:
            callback, self.before_append = self.before_append, None
            callback()
        staged = copy.deepcopy(self.tabs)
        for request in requests:
            if 'addSheet' in request:
                p = request['addSheet']['properties']
                if p['title'] in staged: raise wg.WorkspaceError('GOOGLE_API_ERROR')
                staged[p['title']] = {'id': p['sheetId'], 'rows': []}
            elif 'duplicateSheet' in request:
                p = request['duplicateSheet']
                original = next(t for t in staged.values() if t['id'] == p['sourceSheetId'])
                staged[p['newSheetName']] = {'id': p['newSheetId'], 'rows': copy.deepcopy(original['rows'])}
            else:
                append = 'appendCells' in request
                p = request.get('appendCells') or request['updateCells']
                start = p.get('start', {})
                tab = next(t for t in staged.values() if t['id'] == p.get('sheetId', start.get('sheetId')))
                at = len(tab['rows']) if append else start['rowIndex']
                for i, record in enumerate(p['rows']):
                    while len(tab['rows']) <= at+i: tab['rows'].append([])
                    for j, item in enumerate(record['values']):
                        col = start.get('columnIndex', 0)+j
                        while len(tab['rows'][at+i]) <= col: tab['rows'][at+i].append('')
                        tab['rows'][at+i][col] = next(iter(item['userEnteredValue'].values()))
        self.tabs = staged
        return {}


def case(smr='SMR-1'):
    return dict(smr=smr, channel='online', active=True, fingerprint='a'*64,
                source_status='PENDING', source_loaded_at=wg.stamp(),
                online={'source':'engine','action':'APPROVE'}, instore=None,
                suggestion={'action':'APPROVE','decided_at':wg.stamp()})


def call(backend, action, actor='alice', **payload):
    payload.setdefault('operation_id', uuid.uuid4().hex)
    return backend.call(action, actor, 'machine-'+actor, 'test', **payload)


@pytest.fixture
def pair():
    book = Book()
    return book, wg.Backend(client=book), wg.Backend(client=book)


def test_health_creates_only_additive_schema_and_proves_write_access(pair):
    book, a, b = pair
    assert a.health()['write_ok']
    assert b.health()['sheet_ok']
    assert set(book.tabs) == set(wg.HEADERS)
    assert book.tabs[wg.META]['rows'][1] == ['schema', 6]


def test_schema_validation_never_repairs_over_existing_data(pair):
    book, a, _ = pair; a.health()
    book.tabs[wg.JOURNAL]['rows'][0][0] = 'Changed by user'
    before = copy.deepcopy(book.tabs)
    with pytest.raises(wg.WorkspaceError) as e: a.health(force=True)
    assert e.value.code == 'SHEET_SCHEMA_ERROR'
    assert book.tabs == before


def test_legacy_state_and_execution_are_preserved_by_atomic_snapshot(pair):
    book, a, _ = pair; old = case(); old['revision'] = 7
    book.tabs['Workspace state'] = {'id':1,'rows':[wg.STATE_HEAD, ['SMR-1',7,json.dumps(old)]]}
    book.tabs['Workspace execution'] = {'id':2,'rows':[wg.EXEC_HEAD,['SMR-1',7,'old','previous','UNCERTAIN',wg.stamp(),'']]}
    before = copy.deepcopy(book.tabs)
    result = call(a, 'read')
    assert result['cases'][0]['revision'] == 7
    assert result['executions'][0][4] == 'UNCERTAIN'
    assert all(book.tabs[k] == v for k,v in before.items())


def test_source_read_idempotency_and_crm_audit_across_two_pcs(pair):
    book, a, b = pair; op = uuid.uuid4().hex; source = case()
    first = call(a, 'source_batch', operation_id=op, cases=[source])
    assert call(a, 'source_batch', operation_id=op, cases=[source]) == first
    assert len(book.tabs[wg.JOURNAL]['rows']) == 2
    shared = call(b, 'read')
    assert shared['cases'][0]['smr'] == 'SMR-1'
    audit = book.tabs[wg.AUDIT]['rows'][1]
    assert audit[2:5] == ['alice', 'machine-alice', 'test'] and audit[8] == 'OK'
    with pytest.raises(wg.WorkspaceError) as e:
        call(a, 'source_batch', operation_id=op, cases=[case('SMR-2')])
    assert e.value.code == 'WORKSPACE_CONFLICT'


def test_decide_rejects_revision_mismatch_and_late_writer(pair):
    book, a, b = pair; call(a, 'source_batch', cases=[case()])
    decision = dict(smr='SMR-1', revision=1, team='online', decision='MANUAL', note='reviewed')
    # A has read/prepared revision 1; B commits before A's atomic append.
    book.before_append = lambda: call(b, 'decide', actor='bob', **decision)
    with pytest.raises(wg.WorkspaceError) as e: call(a, 'decide', **decision)
    assert e.value.code == 'WORKSPACE_CONFLICT'
    result = call(a, 'read')['cases'][0]
    assert result['revision'] == 2 and result['online']['actor'] == 'bob'
    assert book.tabs[wg.AUDIT]['rows'][-1][8] == 'CONFLICT'


def test_claim_race_only_first_token_owns_execution_and_only_owner_can_finish(pair):
    book, a, b = pair; call(a, 'source_batch', cases=[case()])
    token_a, token_b = uuid.uuid4().hex, uuid.uuid4().hex
    winners = []
    book.before_append = lambda: winners.append(call(b, 'claim_execution', actor='bob', smr='SMR-1',revision=1,claim=token_b))
    with pytest.raises(wg.WorkspaceError): call(a, 'claim_execution', smr='SMR-1',revision=1,claim=token_a)
    assert len(winners) == 1 and winners[0]['claim'] == token_b
    with pytest.raises(wg.WorkspaceError): call(a, 'finish_execution',smr='SMR-1',revision=1,claim=token_b,state='SENT')
    assert call(b, 'finish_execution',actor='bob',smr='SMR-1',revision=1,claim=token_b,state='SENT')['ok']
    assert call(a, 'read')['executions'][0][4] == 'SENT'


def test_read_only_downloads_new_rows_and_reports_unchanged(pair):
    book, a, _ = pair; call(a, 'source_batch', cases=[case()]); result = call(a, 'read')
    book.requests.clear()
    assert call(a,'read',known_generation=result['generation'])['unchanged']
    ranges = [r for m,s,k in book.requests for r in k.get('params',{}).get('ranges',[])]
    assert ranges == [f"'{wg.JOURNAL}'!A3:J"]


def test_source_race_reports_conflict_instead_of_overwriting(pair):
    book, a, b = pair; source = case()
    book.before_append = lambda: call(b, 'source_batch',actor='bob',cases=[source])
    result = call(a, 'source_batch', cases=[source])
    assert result['cases'] == [] and result['rejected'][0]['status'] == 'conflict'
    assert len(call(a,'read')['cases']) == 1


def test_network_failure_preserves_cached_state(pair):
    book,a,_=pair; call(a,'source_batch',cases=[case()]); before=copy.deepcopy(a.cases)
    book.fail='GOOGLE_API_ERROR'
    with pytest.raises(wg.WorkspaceError): call(a,'read')
    assert a.cases==before


@pytest.mark.parametrize('status,reason,code',[(403,'PERMISSION_DENIED','SHEET_ACCESS_DENIED'),
    (404,'NOT_FOUND','SHEET_NOT_FOUND'),(401,'UNAUTHENTICATED','GOOGLE_AUTH_ERROR'),
    (403,'SERVICE_DISABLED','GOOGLE_API_ERROR'),(400,'bad','GOOGLE_API_ERROR')])
def test_google_errors_are_structured_and_never_echo_tokens(status,reason,code):
    transport=httpx.MockTransport(lambda r:httpx.Response(status,json={'error':{'message':reason+' PRIVATE-TEST-VALUE'}}))
    with wg.Client(wg.EXPECTED_SHEET_ID,transport=transport) as client:
        with pytest.raises(wg.WorkspaceError) as e:client.request('GET')
    assert e.value.code==code and 'PRIVATE-TEST-VALUE' not in str(e.value)


def test_missing_and_invalid_dpapi_credentials(monkeypatch):
    with pytest.raises(wg.WorkspaceError) as e:wg.Client(wg.EXPECTED_SHEET_ID)
    assert e.value.code=='GOOGLE_CREDENTIALS_MISSING'
    google_credentials.key_path().write_bytes(b'not-a-real-dpapi-file')
    with pytest.raises(wg.WorkspaceError) as e:wg.Client(wg.EXPECTED_SHEET_ID)
    assert e.value.code=='GOOGLE_AUTH_ERROR'


def test_facade_uses_real_crm_identity_and_never_google_email(monkeypatch,pair):
    book,a,_=pair
    monkeypatch.setattr(wg,'get_backend',lambda cfg:a)
    monkeypatch.setattr(crm_sync,'_authenticated_identity',{'username':'SNAPP\\alice','user_id':'u'})
    assert workspace.call('whoami')['user']['username']=='alice'
    monkeypatch.setattr(crm_sync,'_authenticated_identity',None)
    with pytest.raises(sheets.SheetError):workspace.call('read')


def test_audit_events_keep_original_crm_actor_and_operation_receipts(pair):
    book, a, b = pair
    event = dict(event_id=uuid.uuid4().hex, at=wg.stamp(), actor='alice', kind='WORKSPACE_CONNECT')
    call(a, 'audit_batch', actor='bob', events=[event])
    call(b, 'source_batch', actor='bob', cases=[case()])
    rows = book.tabs[wg.AUDIT]['rows']
    assert rows[2][2] == 'alice' and rows[2][5] == 'WORKSPACE_CONNECT'
    assert rows[-1][8] == 'OK'
    assert call(b, 'read')['cases'][0]['revision'] == 1


def test_lost_write_response_reconciles_without_duplicate_mutation(pair, monkeypatch):
    book, a, _ = pair; original = book.request
    def request(method, suffix='', **kwargs):
        result = original(method, suffix, **kwargs)
        if any('appendCells' in r for r in kwargs.get('json', {}).get('requests', [])):
            raise wg.WorkspaceError('GOOGLE_API_ERROR')
        return result
    monkeypatch.setattr(book, 'request', request)
    assert call(a, 'source_batch', cases=[case()])['ok']
    assert len(book.tabs[wg.JOURNAL]['rows']) == 2


def test_finish_without_current_claim_cannot_report_sent(pair):
    _, a, _ = pair
    with pytest.raises(wg.WorkspaceError):
        call(a, 'finish_execution', smr='SMR-1', revision=1, claim=uuid.uuid4().hex, state='SENT')


def test_health_detects_read_only_access(pair, monkeypatch):
    book, a, _ = pair; a.health(); original = book.request
    def request(method, *args, **kwargs):
        if method == 'POST': raise wg.WorkspaceError('SHEET_ACCESS_DENIED')
        return original(method, *args, **kwargs)
    monkeypatch.setattr(book, 'request', request)
    with pytest.raises(wg.WorkspaceError) as error: a.health(force=True)
    assert error.value.code == 'SHEET_ACCESS_DENIED'


def test_completed_execution_allows_source_refresh_but_not_same_revision_claim(pair):
    _, a, _ = pair; source = case(); token = uuid.uuid4().hex
    call(a, 'source_batch', cases=[source])
    call(a, 'claim_execution', smr='SMR-1', revision=1, claim=token)
    pending = dict(source, active=False, source_status='APPROVED')
    assert call(a, 'source_batch', cases=[pending])['rejected'][0]['status'] == 'rejected'
    call(a, 'finish_execution', smr='SMR-1', revision=1, claim=token, state='SENT')
    with pytest.raises(wg.WorkspaceError):
        call(a, 'claim_execution', smr='SMR-1', revision=1, claim=uuid.uuid4().hex)
    assert call(a, 'source_batch', cases=[pending])['cases'][0]['revision'] == 2
    assert call(a, 'read')['cases'][0]['active'] is False
