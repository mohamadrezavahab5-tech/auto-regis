"""Direct Google Sheets workspace; the running CRM session supplies the audit actor."""
import hashlib
import json
import re
import secrets
import uuid

from . import google_credentials, sheets
from .paths import user_dir, scripts_dir


ADMIN = 'mohammadreza.vahab'
ROLES = {'admin':'مدیر', 'online':'تیم Online', 'instore':'تیم Instore', 'viewer':'فقط مشاهده'}
USER_HEAD = ['Username','Role','Enabled','Token hash','Updated at']
STATE_HEAD = ['SMR','Revision','Case JSON']
OPS_HEAD = ['Operation ID','User','Result JSON']


def allowed_teams(profile):
    from . import crm_sync
    if crm_sync.authenticated_identity():
        return {'online', 'instore'}
    try:
        if username(profile.get('username')) == ADMIN:
            return {'online', 'instore'}
    except ValueError:
        pass
    role = profile.get('workspace_role')
    if role == 'admin':
        return {'online', 'instore'}
    return {role} if role in ('online', 'instore') else set()


def username(value):
    name = str(value or '').strip().lower().split('\\')[-1].split('@')[0]
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{1,79}',name):
        raise ValueError('نام کاربری معتبر نیست')
    return name


def token_path(): return user_dir()/'workspace-access.dpapi'


def save_access(user, token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', token): raise ValueError('کد دسترسی معتبر نیست')
    body=json.dumps(dict(username=username(user),token=token)).encode()
    token_path().write_bytes(google_credentials._dpapi(body))


def access():
    try: return json.loads(google_credentials._dpapi(token_path().read_bytes(),decrypt=True))
    except (OSError,ValueError,RuntimeError): return None


def hash_token(token): return hashlib.sha256(token.encode()).hexdigest()


def users(google):
    return [dict(username=r[0],role=r[1],enabled=r[2] is True) for r in google.read('Users',USER_HEAD) if r[0]]


def provision(google, actor, user, role, enabled=True, rotate=False):
    """Admin-only direct bootstrap; only the admin PC possesses the encrypted SA key."""
    from .store import now
    if username(actor)!=ADMIN: raise PermissionError('فقط مدیر مجاز است')
    user=username(user)
    if role not in ROLES or (role=='admin' and user!=ADMIN): raise ValueError('نقش معتبر نیست')
    if user==ADMIN and (role!='admin' or not enabled): raise ValueError('حساب مدیر اصلی قابل غیرفعال‌کردن نیست')
    rows=google.read('Users',USER_HEAD)
    found=[i for i,r in enumerate(rows) if r[0]==user]
    if len(found)>1: raise ValueError('کاربر تکراری در Users')
    index=found[0] if found else len(rows)
    token=secrets.token_urlsafe(32) if not found or rotate else None
    digest=hash_token(token) if token else rows[index][3]
    google.batch([google.update('Users',index+1,[user,role,bool(enabled),digest,now()])])
    return token


def call(action, cfg=None, **payload):
    from . import crm_sync
    from .version import __version__
    cfg=cfg or sheets.load()
    identity = crm_sync.authenticated_identity()
    if not identity:
        raise sheets.SheetError('ابتدا با حساب CRM وارد شو', 'AUTH_ERROR')
    from .workspace_google import get_backend
    return get_backend(cfg).call(action, username(identity['username']), machine_id(), __version__, **payload)



def machine_id():
    path = user_dir() / 'machine-id.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))['id']
    except (OSError, ValueError, KeyError):
        from .atomic_file import save_json
        value = uuid.uuid4().hex
        save_json(path, {'id': value})
        return value


def health(cfg=None):
    result = call('health', cfg)
    if result.get('server_version') != 6 or result.get('schema_version') != 6 or result.get('sheet_ok') is not True:
        raise sheets.SheetError('نسخه سرویس مشترک با برنامه سازگار نیست', 'SHEET_SCHEMA_ERROR')
    return result


def publish_release(version, url, sha256, notes, cfg=None):
    return call('publish_release', cfg, operation_id=uuid.uuid4().hex,
                version=version, url=url, sha256=sha256, notes=notes)


def safe_event(event):
    """Allow only audit metadata; never ship arbitrary diagnostic dictionaries."""
    allowed = ('event_id', 'at', 'kind', 'smr', 'revision', 'result', 'error_code', 'actor')
    out = {k: event[k] for k in allowed if k in event}
    detail = event.get('detail') or {}
    if isinstance(detail, dict):
        detail = {k: v for k, v in detail.items() if k in ('status', 'state', 'action', 'team', 'decision', 'mode', 'error_code', 'approvals_reset')}
        out['detail'] = json.dumps(detail, ensure_ascii=False)
    else:
        out['detail'] = ''
    return out


def audit(db, kind, error_code=''):
    from . import crm_sync, store
    identity = crm_sync.authenticated_identity()
    if not identity:
        return
    event = dict(event_id=uuid.uuid4().hex, at=store.now(), smr='', kind=kind,
                 actor=username(identity['username']), error_code=error_code, detail={})
    with db:
        db.execute('INSERT INTO workflow_events(event_id,smr,at,body) VALUES(?,?,?,?)',
                   (event['event_id'], '', event['at'], json.dumps(event)))




def cache_cases(db, cases):
    """Remote state is authoritative in workspace mode, never re-upload approvals."""
    with db:
        for case in cases:
            db.execute('''INSERT INTO workflow_cases(smr,body,revision,synced_revision) VALUES(?,?,?,?)
             ON CONFLICT(smr) DO UPDATE SET body=excluded.body,revision=excluded.revision,synced_revision=excluded.revision''',
             (case['smr'],json.dumps(case,ensure_ascii=False),case['revision'],case['revision']))


def decide(smr, team, action, note, revision, reason='', cfg=None):
    return call('decide',cfg,operation_id=uuid.uuid4().hex,smr=smr,team=team,decision=action,
                note=note,revision=revision,reason=reason)

def ensure_shared(db):
    db.execute('''CREATE TABLE IF NOT EXISTS workspace_execution_outbox (
      claim TEXT PRIMARY KEY, body TEXT NOT NULL, pending INTEGER NOT NULL DEFAULT 0)''')
    db.commit()


def recover_execution(db):
    """Only at process startup: a previous owner of a claim cannot still be running here."""
    ensure_shared(db)
    with db:
        db.execute('UPDATE workspace_execution_outbox SET pending=1')
        for (raw,) in db.execute('SELECT body FROM workspace_execution_outbox').fetchall():
            result = json.loads(raw)
            if result['state'] == 'UNCERTAIN':
                db.execute("UPDATE nbo_execution SET state='UNCERTAIN' WHERE smr=? AND revision=? AND state='SENDING'",
                           (result['smr'], result['revision']))


def queue_execution_result(db, payload, pending=True):
    ensure_shared(db)
    payload = dict(payload)
    from . import crm_sync
    identity = crm_sync.authenticated_identity()
    if identity:
        payload.setdefault('crm_actor', username(identity['username']))
    payload.setdefault('operation_id', hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32])
    with db:
        db.execute('INSERT OR REPLACE INTO workspace_execution_outbox VALUES(?,?,?)',
                   (payload['claim'], json.dumps(payload, ensure_ascii=False), int(pending)))


def flush_execution(db, cfg=None):
    ensure_shared(db)
    for claim, raw in db.execute('SELECT claim,body FROM workspace_execution_outbox WHERE pending=1').fetchall():
        from . import crm_sync
        payload = json.loads(raw)
        owner = payload.pop('crm_actor', None)
        identity = crm_sync.authenticated_identity()
        if owner and (not identity or username(identity['username']) != owner):
            continue  # A different login cannot finish the previous person's execution.
        call('finish_execution', cfg, **payload)
        with db:
            db.execute('DELETE FROM workspace_execution_outbox WHERE claim=? AND body=? AND pending=1', (claim, raw))


def prepare_execution(db, case, cfg, automatic=False):
    """Reserve locally BEFORE acquiring the global claim; never submit on an ambiguous response."""
    from . import execution
    problem = execution.eligibility(case)
    if problem:
        raise ValueError(problem)
    if automatic and case.get('channel') != 'online':
        raise ValueError('ثبت خودکار فقط برای Online است')
    execution.claim(db, case)
    claim = uuid.uuid4().hex
    payload = dict(smr=case['smr'], revision=case['revision'], claim=claim,
                   state='UNCERTAIN', detail='برنامه هنگام ثبت بسته شد؛ نتیجه باید در NBO بررسی شود')
    # Durable default before the remote call and any NBO interaction. Normal sync skips active claims.
    queue_execution_result(db, payload, pending=False)
    try:
        result = call('claim_execution', cfg, operation_id=claim, claim=claim,
                      smr=case['smr'], revision=case['revision'], automatic=automatic)
        if result.get('claim') != claim:
            raise sheets.SheetError('رسید رزرو مشترک معتبر نیست؛ هیچ ثبتی انجام نشد')
    except Exception:
        payload.update(state='BLOCKED', detail='رزرو مشترک تأیید نشد؛ برنامه هیچ عملیاتی در NBO شروع نکرد')
        queue_execution_result(db, payload)
        execution.record(db, case, 'BLOCKED', payload['detail'])
        # Keep the receipt for the next sync; a lost claim response must not trigger a second send.
        raise
    return dict(claim=claim, case=result.get('case', case))


def finish_execution(db, case, claim, state, detail):
    queue_execution_result(db, dict(smr=case['smr'], revision=case['revision'], claim=claim,
                                    state=state, detail=detail))


def cache_snapshot(db, cases, ledger, generation):
    """Install a complete cloud generation; local unpublished changes survive a concurrent download."""
    from . import execution
    execution.ensure(db, recover=False)
    ensure_shared(db)
    with db:
        for case in cases:
            if not isinstance(case.get('revision'), int) or not case.get('smr'):
                raise ValueError('پرونده مشترک معتبر نیست')
            db.execute('''INSERT INTO workflow_cases(smr,body,revision,synced_revision) VALUES(?,?,?,?)
              ON CONFLICT(smr) DO UPDATE SET body=excluded.body,revision=excluded.revision,synced_revision=excluded.revision
              WHERE workflow_cases.revision<=workflow_cases.synced_revision''',
              (case['smr'], json.dumps(case, ensure_ascii=False), case['revision'], case['revision']))
        for row in ledger:
            if len(row) != 7 or row[4] not in execution.LABELS:
                raise ValueError('سابقه مشترک معتبر نیست')
            smr, revision, _claim, _actor, state, at, detail = row
            if db.execute('SELECT 1 FROM workspace_execution_outbox WHERE claim=?', (_claim,)).fetchone():
                continue
            db.execute('''INSERT INTO nbo_execution VALUES(?,?,?,?,?) ON CONFLICT(smr,revision)
              DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,detail=excluded.detail
              WHERE nbo_execution.updated_at<=excluded.updated_at''', (smr, revision, state, at, detail))
        db.execute("INSERT OR REPLACE INTO workflow_meta VALUES('shared_generation',?)", (str(generation),))


def shared_queues(db):
    from . import workflow
    online, both = [], []
    for c in workflow.cases(db):
        if not c.get('active'):
            continue
        row = dict(smr=c['smr'], site=c.get('site', ''), category=c.get('category', ''),
                   status=c.get('source_status', ''), created_at=c.get('source_created_at', ''),
                   has_online='true', has_instore='true' if c['channel'] == 'both' else 'false',
                   account_holder=c.get('account_holder', ''), owner_name=c.get('owner_name', ''),
                   owner_family=c.get('owner_family', ''))
        (both if c['channel'] == 'both' else online).append(row)
    return online, [], both


def shared_reviews(db):
    from . import workflow
    return {c['smr']: (c['suggestion']['action'], c['suggestion'].get('reason_codes', []),
                      c['suggestion'].get('decided_at', ''))
            for c in workflow.cases(db) if c.get('suggestion')}


def server_code(cfg=None):
    """Generate complete, configured Apps Script code for deployment.
    
    Reads scripts/workspace-server.gs, substitutes APP_SHARED_KEY and EXPECTED_SHEET_ID,
    and returns ready-to-paste code for Google Apps Script editor.
    """
    if not cfg:
        # Load from bundled config, not user settings (which may not have app_key yet)
        from .paths import config_dir
        try:
            cfg = json.loads((config_dir() / 'workspace.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            cfg = {}
    
    own_sheet = cfg.get('own_sheet_id', '')
    app_key = cfg.get('app_key', '')
    
    if not app_key:
        raise ValueError('app_key not configured in config/workspace.json')
    
    # Read the base workspace server code
    server_path = scripts_dir() / 'workspace-server.gs'
    with open(server_path, 'r', encoding='utf-8') as f:
        code = f.read()
    
    # Substitute the configuration placeholders using regex to match various formats
    code = re.sub(r"const APP_SHARED_KEY = '[^']*'", 
                  f"const APP_SHARED_KEY = '{app_key}'", code)
    
    # Add EXPECTED_SHEET_ID if not present
    if 'EXPECTED_SHEET_ID' not in code:
        lines = code.split('\n')
        for i, line in enumerate(lines):
            if 'const APP_SHARED_KEY' in line:
                lines.insert(i + 1, f"const EXPECTED_SHEET_ID = '{own_sheet}';")
                code = '\n'.join(lines)
                break
    else:
        code = re.sub(r"const EXPECTED_SHEET_ID = '[^']*'",
                      f"const EXPECTED_SHEET_ID = '{own_sheet}'", code)
    
    return code
