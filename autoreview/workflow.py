"""Durable human decisions, separate from recommendations and actual NBO status.

No external actions. Own-sheet sync is an outbox; acknowledgements cannot discard
newer revisions. A changed source invalidates old human approvals.
"""
import hashlib
import json
import uuid

from . import store

STATES = {
    'WAIT_ONLINE': 'منتظر نظر Online', 'WAIT_INSTORE': 'منتظر نظر Instore',
    'MANUAL': 'نیازمند بررسی دستی', 'EDIT': 'نیاز به اصلاح؛ اعمال نشده',
    'CANCEL': 'پیشنهاد لغو؛ اعمال نشده', 'CONFLICT': 'اختلاف نظر دو تیم',
    'READY': 'آماده تأیید در NBO؛ اعمال نشده', 'OUT_OF_SCOPE': 'خارج از صف فعال',
}
FIELDS = ('site', 'category', 'ownership', 'has_online', 'has_instore',
          'account_holder', 'owner_name', 'owner_family', 'status')


def ensure(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS workflow_cases (
      smr TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL,
      synced_revision INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS workflow_events (
      event_id TEXT PRIMARY KEY, smr TEXT NOT NULL, at TEXT NOT NULL,
      body TEXT NOT NULL, synced INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS workflow_commands (
      command_id TEXT PRIMARY KEY, response TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS workflow_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    with db:
        db.execute('INSERT OR IGNORE INTO workflow_meta VALUES (?, ?)', ('device_id', uuid.uuid4().hex))


def get(db, smr):
    row = db.execute('SELECT body FROM workflow_cases WHERE smr=?', (smr,)).fetchone()
    return json.loads(row[0]) if row else None


def cases(db):
    return [json.loads(r[0]) for r in db.execute('SELECT body FROM workflow_cases ORDER BY smr')]


def state(case):
    if not case.get('active'):
        return 'OUT_OF_SCOPE'
    online = (case.get('online') or {}).get('action')
    instore = (case.get('instore') or {}).get('action') if case['channel'] == 'both' else None
    decisions = [v for v in (online, instore) if v]
    if 'APPROVE' in decisions and any(v in ('EDIT', 'CANCEL') for v in decisions):
        return 'CONFLICT'
    if 'CANCEL' in decisions:
        return 'CANCEL'
    if 'EDIT' in decisions:
        return 'EDIT'
    if 'MANUAL' in decisions:
        return 'MANUAL'
    if online != 'APPROVE':
        return 'MANUAL' if (case.get('suggestion') or {}).get('action') == 'MANUAL' else 'WAIT_ONLINE'
    if case['channel'] == 'both' and instore != 'APPROVE':
        return 'WAIT_INSTORE'
    return 'READY'


def _save(db, case, kind, actor='', detail=None):
    case['revision'] = int(case.get('revision', 0)) + 1
    case['updated_at'] = store.now()
    case['state'] = state(case)
    case['state_fa'] = STATES[case['state']]
    body = json.dumps(case, ensure_ascii=False)
    db.execute('''INSERT INTO workflow_cases(smr,body,revision) VALUES(?,?,?)
      ON CONFLICT(smr) DO UPDATE SET body=excluded.body,revision=excluded.revision''',
               (case['smr'], body, case['revision']))
    event = dict(event_id=uuid.uuid4().hex, smr=case['smr'], at=case['updated_at'],
                 kind=kind, actor=actor, detail=detail or {}, revision=case['revision'])
    db.execute('INSERT INTO workflow_events(event_id,smr,at,body) VALUES(?,?,?,?)',
               (event['event_id'], case['smr'], event['at'], json.dumps(event, ensure_ascii=False)))
    store.log(db, case['smr'], kind, dict(user=actor, revision=case['revision'], **(detail or {})))


def refresh(db, rows, eligible_ids, source_loaded_at=None):
    """Called with a COMPLETE local NBO reference, never a partial batch."""
    seen = set()
    with db:
        for row in rows:
            smr = row['smr']
            seen.add(smr)
            old = get(db, smr)
            active = smr in eligible_ids
            if not active and old is None:
                continue
            fingerprint = hashlib.sha256(json.dumps({k: row.get(k) for k in FIELDS},
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            channel = 'both' if str(row.get('has_instore')).lower() == 'true' else 'online'
            if old and old['fingerprint'] == fingerprint and old['active'] == active:
                if source_loaded_at and old.get('source_loaded_at') != source_loaded_at:
                    old['source_loaded_at'] = source_loaded_at
                    db.execute('UPDATE workflow_cases SET body=? WHERE smr=?', (json.dumps(old,ensure_ascii=False),smr))
                continue
            case = dict(smr=smr, site=row.get('site', ''), category=row.get('category', ''),
                brand=row.get('brand_fa', ''), source_status=row.get('status', ''), channel=channel,
                active=active, fingerprint=fingerprint, online=None, instore=None, suggestion=None,
                revision=old['revision'] if old else 0, source_loaded_at=source_loaded_at or store.now())
            _save(db, case, 'WORKFLOW_SOURCE_CHANGED' if old else 'WORKFLOW_IMPORTED',
                  detail={'approvals_reset': bool(old)})
        for old in cases(db):
            if old['smr'] not in seen and old['active']:
                old.update(active=False, online=None, instore=None)
                _save(db, old, 'WORKFLOW_SOURCE_REMOVED')


def suggest(db, smr, result):
    with db:
        case = get(db, smr)
        if not case or not case['active']:
            return
        suggestion = {k: result.get(k) for k in ('action', 'reason_codes', 'notes', 'decided_at')}
        if case.get('suggestion') == suggestion:
            return
        old = case.get('suggestion') or {}
        if any(old.get(k) != suggestion.get(k) for k in ('action', 'reason_codes')):
            case['online'] = None
        case['suggestion'] = suggestion
        _save(db, case, 'WORKFLOW_SUGGESTED', detail={'action': suggestion['action']})


def decide(db, smr, team, action, actor, note, expected_revision, reason='', labels=None):
    if team not in ('online', 'instore') or action not in ('APPROVE', 'EDIT', 'CANCEL', 'MANUAL', 'REOPEN'):
        raise ValueError('تیم یا تصمیم معتبر نیست')
    if not actor or not actor.strip() or not note or not note.strip():
        raise ValueError('نام بررسی‌کننده و توضیح/مرجع بررسی الزامی است')
    with db:
        # Take the write lock before reading: concurrent decisions cannot overwrite one another.
        db.execute('UPDATE workflow_cases SET revision=revision WHERE smr=?', (smr,))
        case = get(db, smr)
        if not case or not case['active']:
            raise ValueError('درخواست در صف فعال نیست؛ مرجع NBO را به‌روز کن')
        if case['revision'] != expected_revision:
            raise ValueError('نسخه پرونده تغییر کرده؛ نتیجه تازه را بخوان و دوباره تصمیم بگیر')
        if team == 'instore' and case['channel'] != 'both':
            raise ValueError('درخواست صرفاً آنلاین به نظر Instore نیاز ندارد')
        if action in ('EDIT', 'CANCEL'):
            if not labels or reason not in labels.get(action.lower(), {}):
                raise ValueError('برای اصلاح یا لغو، کد دلیل معتبر NBO لازم است')
        elif reason:
            raise ValueError('این تصمیم نباید کد دلیل اصلاح یا لغو داشته باشد')
        case[team] = None if action == 'REOPEN' else dict(action=action, actor=actor.strip(),
            note=note.strip(), reason=reason, at=store.now(), source='human')
        _save(db, case, 'WORKFLOW_HUMAN_DECISION', actor.strip(),
              {'team': team, 'action': action, 'reason': reason, 'note': note.strip()})
        return case


def apply_command(db, command, labels):
    cid = str(command.get('command_id') or '')
    if not cid or len(cid) > 128:
        raise ValueError('شناسه فرمان معتبر نیست')
    # One transaction includes both the decision and its replay receipt.
    db.execute('BEGIN IMMEDIATE')
    try:
        cached = db.execute('SELECT response FROM workflow_commands WHERE command_id=?', (cid,)).fetchone()
        if cached:
            db.commit()
            return json.loads(cached[0])
        db.execute('SAVEPOINT command_decision')
        try:
            # decide owns a transaction context: use the pure validation/write part without a separate commit.
            with _NoCommit(db) as nested:
                decide(nested, str(command.get('smr', '')), str(command.get('team', '')),
                       str(command.get('action', '')), str(command.get('actor', '')),
                       str(command.get('note', '')), int(command.get('revision', -1)),
                       str(command.get('reason', '')), labels)
            response = {'command_id': cid, 'status': 'accepted', 'error': ''}
        except (ValueError, TypeError) as e:
            db.execute('ROLLBACK TO command_decision')
            response = {'command_id': cid, 'status': 'rejected', 'error': str(e)}
        db.execute('RELEASE command_decision')
        db.execute('INSERT INTO workflow_commands VALUES (?,?)', (cid, json.dumps(response, ensure_ascii=False)))
        db.commit()
        return response
    except Exception:
        db.rollback()
        raise


class _NoCommit:
    """Reuse decision validation inside the command's atomic transaction."""
    def __init__(self, db): self.db = db
    def __getattr__(self, name): return getattr(self.db, name)
    def __enter__(self): return self
    def __exit__(self, *args): return False


def pending(db, limit=100):
    return {
        'device_id': db.execute("SELECT value FROM workflow_meta WHERE key='device_id'").fetchone()[0],
        'cases': [json.loads(r[0]) for r in db.execute(
            'SELECT body FROM workflow_cases WHERE revision>synced_revision ORDER BY smr LIMIT ?', (limit,))],
        'events': [json.loads(r[0]) for r in db.execute(
            'SELECT body FROM workflow_events WHERE synced=0 ORDER BY rowid LIMIT ?', (limit,))],
    }


def acknowledge(db, sent):
    with db:
        for case in sent['cases']:
            db.execute('UPDATE workflow_cases SET synced_revision=MAX(synced_revision,?) WHERE smr=?',
                       (case['revision'], case['smr']))
        db.executemany('UPDATE workflow_events SET synced=1 WHERE event_id=?',
                       [(e['event_id'],) for e in sent['events']])


def pending_count(db):
    return db.execute('SELECT COUNT(*) FROM workflow_cases WHERE revision>synced_revision').fetchone()[0]
