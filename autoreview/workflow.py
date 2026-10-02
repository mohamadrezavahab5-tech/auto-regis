"""Each request's journey after the engine: the Online verdict, the Instore verdict (Online + Instore requests only), and what
NBO finally did. Kept apart from the engine's recommendation and from NBO's real status.

Owner rules (2026-10-01):
- Online only: the Online verdict alone decides. Online + Instore: ready for NBO only when BOTH teams approved.
- The engine's APPROVE / EDIT / CANCEL counts as the Online verdict while 'engine verdict counts' is on; MANUAL always waits
  for a person, and a person can replace any verdict at any time (a person's verdict stands until the evidence changes).
- Nothing here changes NBO. When NBO's own status moves on (approved there, or closed), the case stays as DONE_* so the team
  always sees what is finished, not only what is left.

No external actions. Own-sheet sync is an outbox; acknowledgements cannot discard newer revisions. A changed request (site,
names, channel ...) invalidates old verdicts, and a request that re-enters the queue starts over.
"""
import hashlib
import json
import re
import uuid

from . import jalali, store
from .texts import notes_fa, reasons_fa

STATES = {
    'WAIT_ONLINE': 'منتظر نظر Online', 'MANUAL': 'نیازمند بررسی دستی', 'WAIT_INSTORE': 'منتظر نظر Instore',
    'CONFLICT': 'اختلاف نظر دو تیم', 'EDIT': 'نیاز به اصلاح؛ اعمال نشده', 'CANCEL': 'پیشنهاد لغو؛ اعمال نشده',
    'READY': 'آماده تأیید در NBO؛ اعمال نشده', 'DONE_APPROVED': 'تأییدشده در NBO', 'DONE_CLOSED': 'بسته‌شده در NBO',
    'OUT_OF_SCOPE': 'خارج از صف فعال',
}
OPEN_STATES = ('WAIT_ONLINE', 'MANUAL', 'WAIT_INSTORE', 'CONFLICT', 'EDIT', 'CANCEL', 'READY')
DONE_STATES = ('DONE_APPROVED', 'DONE_CLOSED')
ACTIONS = ('APPROVE', 'EDIT', 'CANCEL', 'MANUAL', 'REOPEN')
FIELDS = ('site', 'category', 'ownership', 'has_online', 'has_instore', 'account_holder', 'owner_name', 'owner_family')
ENGINE = 'AutoReview'

# What people type in the sheet / the old Online-Instore dropdowns -> verdict
SHEET_RESULTS = {'APPROVE': 'تایید قرارداد', 'EDIT': 'نیاز به ادیت', 'CANCEL': 'لغو قرارداد', 'MANUAL': 'بررسی دستی'}
_ACTION_ALIASES = {
    'approve': 'APPROVE', 'تایید': 'APPROVE', 'تایید قرارداد': 'APPROVE', 'تاییدشد': 'APPROVE', 'تایید شد': 'APPROVE',
    'edit': 'EDIT', 'اصلاح': 'EDIT', 'نیاز به اصلاح': 'EDIT', 'ادیت': 'EDIT', 'نیاز به ادیت': 'EDIT',
    'cancel': 'CANCEL', 'لغو': 'CANCEL', 'لغو قرارداد': 'CANCEL',
    'manual': 'MANUAL', 'دستی': 'MANUAL', 'بررسی دستی': 'MANUAL',
    'reopen': 'REOPEN', 'بازگشایی': 'REOPEN',
}
_TEAM_ALIASES = {'online': 'online', 'آنلاین': 'online', 'انلاین': 'online',
                 'instore': 'instore', 'in-store': 'instore', 'in store': 'instore', 'حضوری': 'instore',
                 'این استور': 'instore', 'اینستور': 'instore'}


def _norm(text) -> str:
    """Spelling-tolerant key: Arabic ي/ك, hamza seat, ZWNJ and extra spaces never make a valid choice look invalid."""
    s = str(text or '').strip().lower()
    s = s.replace('ي', 'ی').replace('ك', 'ک').replace('أ', 'ا').replace('ٔ', '').replace('‌', ' ')
    return re.sub(r'\s+', ' ', s)


def action_of(text):
    return _ACTION_ALIASES.get(_norm(text)) or (str(text).strip().upper() if str(text).strip().upper() in ACTIONS else None)


def team_of(text):
    return _TEAM_ALIASES.get(_norm(text))


def reason_of(text, action, labels):
    """An NBO reason given as its code or its Persian label -> the code ('' if none matches)."""
    options = (labels or {}).get(str(action).lower(), {})
    raw = str(text or '').strip()
    if raw in options:
        return raw
    wanted = _norm(raw)
    return next((code for code, lab in options.items() if _norm(lab) == wanted), '') if wanted else ''


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
    CREATE TABLE IF NOT EXISTS workflow_sheet_marks (
      smr TEXT NOT NULL, team TEXT NOT NULL, hash TEXT NOT NULL, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
      PRIMARY KEY (smr, team));
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
        return {'approved': 'DONE_APPROVED', 'closed': 'DONE_CLOSED'}.get(case.get('outcome'), 'OUT_OF_SCOPE')
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


def counts(db):
    """{state: n} over every case ever seen, open and done."""
    out = {k: 0 for k in STATES}
    for case in cases(db):
        out[case.get('state') or state(case)] += 1
    return out


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


def _fingerprint(row):
    return hashlib.sha256(json.dumps({k: row.get(k) for k in FIELDS}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def refresh(db, rows, eligible_ids, source_loaded_at=None, approved_statuses=(), complete=True):
    """Called with a COMPLETE local NBO reference, never a partial batch.

    - unchanged request, new NBO status: verdicts stay; leaving the queue makes it DONE_APPROVED (NBO approved it, by the
      app or by hand) or DONE_CLOSED (edit requested, cancelled ...)
    - changed request (site, names, channel ...) or a request coming back into the queue: verdicts start over."""
    approved = set(approved_statuses or ())
    loaded = source_loaded_at or store.now()
    seen = set()
    with db:
        for row in rows:
            smr = row['smr']
            seen.add(smr)
            if row.get('status') == 'LEFT_QUEUE':          # reference.LEFT_QUEUE: decided in NBO, outcome not known yet
                continue
            old = get(db, smr)
            active = smr in eligible_ids
            if not active and old is None:
                continue
            status = row.get('status', '') or ''
            fingerprint = _fingerprint(row)
            outcome = None if active else ('approved' if status in approved else 'closed')
            if old and old['fingerprint'] == fingerprint and not (active and not old['active']):
                if old.get('source_status') == status and old['active'] == active:
                    if source_loaded_at and old.get('source_loaded_at') != source_loaded_at:
                        old['source_loaded_at'] = source_loaded_at
                        db.execute('UPDATE workflow_cases SET body=? WHERE smr=?', (json.dumps(old, ensure_ascii=False), smr))
                    continue
                old.update(source_status=status, active=active, outcome=outcome, source_loaded_at=loaded)
                _save(db, old, 'WORKFLOW_NBO_STATUS', detail={'status': status})
                continue
            channel = 'both' if str(row.get('has_instore')).lower() == 'true' else 'online'
            case = dict(smr=smr, site=row.get('site', ''), category=row.get('category', ''),
                        brand=row.get('brand_fa', ''), source_status=status, channel=channel,
                        active=active, outcome=outcome, fingerprint=fingerprint, online=None, instore=None,
                        suggestion=None, online_hold=False,
                        revision=old['revision'] if old else 0, source_loaded_at=loaded,
                        reset_at=store.now() if old else '')       # reviews before a reset no longer describe this request
            _save(db, case, 'WORKFLOW_SOURCE_CHANGED' if old else 'WORKFLOW_IMPORTED',
                  detail={'approvals_reset': bool(old)})
        for old in cases(db) if complete else ():     # a partial NBO refresh proves nothing about absent requests
            if old['smr'] not in seen and old['active']:
                old.update(active=False, outcome=None, online=None, instore=None)
                _save(db, old, 'WORKFLOW_SOURCE_REMOVED')


def _engine_verdict(suggestion):
    action = (suggestion or {}).get('action')
    if action not in ('APPROVE', 'EDIT', 'CANCEL'):
        return None                                     # MANUAL (or nothing yet) always waits for a person
    codes = list(suggestion.get('reason_codes') or [])
    note = notes_fa(suggestion.get('notes')) or 'نتیجهٔ بررسی خودکار'
    return dict(action=action, actor=ENGINE, note=note, reason=codes[0] if codes else '', reasons=codes,
                at=suggestion.get('decided_at') or store.now(), source='engine')


def suggest(db, smr, result, engine_counts=False):
    with db:
        case = get(db, smr)
        if not case or not case['active']:
            return
        suggestion = {k: result.get(k) for k in ('action', 'reason_codes', 'notes', 'decided_at')}
        before = json.dumps([case.get('suggestion'), case.get('online'), case.get('online_hold')], sort_keys=True,
                            ensure_ascii=False)
        old = case.get('suggestion') or {}
        if any(old.get(k) != suggestion.get(k) for k in ('action', 'reason_codes')):
            case['online'] = None                       # new evidence: no earlier Online verdict stands on its own
            case['online_hold'] = False
        case['suggestion'] = suggestion
        if engine_counts and case['online'] is None and not case.get('online_hold'):
            case['online'] = _engine_verdict(suggestion)
        if json.dumps([case.get('suggestion'), case.get('online'), case.get('online_hold')], sort_keys=True,
                      ensure_ascii=False) == before:
            return
        _save(db, case, 'WORKFLOW_SUGGESTED', detail={'action': suggestion['action'],
                                                      'engine_verdict': bool((case['online'] or {}).get('source') == 'engine')})


def reconcile_suggestions(db, latest, engine_counts=False):
    """latest: store.latest_results(). Gives every open case the engine's newest review when it does not have it yet - a
    review that was interrupted, stopped or failed half-way never reached the queue (live 2026-10-02: 41 Online + Instore
    requests reviewed but still 'waiting for Online'). A review from before the case was reset is ignored. -> applied."""
    resets = {}
    for smr, body in db.execute("SELECT smr, body FROM workflow_events"):
        if '"WORKFLOW_SOURCE_CHANGED"' in body:
            at = json.loads(body).get('at') or ''
            resets[smr] = max(resets.get(smr, ''), at)
    applied = 0
    for case in cases(db):
        result = latest.get(case['smr'])
        if not case.get('active') or not result:
            continue
        since = case.get('reset_at') or resets.get(case['smr'], '')
        if (result.get('decided_at') or '') < since:
            continue
        if (case.get('suggestion') or {}).get('decided_at', '') >= (result.get('decided_at') or ''):
            continue
        suggest(db, case['smr'], result, engine_counts)
        applied += 1
    return applied


def adopt_engine_verdicts(db, on):
    """The 'engine verdict counts' switch: fill in the engine's Online verdict where nobody decided yet, or withdraw the
    engine's verdicts when switched off. A person's verdict is never touched. -> number of cases changed."""
    changed = 0
    with db:
        for case in cases(db):
            if not case.get('active'):
                continue
            online = case.get('online') or {}
            if on and not online and not case.get('online_hold') and case.get('suggestion'):
                verdict = _engine_verdict(case['suggestion'])
                if verdict:
                    case['online'] = verdict
                    _save(db, case, 'WORKFLOW_ENGINE_VERDICT', ENGINE, {'action': verdict['action']})
                    changed += 1
            elif not on and online.get('source') == 'engine':
                case['online'] = None
                _save(db, case, 'WORKFLOW_ENGINE_VERDICT_WITHDRAWN', ENGINE)
                changed += 1
    return changed


def decide(db, smr, team, action, actor, note, expected_revision, reason='', labels=None, source='human'):
    if team not in ('online', 'instore') or action not in ACTIONS:
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
            note=note.strip(), reason=reason, at=store.now(), source=source)
        if team == 'online':
            case['online_hold'] = action == 'REOPEN'    # reopened: the engine must not quietly fill it back in
        _save(db, case, 'WORKFLOW_HUMAN_DECISION', actor.strip(),
              {'team': team, 'action': action, 'reason': reason, 'note': note.strip(), 'source': source})
        return case


def normalize_command(command, labels):
    """A decision typed into the sheet: Persian or English team / decision / reason, revision optional (empty = the
    revision the app sees when it first reads the row, a few seconds after it was ticked)."""
    cmd = dict(command)
    cmd['team'] = team_of(cmd.get('team')) or str(cmd.get('team', '')).strip()
    cmd['action'] = action_of(cmd.get('action')) or str(cmd.get('action', '')).strip()
    if cmd['action'] in ('EDIT', 'CANCEL'):
        cmd['reason'] = reason_of(cmd.get('reason'), cmd['action'], labels) or str(cmd.get('reason', '')).strip()
    revision = cmd.get('revision')
    if revision in (None, ''):
        cmd['revision'] = None
    return cmd


def apply_command(db, command, labels):
    cid = str(command.get('command_id') or '')
    if not cid or len(cid) > 128:
        raise ValueError('شناسه فرمان معتبر نیست')
    command = normalize_command(command, labels)
    # One transaction includes both the decision and its replay receipt.
    db.execute('BEGIN IMMEDIATE')
    try:
        cached = db.execute('SELECT response FROM workflow_commands WHERE command_id=?', (cid,)).fetchone()
        if cached:
            db.commit()
            return json.loads(cached[0])
        db.execute('SAVEPOINT command_decision')
        try:
            smr = str(command.get('smr', '')).strip()
            revision = command.get('revision')
            if revision is None:
                current = get(db, smr)
                revision = current['revision'] if current else -1
            # decide owns a transaction context: use the pure validation/write part without a separate commit.
            with _NoCommit(db) as nested:
                decide(nested, smr, str(command.get('team', '')),
                       str(command.get('action', '')), str(command.get('actor', '')),
                       str(command.get('note', '')), int(revision),
                       str(command.get('reason', '')), labels, source='sheet')
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


# ---- the Online-Instore tab of the owner's sheet ---------------------------------------------------------------------------
def _mark(db, smr, team, digest, fingerprint, status):
    db.execute('''INSERT INTO workflow_sheet_marks VALUES (?,?,?,?,?) ON CONFLICT(smr, team) DO UPDATE SET
                  hash=excluded.hash, fingerprint=excluded.fingerprint, status=excluded.status''',
               (smr, team, digest, fingerprint, status))


def sheet_verdict(db, smr, team, values, labels):
    """A team's verdict typed into the owner's sheet (the Instore columns of the Online-Instore tab).

    Applied once per distinct entry: the same cells are not applied again every sync, an edit of them is a new verdict and
    clearing the result withdraws a verdict that came from the sheet. If the request itself changed after the verdict was
    applied, the old cells are not re-used - the team picks the result again. -> short Persian status for the row."""
    entry = {k: str(values.get(k) or '').strip() for k in ('result', 'reason', 'note', 'actor', 'date')}
    digest = hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    case = get(db, smr)
    if not case:
        return 'این کد در گردش کار نیست'
    mark = db.execute('SELECT hash, fingerprint, status FROM workflow_sheet_marks WHERE smr=? AND team=?', (smr, team)).fetchone()
    if mark and mark[0] == digest:
        if entry['result'] and mark[1] != case['fingerprint']:
            return 'اطلاعات درخواست عوض شد؛ نتیجه را پاک کن و دوباره انتخاب کن'
        return mark[2]
    current = case.get(team) or {}
    if not entry['result']:
        if current.get('source') != 'sheet' or not case['active']:
            with db:
                _mark(db, smr, team, digest, case['fingerprint'], '')
            return ''
        action, reason = 'REOPEN', ''
    else:
        action = action_of(entry['result'])
        if action not in ('APPROVE', 'EDIT', 'CANCEL', 'MANUAL'):
            with db:
                _mark(db, smr, team, digest, case['fingerprint'], 'نتیجه نامعتبر است؛ از فهرست کشویی انتخاب کن')
            return 'نتیجه نامعتبر است؛ از فهرست کشویی انتخاب کن'
        reason = reason_of(entry['reason'], action, labels) if action in ('EDIT', 'CANCEL') else ''
    actor = entry['actor'] or ('تیم Instore' if team == 'instore' else 'تیم Online')
    note = entry['note'] or ('ثبت در شیت' + (f" — {entry['date']}" if entry['date'] else ''))
    try:
        decide(db, smr, team, action, actor, note, case['revision'], reason, labels, source='sheet')
        status = 'نظر برداشته شد' if action == 'REOPEN' else 'ثبت شد'
    except ValueError as e:
        status = 'رد شد: ' + str(e)
    with db:
        _mark(db, smr, team, digest, case['fingerprint'], status)
    return status


def sheet_row(case, labels=None):
    """App-owned cells of one Online-Instore row: Online date, result, reasons; and the case's state."""
    online = case.get('online') or {}
    action = online.get('action')
    codes = online.get('reasons') or ([online['reason']] if online.get('reason') else [])
    when = ''
    if online.get('at'):
        try:
            when = jalali.jdate(online['at'], False)
        except (TypeError, ValueError):
            when = ''
    code = case.get('state') or state(case)
    return dict(smr=case['smr'], site=case.get('site', ''), category=case.get('category', ''), online_date=when,
                online_result=SHEET_RESULTS.get(action, ''), online_reasons=reasons_fa(codes) if codes else '',
                online_by=(online.get('actor', '') + ' (خودکار)') if online.get('source') == 'engine' else online.get('actor', ''),
                state=case.get('state_fa') or STATES[code], state_code=code,
                updated_at=case.get('updated_at', ''), revision=case.get('revision', 0))


# ---- outbox ----------------------------------------------------------------------------------------------------------------
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
