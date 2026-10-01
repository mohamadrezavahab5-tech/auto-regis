"""Approval execution ledger. An uncertain write is never automatically repeated."""
from datetime import datetime, timezone

from . import store, workflow
from .workspace import ADMIN, username

LABELS = {'PREVIEW': 'آزمایشی؛ ارسال نشده', 'SENDING': 'در حال ارسال',
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


def eligibility(case, now=None):
    if case.get('channel') not in ('online', 'both'):
        return 'مسیر درخواست مشخص نیست'
    if workflow.state(case) != 'READY':
        return 'تأیید تیم‌های لازم کامل نیست'
    if case.get('source_status') not in ('PENDING', 'COMMERCIAL_IN_PROGRESS'):
        return 'وضعیت مرجع برای تأیید مجاز نیست'
    try:
        loaded = datetime.fromisoformat(case['source_loaded_at'].replace('Z', '+00:00'))
        if loaded.tzinfo is None:
            return 'زمان مرجع منطقه زمانی ندارد'
        age = ((now or datetime.now(timezone.utc)) - loaded).total_seconds()
        if age < -30 or age > 300:
            return 'مرجع NBO باید در پنج دقیقه اخیر به‌روز شده باشد'
    except (KeyError, ValueError, TypeError):
        return 'زمان دریافت مرجع معتبر نیست'
    return ''


def records(db):
    return [dict(zip(('smr', 'revision', 'state', 'updated_at', 'detail'), row))
            for row in db.execute('SELECT smr,revision,state,updated_at,detail FROM nbo_execution ORDER BY updated_at DESC')]


def record(db, case, state, detail=''):
    if state not in LABELS:
        raise ValueError('Unknown execution state')
    with db:
        db.execute('''INSERT INTO nbo_execution VALUES(?,?,?,?,?) ON CONFLICT(smr,revision)
          DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at,detail=excluded.detail''',
                   (case['smr'], case['revision'], state, store.now(), detail))
        store.log(db, case['smr'], 'NBO_' + state, {'revision': case['revision'], 'detail': detail})


def claim(db, case):
    """Only one process can claim a revision; any previous ambiguous send blocks the SMR."""
    db.execute('BEGIN IMMEDIATE')
    try:
        current = workflow.get(db, case['smr'])
        if not current or current['revision'] != case['revision'] or eligibility(current):
            raise ValueError('پرونده تغییر کرده یا آماده نیست')
        if db.execute("SELECT 1 FROM nbo_execution WHERE smr=? AND state IN ('SENDING','UNCERTAIN','VERIFIED')",
                      (case['smr'],)).fetchone():
            raise ValueError('این درخواست قبلاً ارسال شده یا نتیجه نامشخص دارد')
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
