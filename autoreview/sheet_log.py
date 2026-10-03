"""The log tabs of the owner's sheet: one readable Persian row for everything that happens to a request - the engine's
review, each team's verdict, NBO's status changing, and every change made in NBO with who did it, how, and WHERE: in
this app or outside it (straight in NBO). Online-only requests go to 'لاگ Online', Online + Instore requests to their
own tab. Owner 2026-10-03: "the log in the sheet must be recorded exactly", "apart for Online and for Online + Instore",
"show whether the approval came from this app or from outside". The Audit tab keeps the same events as JSON for the
app; these tabs are for people. Rows are only added, never rewritten."""
import json

from . import activity, execution, jalali, workflow
from .texts import ACTION_FA, notes_fa, reasons_fa

TEAM_FA = {'online': 'Online', 'instore': 'Instore'}
SOURCE_FA = {'human': 'این اپ', 'sheet': 'شیت (تیم Instore)', 'workspace': 'این اپ (همکار)', 'engine': 'این اپ (موتور)'}
MODE_FA = {'manual': 'دستی', 'automatic': 'خودکار', 'rehearsal': 'تمرین'}
# what the app did in NBO, worth a row (a preview row is only "ready", a rehearsal changes nothing)
NBO_STAGES = ('NBO_SENDING', 'NBO_SENT', 'NBO_BLOCKED', 'NBO_UNCERTAIN', 'NBO_VERIFIED')
POINTER = 'sheet_log_audit_id'
APP, ENGINE, OUTSIDE = 'این اپ', 'این اپ (موتور)', 'بیرون از اپ (مستقیم در NBO)'


def _when(at):
    try:
        return jalali.jdatetime(at, False)
    except (TypeError, ValueError):
        return str(at or '')


def _base(db, smr, at):
    """-> (case, path 'online' | 'both', [time, request, site])"""
    case = workflow.get(db, smr) or {}
    return case, ('both' if case.get('channel') == 'both' else 'online'), [_when(at), smr, case.get('site', '')]


def event_row(db, e, mine=None):
    """One workflow event -> (path, row) for its log tab. mine: activity.sent_from_app(db)."""
    kind, d = e.get('kind'), e.get('detail') or {}
    case, path, row = _base(db, e.get('smr', ''), e.get('at'))
    s = case.get('suggestion') or {}
    what, result, where, who, why = kind or '', '', APP, e.get('actor') or '—', ''
    if kind == 'WORKFLOW_IMPORTED':
        what = 'وارد صف بررسی شد'
    elif kind == 'WORKFLOW_SUGGESTED':
        what, result, where, who = 'بررسی موتور', ACTION_FA.get(d.get('action'), d.get('action') or ''), ENGINE, 'موتور'
        why = reasons_fa(s.get('reason_codes')) or notes_fa(s.get('notes'))
    elif kind == 'WORKFLOW_ENGINE_VERDICT':
        what, where, who = 'نتیجه‌ی موتور به‌عنوان نظر Online ثبت شد', ENGINE, 'موتور'
        result, why = ACTION_FA.get(d.get('action'), d.get('action') or ''), reasons_fa(s.get('reason_codes'))
    elif kind == 'WORKFLOW_ENGINE_VERDICT_WITHDRAWN':
        what, where, who = 'نظر موتور برداشته شد؛ منتظر نظر Online', ENGINE, 'موتور'
    elif kind == 'WORKFLOW_HUMAN_DECISION':
        what = f"نظر تیم {TEAM_FA.get(d.get('team'), d.get('team') or '')} ثبت شد"
        result = 'نظر برداشته شد' if d.get('action') == 'REOPEN' else ACTION_FA.get(d.get('action'), d.get('action') or '')
        where = SOURCE_FA.get(d.get('source'), APP)
        why = ' — '.join(x for x in (reasons_fa([d['reason']]) if d.get('reason') else '', d.get('note') or '') if x)
    elif kind == 'WORKFLOW_NBO_STATUS':
        status = d.get('status')
        by_app = e.get('smr') in (mine if mine is not None else activity.sent_from_app(db))
        what, result, who = 'وضعیت در NBO عوض شد', activity.NBO_FA.get(status, status or ''), 'NBO'
        # a request leaving the queue without a send from this app was decided by someone directly in NBO
        where = APP if by_app else (OUTSIDE if status not in activity.NBO_OPEN else 'NBO')
        why = '' if by_app or status in activity.NBO_OPEN else 'این تغییر را این اپ نزده است'
    elif kind == 'WORKFLOW_SOURCE_CHANGED':
        what, where, who = 'اطلاعات درخواست در NBO عوض شد؛ نظرها از نو شروع شد', 'NBO', 'NBO'
    elif kind == 'WORKFLOW_SOURCE_REMOVED':
        what, where, who = 'از خروجی NBO بیرون رفت', 'NBO', 'NBO'
    return path, row + [what, result, where, who, why, e.get('event_id', '')]


def nbo_rows(db, limit=400):
    """What this app did in NBO since the last time: -> ([(path, row)], last audit id). The first call starts at the
    first real send, so the test runs made before going live never show up."""
    got = db.execute('SELECT value FROM workflow_meta WHERE key=?', (POINTER,)).fetchone()
    if got is None:
        first = db.execute("SELECT MIN(id) FROM audit WHERE stage = 'NBO_SENT'").fetchone()[0]
        last = (first - 1) if first else (db.execute('SELECT COALESCE(MAX(id), 0) FROM audit').fetchone()[0])
    else:
        last = int(got[0])
    marks = ','.join('?' * len(NBO_STAGES))
    rows = []
    for ident, ts, smr, stage, raw in db.execute(
            f'SELECT id, ts, smr, stage, detail FROM audit WHERE id > ? AND stage IN ({marks}) ORDER BY id LIMIT ?',
            (last, *NBO_STAGES, limit)):
        try:
            d = json.loads(raw) if raw else {}
        except ValueError:
            d = {}
        last = ident
        if d.get('mode') == 'rehearsal':
            continue
        _case, path, row = _base(db, smr, ts)
        target = d.get('target') or ()
        what = 'ثبت در NBO' + (f": {ACTION_FA.get(target[0], target[0])}" if target else '')
        who = ' — '.join(x for x in (d.get('actor') or '', MODE_FA.get(d.get('mode'), d.get('mode') or '')) if x) or '—'
        rows.append((path, row + [what, execution.LABELS.get(stage[4:], stage), APP, who, str(d.get('detail') or ''),
                                  f'audit:{ident}']))
    return rows, last


def remember(db, last):
    with db:
        db.execute('INSERT INTO workflow_meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   (POINTER, str(int(last))))
