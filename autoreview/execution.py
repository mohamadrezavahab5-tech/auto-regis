"""Execution ledger: what the app did in NBO, per request and case revision. An uncertain write is never repeated by itself.

Owner 2026-10-01: decisions are applied in NBO's own screen the way the team's old scripts did (app/nbo_actor.py) - by hand
(one request, the person confirms) or automatically (live mode, owner only). What NBO then shows in its next export is
recorded too (APPROVED_IN_NBO), so 'sent' and 'really approved' are never confused."""
from datetime import datetime, timezone

from . import store, workflow
from .workspace import ADMIN, username

LABELS = {'PREVIEW': 'آماده؛ منتظر تأیید در NBO', 'SENDING': 'در حال ارسال',
          'SENT': 'در NBO ثبت شد؛ منتظر دیده‌شدن در خروجی بعدی', 'REHEARSED': 'تمرین موفق (چیزی ثبت نشد)',
          'APPROVED_IN_NBO': 'در NBO تأیید شد',
          'VERIFIED': 'تأیید در NBO بررسی شد', 'BLOCKED': 'متوقف؛ نیازمند بررسی',
          'UNCERTAIN': 'نتیجه نامشخص؛ تکرار خودکار ممنوع'}


def ensure(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS nbo_execution (
      smr TEXT NOT NULL, revision INTEGER NOT NULL, state TEXT NOT NULL,
      updated_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
      PRIMARY KEY(smr, revision));
      CREATE TABLE IF NOT EXISTS execution_mode_events (
      at TEXT NOT NULL, actor TEXT NOT NULL, live INTEGER NOT NULL);''')
    with db:
        db.execute("UPDATE nbo_execution SET state='UNCERTAIN', detail=? WHERE state='SENDING'",
                   ('برنامه هنگام ارسال بسته شده؛ ابتدا وضعیت NBO بررسی شود',))


MAX_SOURCE_AGE = 1800        # the live NBO page is re-read before every click; the export only has to be recent


def target(case):
    """-> (NBO action, reason code) the case's verdicts ask for, or None. Approve only when every needed team approved;
    edit / cancel carry the NBO reason the deciding verdict gave."""
    st = workflow.state(case)
    if st == 'READY':
        return 'APPROVE', ''
    if st in ('EDIT', 'CANCEL'):
        for team in ('online', 'instore'):
            v = case.get(team) or {}
            if v.get('action') == st and v.get('reason'):
                return st, v['reason']
    return None


def eligibility(case, now=None):
    if case.get('channel') not in ('online', 'both'):
        return 'مسیر درخواست مشخص نیست'
    if target(case) is None:
        return 'تأیید تیم‌های لازم کامل نیست' if workflow.state(case) in ('WAIT_ONLINE', 'WAIT_INSTORE', 'MANUAL', 'CONFLICT') \
            else 'این پرونده کاری در NBO ندارد'
    if case.get('source_status') not in ('PENDING', 'COMMERCIAL_IN_PROGRESS'):
        return 'وضعیت مرجع برای تغییر مجاز نیست'
    try:
        loaded = datetime.fromisoformat(case['source_loaded_at'].replace('Z', '+00:00'))
        if loaded.tzinfo is None:
            return 'زمان مرجع منطقه زمانی ندارد'
        age = ((now or datetime.now(timezone.utc)) - loaded).total_seconds()
        if age < -30 or age > MAX_SOURCE_AGE:
            return 'خروجی NBO باید در نیم ساعت اخیر گرفته شده باشد'
    except (KeyError, ValueError, TypeError):
        return 'زمان دریافت مرجع معتبر نیست'
    return ''


def records(db, limit=None):
    sql = 'SELECT smr,revision,state,updated_at,detail FROM nbo_execution ORDER BY updated_at DESC'
    rows = db.execute(sql + (' LIMIT ?' if limit else ''), (limit,) if limit else ()).fetchall()
    return [dict(zip(('smr', 'revision', 'state', 'updated_at', 'detail'), row), label=LABELS.get(row[2], row[2])) for row in rows]


def note_nbo_outcomes(db):
    """NBO now shows approved (whoever pressed the button): record it once per request, and say plainly when it happened
    without the team verdicts the process needs. -> number recorded."""
    done = {r[0] for r in db.execute("SELECT smr FROM nbo_execution WHERE state IN ('VERIFIED','APPROVED_IN_NBO')")}
    n = 0
    for case in workflow.cases(db):
        if case.get('state') != 'DONE_APPROVED' or case['smr'] in done:
            continue
        online = (case.get('online') or {}).get('action') == 'APPROVE'
        instore = case['channel'] != 'both' or (case.get('instore') or {}).get('action') == 'APPROVE'
        record(db, case, 'APPROVED_IN_NBO', 'پس از تأیید تیم‌های لازم' if online and instore
               else 'بدون تأیید کامل گردش کار؛ بررسی شود')
        n += 1
    return n


def record(db, case, state, detail=''):
    if state not in LABELS:
        raise ValueError('Unknown execution state')
    with db:
        db.execute('''INSERT INTO nbo_execution VALUES(?,?,?,?,?) ON CONFLICT(smr,revision)
          DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,detail=excluded.detail''',
                   (case['smr'], case['revision'], state, store.now(), detail))
        store.log(db, case['smr'], 'NBO_' + state, {'revision': case['revision'], 'detail': detail})


def claim(db, case, require_synced=True):
    """Only one process can claim a revision; any previous ambiguous send blocks the SMR."""
    db.execute('BEGIN IMMEDIATE')
    try:
        current = workflow.get(db, case['smr'])
        if not current or current['revision'] != case['revision'] or eligibility(current):
            raise ValueError('پرونده تغییر کرده یا آماده نیست')
        if db.execute("SELECT 1 FROM nbo_execution WHERE smr=? AND (state IN ('SENDING','UNCERTAIN') OR "
                      "(revision=? AND state IN ('VERIFIED','SENT')))", (case['smr'], case['revision'])).fetchone():
            raise ValueError('این درخواست قبلاً ارسال شده یا نتیجه نامشخص دارد')
        if require_synced:
            row = db.execute('SELECT synced_revision FROM workflow_cases WHERE smr=?', (case['smr'],)).fetchone()
            if not row or row[0] < case['revision']:
                raise ValueError('تأییدها هنوز با شیت همگام نشده‌اند')
        db.execute('''INSERT INTO nbo_execution VALUES(?,?,?,?,?) ON CONFLICT(smr,revision)
          DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,detail=excluded.detail''',
                   (case['smr'], case['revision'], 'SENDING', store.now(), ''))
        store.log(db, case['smr'], 'NBO_SENDING', {'revision': case['revision']})
        db.commit()
    except Exception:
        db.rollback()
        raise


class Mode:
    """Live is armed by the authenticated owner for this app session only."""
    def __init__(self):
        self.live = False

    def set(self, live, actor, db, readiness=''):
        if username(actor) != ADMIN:
            raise PermissionError('تغییر حالت اجرا فقط برای مدیر مجاز است')
        if live and readiness:
            raise ValueError(readiness)
        with db:
            db.execute('INSERT INTO execution_mode_events VALUES(?,?,?)', (store.now(), actor, int(bool(live))))
        self.live = bool(live)
