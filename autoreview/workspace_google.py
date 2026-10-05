"""Direct Sheets Workspace.

Sheets has no compare-and-swap. A mutable claim cell + readback is NOT a lock.
The atomic appendCells order is the arbitration order: an immutable mutation
journal is folded against expected revisions. A later contender cannot replace
an earlier accepted claim. State/claim JSON and audit metadata share one batch.
Legacy state/execution are copied once, atomically, and never modified here.
"""
import copy
import hashlib
import json
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx

from . import google_credentials, google_sheet, sheets, workflow

EXPECTED_SHEET_ID = '1UHlktMbe6bhDMKQd51Z-H1pvrrgD3ppTl9QmI1cEFrQ'
BASE = 'Workspace baseline v6'
EXEC_BASE = 'Workspace execution baseline v6'
JOURNAL = 'Workspace state v6'
AUDIT = 'Workspace audit v6'
META = 'Workspace meta v6'
STATE_HEAD = ['SMR', 'Revision', 'Case JSON']
EXEC_HEAD = ['SMR', 'Revision', 'Claim', 'User', 'State', 'Updated at', 'Detail']
JOURNAL_HEAD = ['Operation ID', 'Payload hash', 'Action', 'CRM actor', 'Machine ID',
                'Client version', 'Timestamp', 'Mutation JSON', 'Result JSON', 'Error code']
AUDIT_HEAD = ['Event ID', 'Timestamp', 'CRM actor', 'Machine ID', 'Client version',
              'Action', 'SMR', 'Revision', 'Result', 'Error code', 'Detail']
HEADERS = {BASE: STATE_HEAD, EXEC_BASE: EXEC_HEAD, JOURNAL: JOURNAL_HEAD,
           AUDIT: AUDIT_HEAD, META: ['Key', 'Value']}
MESSAGES = {
    'GOOGLE_CREDENTIALS_MISSING': 'فایل Service Account را روی همین کامپیوتر وارد کن.',
    'GOOGLE_AUTH_ERROR': 'کلید Google معتبر یا قابل بازخوانی نیست؛ فایل اصلی را دوباره وارد کن.',
    'SHEET_ACCESS_DENIED': 'به شیت دسترسی نوشتن نداریم؛ ایمیل Service Account باید Editor باشد.',
    'SHEET_NOT_FOUND': 'شیت مشترک پیدا نشد یا برای این Service Account قابل مشاهده نیست.',
    'SHEET_SCHEMA_ERROR': 'ساختار فضای مشترک معتبر نیست؛ داده‌ها تغییر نکردند.',
    'GOOGLE_API_ERROR': 'Google Sheets API در دسترس نیست؛ داده‌های محلی محفوظ‌اند.',
    'WORKSPACE_CONFLICT': 'نسخه یا مالک درخواست تغییر کرده؛ ابتدا همگام‌سازی کن.',
}


class WorkspaceError(sheets.SheetError):
    def __init__(self, code, detail=''):
        super().__init__(MESSAGES.get(code, 'عملیات فضای مشترک انجام نشد.') + (' ' + detail if detail else ''), code)


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def stamp():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.timestamp() if result.tzinfo else float('-inf')
    except (ValueError, TypeError):
        return float('-inf')


def cell(value):
    if isinstance(value, bool): return {'userEnteredValue': {'boolValue': value}}
    if isinstance(value, (int, float)): return {'userEnteredValue': {'numberValue': value}}
    return {'userEnteredValue': {'stringValue': str(value if value is not None else '')}}


def row(values):
    return {'values': [cell(v) for v in values]}


class Client(google_sheet.Client):
    """Existing google-auth/DPAPI transport, with sanitized structured errors."""
    def close(self):
        self.http.close()

    def __init__(self, sheet_id, **kwargs):
        if not kwargs.get('transport') and not google_credentials.available():
            raise WorkspaceError('GOOGLE_CREDENTIALS_MISSING')
        try:
            super().__init__(sheet_id, **kwargs)
        except Exception:
            raise WorkspaceError('GOOGLE_AUTH_ERROR') from None

    def request(self, method, suffix='', **kwargs):
        headers = {}
        if self.credentials is not None:
            try:
                if not self.credentials.valid:
                    self.credentials.refresh(google_sheet.TimedRequest())
                headers['Authorization'] = 'Bearer ' + self.credentials.token
            except Exception:
                raise WorkspaceError('GOOGLE_AUTH_ERROR') from None
        # Mutations may have committed despite a lost response. Never blindly retry them.
        attempts = 3 if method == 'GET' else 1
        for attempt in range(attempts):
            try:
                response = self.http.request(method, self.base + suffix, headers=headers, **kwargs)
            except httpx.HTTPError:
                if attempt + 1 < attempts:
                    time.sleep(random.uniform(.1, .3) * 2 ** attempt)
                    continue
                raise WorkspaceError('GOOGLE_API_ERROR') from None
            if response.status_code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                time.sleep(random.uniform(.1, .3) * 2 ** attempt)
                continue
            if response.status_code != 200:
                try: reason = compact(response.json().get('error', {})).lower()
                except (ValueError, AttributeError): reason = ''
                code = {401: 'GOOGLE_AUTH_ERROR', 403: 'SHEET_ACCESS_DENIED', 404: 'SHEET_NOT_FOUND'}.get(response.status_code, 'GOOGLE_API_ERROR')
                if any(s in reason for s in ('service_disabled', 'accessnotconfigured', 'has not been used', 'api has been disabled')):
                    code = 'GOOGLE_API_ERROR'
                raise WorkspaceError(code, f'HTTP {response.status_code}')
            try: return response.json()
            except ValueError: raise WorkspaceError('GOOGLE_API_ERROR') from None


class Backend:
    def __init__(self, sheet_id=EXPECTED_SHEET_ID, client=None):
        if sheet_id != EXPECTED_SHEET_ID:
            raise WorkspaceError('SHEET_NOT_FOUND')
        self.client = client or Client(sheet_id)
        self.lock = threading.RLock()  # local transport/cache protection only; NOT a cross-PC lock
        self.props = {}
        self.checked = 0
        self.loaded = False
        self.cursor = 0
        self.cases, self.executions, self.ops, self.events = {}, {}, {}, set()
        self.releases = []
        self.operation_rows = {}
        self.audit_rows = {}
        self.audit_cursor = 0

    def ranges(self, ranges):
        result = self.client.request('GET', '/values:batchGet', params={
            'ranges': ranges, 'valueRenderOption': 'UNFORMATTED_VALUE'})
        values = result.get('valueRanges', [])
        if len(values) != len(ranges): raise WorkspaceError('SHEET_SCHEMA_ERROR')
        return [item.get('values', []) for item in values]

    def metadata(self):
        data = self.client.request('GET', params={'fields': 'spreadsheetId,sheets.properties'})
        if data.get('spreadsheetId') != EXPECTED_SHEET_ID: raise WorkspaceError('SHEET_NOT_FOUND')
        self.props = {s['properties']['title']: s['properties'] for s in data.get('sheets', [])}

    def initialize(self):
        """All new tabs appear in one transaction. Concurrent initialization is safe."""
        self.metadata()
        existing = set(HEADERS) & self.props.keys()
        if existing and existing != set(HEADERS): raise WorkspaceError('SHEET_SCHEMA_ERROR')
        if existing: return
        legacy = [(name, header) for name, header in [('Workspace state', STATE_HEAD),
                  ('Workspace execution', EXEC_HEAD)] if name in self.props]
        if legacy:
            for (name, header), values in zip(legacy, self.ranges([f"'{n}'!1:1" for n, _ in legacy])):
                if not values or values[0][:len(header)] != header: raise WorkspaceError('SHEET_SCHEMA_ERROR')
        ids = random.sample(range(100000, 2000000000), len(HEADERS))
        requests = []
        for (name, header), sid in zip(HEADERS.items(), ids):
            original = {BASE: 'Workspace state', EXEC_BASE: 'Workspace execution'}.get(name)
            if original in self.props:
                requests.append({'duplicateSheet': {'sourceSheetId': self.props[original]['sheetId'], 'newSheetId': sid, 'newSheetName': name}})
            else:
                requests.extend([{'addSheet': {'properties': {'sheetId': sid, 'title': name,
                    'gridProperties': {'rowCount': 1000, 'columnCount': max(12, len(header)), 'frozenRowCount': 1}}}},
                    {'updateCells': {'start': {'sheetId': sid, 'rowIndex': 0, 'columnIndex': 0}, 'rows': [row(header)], 'fields': 'userEnteredValue'}}])
            if name == META:
                requests.append({'updateCells': {'start': {'sheetId': sid, 'rowIndex': 1, 'columnIndex': 0},
                    'rows': [row(['schema', 6])], 'fields': 'userEnteredValue'}})
        try:
            self.client.request('POST', ':batchUpdate', json={'requests': requests})
        except WorkspaceError:
            # Another PC may have atomically created the complete schema first.
            self.metadata()
            if not set(HEADERS) <= self.props.keys(): raise
        self.metadata()

    def health(self, force=False):
        with self.lock:
            if force or not self.checked or time.monotonic() - self.checked > 60:
                self.initialize()
                values = self.ranges([f"'{name}'!1:1" for name in HEADERS] + [f"'{META}'!A2:B2"])
                for (name, header), rows in zip(HEADERS.items(), values):
                    if not rows or rows[0][:len(header)] != header: raise WorkspaceError('SHEET_SCHEMA_ERROR')
                if values[-1] != [['schema', 6]]: raise WorkspaceError('SHEET_SCHEMA_ERROR')
                # A harmless idempotent write proves Editor access, unlike a GET alone.
                self.client.request('POST', ':batchUpdate', json={'requests': [{'updateCells': {
                    'start': {'sheetId': self.props[META]['sheetId'], 'rowIndex': 1, 'columnIndex': 0},
                    'rows': [row(['schema', 6])], 'fields': 'userEnteredValue'}}]})
                self.checked = time.monotonic()
            return {'ok': True, 'server_version': 6, 'schema_version': 6, 'sheet_ok': True, 'write_ok': True, 'backend': 'google_sheets'}

    def refresh(self):
        self.health()
        if not self.loaded:
            values = self.ranges([f"'{BASE}'!A2:C", f"'{EXEC_BASE}'!A2:G"])
            initial = {}
            try:
                for r in values[0]:
                    if len(r) != 3 or r[0] in initial: raise ValueError()
                    case = json.loads(r[2])
                    if case['smr'] != r[0] or case['revision'] != r[1]: raise ValueError()
                    initial[r[0]] = case
                ledger = {}
                for r in values[1]:
                    if len(r) < 6 or r[2] in ledger: raise ValueError()
                    ledger[r[2]] = (r + [''])[:7]
            except (ValueError, KeyError, TypeError): raise WorkspaceError('SHEET_SCHEMA_ERROR') from None
            self.cases, self.executions, self.loaded = initial, ledger, True
        # Fetch only new journal rows; no workbook-wide polling or repeated baseline download.
        rows = self.ranges([f"'{JOURNAL}'!A{self.cursor + 2}:J"])[0]
        for raw in rows:
            if len(raw) < 8: raise WorkspaceError('SHEET_SCHEMA_ERROR')
            op, digest, action, actor, machine, version, at, encoded = raw[:8]
            try:
                envelope = json.loads(encoded)
                payload = envelope['payload']
                prepared = envelope.get('prepared', dict(payload, **envelope.get('context', {})))
            except (ValueError, KeyError, TypeError): raise WorkspaceError('SHEET_SCHEMA_ERROR') from None
            actual = hashlib.sha256(compact([action, actor, payload]).encode()).hexdigest()
            if digest != actual: raise WorkspaceError('SHEET_SCHEMA_ERROR')
            if op not in self.ops:
                before = (self.cases, self.executions, self.events, self.releases)
                self.cases, self.executions = dict(self.cases), dict(self.executions)
                self.events, self.releases = set(self.events), list(self.releases)
                try:
                    result = self.apply(action, prepared, actor, at)
                except WorkspaceError as error:
                    self.cases, self.executions, self.events, self.releases = before
                    result = {'ok': False, 'error_code': error.code}
                except (ValueError, KeyError, TypeError):
                    self.cases, self.executions, self.events, self.releases = before
                    result = {'ok': False, 'error_code': 'SHEET_SCHEMA_ERROR'}
                self.ops[op] = (digest, copy.deepcopy(result))
                self.operation_rows[op] = self.cursor + 2
            self.cursor += 1

    def busy(self, smr):
        return any(r[0] == smr and r[4] in ('SENDING', 'UNCERTAIN') for r in self.executions.values())

    def apply(self, action, payload, actor, at):
        """Deterministic reducer. No network, clock reads, or random values here."""
        if action == 'source_batch':
            accepted, rejected = [], []
            for source in payload['cases']:
                smr = source['smr']; old = self.cases.get(smr)
                if self.busy(smr):
                    rejected.append({'smr': smr, 'status': 'rejected'}); continue
                if old and timestamp(source['source_loaded_at']) < timestamp(old.get('source_loaded_at')):
                    rejected.append({'smr': smr, 'status': 'stale'}); continue
                # A race is observable, not an overwrite. Retry safe source data after refreshing.
                if payload['expected'].get(smr) != (old or {}).get('revision', 0):
                    rejected.append({'smr': smr, 'status': 'conflict'}); continue
                case = copy.deepcopy(source)
                case.pop('previous_executions', None)
                changed = not old or any(old.get(k) != case.get(k) for k in ('fingerprint', 'active', 'channel'))
                for team in ('online', 'instore'):
                    incoming = case.get(team)
                    if old and not changed:
                        case[team] = old.get(team)
                        if team == 'online' and (not case[team] or case[team].get('source') == 'engine'):
                            if timestamp((source.get('suggestion') or {}).get('decided_at')) >= timestamp((old.get('suggestion') or {}).get('decided_at')):
                                case[team] = incoming if incoming and incoming.get('source') == 'engine' else None
                    elif old:
                        case[team] = incoming if team == 'online' and incoming and incoming.get('source') == 'engine' else None
                if old and not changed and timestamp((case.get('suggestion') or {}).get('decided_at')) < timestamp((old.get('suggestion') or {}).get('decided_at')):
                    case['suggestion'], case['review'] = old.get('suggestion'), old.get('review')
                relevant = ('fingerprint','active','channel','source_status','outcome','online','instore','suggestion','online_hold')
                case['revision'] = (old or {}).get('revision', 0) + int(not old or any(old.get(k) != case.get(k) for k in relevant))
                case['updated_at'] = at
                case['state'] = workflow.state(case); case['state_fa'] = workflow.STATES[case['state']]
                self.cases[smr] = case
                if not old:
                    history = source.get('previous_executions') or []
                    if history:
                        status = 'UNCERTAIN' if any(r['state'] in ('SENDING','UNCERTAIN') for r in history) else 'SENT'
                        token = 'legacy-' + smr
                        self.executions[token] = [smr, case['revision'], token, actor, status, at, 'Migrated local history']
                accepted.append({'smr': smr, 'revision': case['revision']})
            return {'ok': True, 'cases': accepted, 'rejected': rejected}
        if action == 'decide':
            smr = payload['smr']; case = self.cases.get(smr)
            if not case or self.busy(smr) or not case.get('active') or case['revision'] != payload['revision']:
                raise WorkspaceError('WORKSPACE_CONFLICT')
            team, decision = payload['team'], payload['decision']
            if team == 'instore' and case['channel'] != 'both': raise WorkspaceError('WORKSPACE_CONFLICT')
            case = copy.deepcopy(case)
            case[team] = None if decision == 'REOPEN' else dict(action=decision, actor=actor, note=payload['note'], reason=payload.get('reason',''), at=at, source='workspace')
            case['revision'] += 1; case['updated_at'] = at
            case['state'] = workflow.state(case); case['state_fa'] = workflow.STATES[case['state']]
            self.cases[smr] = case
            return {'ok': True, 'case': copy.deepcopy(case)}
        if action == 'claim_execution':
            from . import execution
            case = self.cases.get(payload['smr'])
            if not case or self.busy(payload['smr']) or case['revision'] != payload['revision']:
                raise WorkspaceError('WORKSPACE_CONFLICT')
            if any(r[0] == case['smr'] and r[1] == case['revision'] and
                   r[4] in ('SENT', 'VERIFIED', 'APPROVED_IN_NBO') for r in self.executions.values()):
                raise WorkspaceError('WORKSPACE_CONFLICT')
            if execution.eligibility(case, datetime.fromisoformat(at)):
                raise WorkspaceError('WORKSPACE_CONFLICT')
            if payload.get('automatic') and (actor != 'mohammadreza.vahab' or case['channel'] != 'online'):
                raise WorkspaceError('WORKSPACE_CONFLICT')
            claim = payload['claim']
            if claim in self.executions: raise WorkspaceError('WORKSPACE_CONFLICT')
            self.executions[claim] = [case['smr'], case['revision'], claim, actor, 'SENDING', at, '']
            return {'ok': True, 'claim': claim, 'case': copy.deepcopy(case)}
        if action == 'finish_execution':
            record = self.executions.get(payload['claim'])
            if record is None:
                if payload['state'] == 'BLOCKED': return {'ok': True, 'not_found': True}
                raise WorkspaceError('WORKSPACE_CONFLICT')
            if record[:4] != [payload['smr'], payload['revision'], payload['claim'], actor]:
                raise WorkspaceError('WORKSPACE_CONFLICT')
            if record[4] == payload['state']: return {'ok': True, 'idempotent': True}
            if record[4] != 'SENDING': raise WorkspaceError('WORKSPACE_CONFLICT')
            self.executions[payload['claim']] = record[:4] + [payload['state'], at, payload.get('detail','')]
            return {'ok': True}
        if action == 'audit_batch':
            ids = [e['event_id'] for e in payload['events']]
            self.events.update(ids)
            return {'ok': True, 'event_ids': ids}
        if action == 'publish_release':
            if actor != 'mohammadreza.vahab': raise WorkspaceError('WORKSPACE_CONFLICT')
            self.releases.append(dict(payload, published_at=at))
            return {'ok': True}
        raise WorkspaceError('SHEET_SCHEMA_ERROR')

    def validate(self, action, payload):
        if action == 'source_batch':
            cases = payload.get('cases')
            if not isinstance(cases, list) or not 1 <= len(cases) <= 50: raise WorkspaceError('SHEET_SCHEMA_ERROR')
            seen = set()
            for case in cases:
                smr = case.get('smr', '')
                if not re.fullmatch(r'SMR-\d+', smr) or smr in seen: raise WorkspaceError('SHEET_SCHEMA_ERROR')
                seen.add(smr)
                if (case.get('channel') not in ('online','both') or not isinstance(case.get('active'), bool)
                    or not re.fullmatch('[a-f0-9]{64}', case.get('fingerprint',''))
                    or timestamp(case.get('source_loaded_at')) == float('-inf')): raise WorkspaceError('SHEET_SCHEMA_ERROR')
        elif action == 'decide':
            if payload.get('team') not in ('online','instore') or payload.get('decision') not in workflow.ACTIONS or not str(payload.get('note','')).strip():
                raise WorkspaceError('SHEET_SCHEMA_ERROR')
            if payload['decision'] in ('EDIT','CANCEL') and payload.get('reason') not in sheets.nbo_labels().get(payload['decision'].lower(), {}):
                raise WorkspaceError('SHEET_SCHEMA_ERROR')
        elif action in ('claim_execution','finish_execution'):
            if not re.fullmatch('[a-f0-9]{32}', payload.get('claim','')): raise WorkspaceError('SHEET_SCHEMA_ERROR')
            if action == 'finish_execution' and payload.get('state') not in ('SENT','UNCERTAIN','BLOCKED'): raise WorkspaceError('SHEET_SCHEMA_ERROR')
        elif action == 'audit_batch':
            if not isinstance(payload.get('events'), list) or len(payload['events']) > 100: raise WorkspaceError('SHEET_SCHEMA_ERROR')
            for event in payload['events']:
                if not re.fullmatch('[a-f0-9]{32}', event.get('event_id', '')): raise WorkspaceError('SHEET_SCHEMA_ERROR')
        elif action == 'publish_release':
            from .updates import pick_latest
            if not pick_latest([payload]): raise WorkspaceError('SHEET_SCHEMA_ERROR')
        else: raise WorkspaceError('SHEET_SCHEMA_ERROR')

    def mutate(self, action, payload, actor, machine, version):
        operation = payload.pop('operation_id', None)
        if action == 'audit_batch' and not operation:
            operation = hashlib.sha256(compact(payload).encode()).hexdigest()[:32]
        if not re.fullmatch('[a-f0-9]{32}', operation or ''): raise WorkspaceError('SHEET_SCHEMA_ERROR')
        self.validate(action, payload)
        # Hash client payload before adding expected revisions, so exact retries can replay.
        digest = hashlib.sha256(compact([action, actor, payload]).encode()).hexdigest()
        self.refresh()
        if operation in self.ops:
            result = self.receipt(operation, digest)
            self.finalize(operation)
            return result
        prepared = copy.deepcopy(payload)
        if action == 'source_batch':
            prepared['expected'] = {c['smr']: self.cases.get(c['smr'], {}).get('revision', 0) for c in prepared['cases']}
        # Validate against the latest authoritative state before writing; rollback the local preview.
        state = (copy.deepcopy(self.cases), copy.deepcopy(self.executions), set(self.events), list(self.releases))
        at = stamp()
        try: preview = self.apply(action, prepared, actor, at)
        finally: self.cases, self.executions, self.events, self.releases = state
        encoded = compact({'payload': payload, 'context': {k: v for k, v in prepared.items() if k not in payload}})
        if len(encoded) > 45000: raise WorkspaceError('SHEET_SCHEMA_ERROR', 'بسته بیش از حد بزرگ است؛ بسته کوچک‌تر لازم است.')
        mutation = [operation,digest,action,actor,machine,version,at,encoded,'PENDING','']
        audit = [operation,at,actor,machine,version,action,payload.get('smr',''),payload.get('revision',''),'PENDING','','']
        audits = [audit]
        if action == 'audit_batch':
            for event in payload['events']:
                if event['event_id'] not in self.events:
                    audits.append([event['event_id'], event.get('at', at), event.get('actor') or actor,
                        machine, version, event.get('kind', 'AUDIT'), event.get('smr', ''),
                        event.get('revision', ''), event.get('result', 'OK'), event.get('error_code', ''),
                        event.get('detail', '')])
        requests = [{'appendCells': {'sheetId': self.props[name]['sheetId'], 'rows': [row(v) for v in values], 'fields': 'userEnteredValue'}}
                    for name, values in ((JOURNAL, [mutation]), (AUDIT, audits))]
        error = None
        try: self.client.request('POST', ':batchUpdate', json={'requests': requests})
        except WorkspaceError as exc: error = exc
        # The read includes all earlier append rows, even if another client appended meanwhile.
        self.refresh()
        if operation not in self.ops:
            if error: raise error
            raise WorkspaceError('GOOGLE_API_ERROR')
        self.finalize(operation)
        return self.receipt(operation, digest)

    def finalize(self, operation):
        """Publish the deterministic result, never modify the immutable mutation columns."""
        index = self.operation_rows[operation]
        result = self.ops[operation][1]
        # Event rows need not have the same index as journal operations.
        found = self.ranges([f"'{AUDIT}'!A{self.audit_cursor + 2}:A"])[0]
        for values in found:
            if not values: raise WorkspaceError('SHEET_SCHEMA_ERROR')
            self.audit_rows.setdefault(values[0], self.audit_cursor + 2)
            self.audit_cursor += 1
        audit_index = self.audit_rows.get(operation)
        if audit_index is None: raise WorkspaceError('SHEET_SCHEMA_ERROR')
        state = 'OK' if result.get('ok') else 'CONFLICT'
        code = result.get('error_code', '')
        self.client.request('POST', ':batchUpdate', json={'requests': [
            {'updateCells': {'start': {'sheetId': self.props[JOURNAL]['sheetId'], 'rowIndex': index-1, 'columnIndex': 8},
                             'rows': [row([compact(result), code])], 'fields': 'userEnteredValue'}},
            {'updateCells': {'start': {'sheetId': self.props[AUDIT]['sheetId'], 'rowIndex': audit_index-1, 'columnIndex': 8},
                             'rows': [row([state, code, ''])], 'fields': 'userEnteredValue'}}]})

    def receipt(self, operation, digest):
        saved_hash, result = self.ops[operation]
        if digest != saved_hash: raise WorkspaceError('WORKSPACE_CONFLICT')
        if not result.get('ok'): raise WorkspaceError(result.get('error_code', 'WORKSPACE_CONFLICT'))
        return copy.deepcopy(result)

    def call(self, action, actor, machine, version, **payload):
        with self.lock:
            if action == 'health': return self.health(force=payload.get('force', False))
            if action == 'whoami':
                self.health(); return {'ok': True, 'version': 6, 'user': {'username': actor, 'role': 'online'}}
            if action == 'read':
                self.refresh()
                if payload.get('generation') is not None and payload['generation'] != self.cursor:
                    return {'ok': True, 'restart': True, 'generation': self.cursor}
                if payload.get('known_generation') == self.cursor:
                    return {'ok': True, 'unchanged': True, 'generation': self.cursor}
                offset = max(0, int(payload.get('offset', 0)))
                cases = list(self.cases.values()); following = offset + 200
                return {'ok': True, 'generation': self.cursor, 'cases': copy.deepcopy(cases[offset:following]),
                        'next': following if following < len(cases) else None,
                        'executions': copy.deepcopy(list(self.executions.values())) if offset == 0 else []}
            if action == 'release':
                self.refresh(); existing = []
                if 'Updates' in self.props:
                    rows = self.ranges(["'Updates'!A2:E"])[0]
                    existing = [dict(zip(('version','url','sha256','notes','published_at'), r)) for r in rows if len(r) >= 3]
                return {'ok': True, 'releases': existing + copy.deepcopy(self.releases)}
            return self.mutate(action, dict(payload), actor, machine, version)


_backends = {}
_backend_lock = threading.Lock()


def get_backend(cfg):
    path = google_credentials.key_path()
    try: key_version = path.stat().st_mtime_ns
    except OSError: raise WorkspaceError('GOOGLE_CREDENTIALS_MISSING') from None
    key = (cfg['own_sheet_id'], str(path), key_version)
    with _backend_lock:
        if key not in _backends:
            _backends[key] = Backend(cfg['own_sheet_id'])
        return _backends[key]
