"""Per-user access tokens for the Google-hosted workspace; clients never share an SA key."""
import hashlib
import json
import re
import secrets
import uuid

from . import google_credentials, sheets
from .paths import user_dir, scripts_dir
from .google_sheet import Client

ADMIN = 'mohammadreza.vahab'
ROLES = {'admin':'مدیر', 'online':'تیم Online', 'instore':'تیم Instore', 'viewer':'فقط مشاهده'}
USER_HEAD = ['Username','Role','Enabled','Token hash','Updated at']
STATE_HEAD = ['SMR','Revision','Case JSON']
OPS_HEAD = ['Operation ID','User','Result JSON']


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
    cfg=cfg or sheets.load()
    auth=access()
    if not auth: raise sheets.SheetError('کد دسترسی شخصی را در اتصال‌ها وارد کن')
    return sheets._post(cfg.get('workspace_url',''),dict(action=action,**auth,**payload))


def server_code(cfg=None):
    cfg=cfg or sheets.load()
    base=(scripts_dir()/'own-sheet.gs').read_text(encoding='utf-8')
    # Reuse formatting/schema helpers; remove the old shared-secret dispatcher.
    a=base.index('function doPost('); b=base.index('\nfunction sync(',a)
    base=base[:a]+base[b:]
    base=base.replace("const SECRET = '__SECRET__';",'')
    base=base.replace('__OWN_ID__',cfg['own_sheet_id'])
    return base+'\n'+(scripts_dir()/'workspace-server.gs').read_text(encoding='utf-8')


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
