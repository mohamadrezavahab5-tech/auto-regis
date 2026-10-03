"""Own-sheet sync, triggered by the UI timer; retries remain in SQLite across restarts."""
import logging
import time

from . import sheets, workflow

log = logging.getLogger("autoreview.sheet")


def sync(db, cfg=None, client=None):
    cfg = cfg or sheets.load()
    if not cfg.get('workflow_sync'):
        raise sheets.SheetError('همگام‌سازی گردش کار خاموش است')
    if cfg.get('auth_mode') == 'workspace':
        return sync_workspace(db, cfg)
    if cfg.get('auth_mode') == 'service_account':
        from .google_sheet import Client
        with Client(cfg['own_sheet_id']) as google:
            return sync_direct(db, google)
    url = cfg.get('webapp_url', '')
    def post(action, **values):
        return sheets._post(url, dict(secret=cfg['secret'], action=action, **values), client)
    info = post('ping')
    if info.get('version', 0) < 3 or info.get('sheet_id') != cfg.get('own_sheet_id'):
        raise sheets.SheetError('اسکریپت نسخه 3 را در شیت اختصاصی نصب و دوباره Deploy کن')
    # Publish current revisions before accepting human commands based on them.
    sent = workflow.pending(db)
    result = post('workflow_sync', **sent)
    if result.get('cases') != len(sent['cases']) or result.get('events') != len(sent['events']):
        raise sheets.SheetError('تعداد رسیدهای شیت با بسته ارسال‌شده برابر نیست؛ دوباره تلاش می‌شود')
    workflow.acknowledge(db, sent)
    incoming = post('workflow_commands', device_id=sent['device_id'])
    commands = incoming.get('commands')
    if not isinstance(commands, list) or len(commands) > 100:
        raise sheets.SheetError('پاسخ فرمان‌های شیت معتبر نیست')
    receipts = [workflow.apply_command(db, c, sheets.nbo_labels()) for c in commands]
    if receipts:
        post('workflow_ack', device_id=sent['device_id'], receipts=receipts)
    return {'remaining': workflow.pending_count(db), 'commands': len(receipts),
            'rejected': sum(r['status'] == 'rejected' for r in receipts)}


_LEGAL_NEXT = 0.0
_REPORT_NEXT = 0.0
_LEGAL_PENDING = None


def sync_direct(db, google):
    """The owner's PC (service-account key) is the single writer of the owner's sheet. Each step is idempotent, so a failure
    half-way is simply finished by the next round 30 seconds later."""
    from . import execution
    labels = sheets.nbo_labels()
    google.ensure_tabs_once(list(labels['edit'].values()) + list(labels['cancel'].values()),
                            sheets.load().get('sheet_editors') or ())
    from .google_sheet import workflow_key
    every = workflow.cases(db)
    sent = workflow.pending(db, limit=400)          # one atomic batch; a large first upload takes a few rounds, not dozens
    from . import sheet_log
    from . import activity
    mine = activity.sent_from_app(db)
    sent['log'] = {e['event_id']: sheet_log.event_row(db, e, mine) for e in sent['events']}
    result = google.sync(sent, keys={c['smr']: workflow_key(c) for c in every})
    if result.get('cases') != len(sent['cases']) or result.get('events') != len(sent['events']):
        raise sheets.SheetError('رسید ارسال کامل نیست؛ صف محفوظ می‌ماند')
    workflow.acknowledge(db, sent)
    # Instore verdicts typed into the Online + Instore tab, then the tab's app columns brought up to date
    statuses = {}
    for smr, values in google.instore_entries():
        status = workflow.sheet_verdict(db, smr, 'instore', values, labels)
        if status:
            statuses[smr] = status
    both = [c for c in workflow.cases(db) if c['channel'] == 'both']           # again: the Instore verdicts changed some
    google.write_online_instore([workflow.sheet_row(c) for c in both], statuses)
    incoming = google.commands()['commands']
    receipts = [workflow.apply_command(db, c, labels) for c in incoming]
    if receipts:
        google.ack(receipts)
    google.upsert_execution(execution.records(db, limit=500))
    nbo_log, last = sheet_log.nbo_rows(db)          # what the app did in NBO: who, by hand or automatic, the outcome
    google.append_log(nbo_log)
    sheet_log.remember(db, last)
    global _LEGAL_NEXT, _LEGAL_PENDING, _REPORT_NEXT
    if time.monotonic() >= _LEGAL_NEXT:              # ~25,000 rows: every 5 minutes, not every 30-second round
        from . import reference, settings
        written = google.write_legal(reference.legal_rows(db, settings.load_rules()))
        _LEGAL_PENDING = getattr(google, 'legal_pending', _LEGAL_PENDING)
        catching_up = written >= 500 or getattr(google, 'legal_writes', 0) >= 3000
        _LEGAL_NEXT = time.monotonic() + (30 if catching_up else 300)   # a first upload continues next round
    if time.monotonic() >= _REPORT_NEXT:             # the report tab: numbers every 5 minutes, its charts follow them
        from . import sheet_report
        from .google_sheet import GoogleSheetError
        _REPORT_NEXT = time.monotonic() + 300
        try:
            google.write_report(sheet_report.grid(sheet_report.report(db, _LEGAL_PENDING)))
        except GoogleSheetError as e:              # the report only: a failure must never stop the data
            log.warning("report tab not written: %s", e)
    return {'remaining': workflow.pending_count(db), 'commands': len(receipts),
            'rejected': sum(r['status'] == 'rejected' for r in receipts)}


def sync_workspace(db, cfg):
    import hashlib
    from . import workspace
    profile = workspace.call('whoami',cfg)['user']
    sent = workflow.pending(db,limit=20)
    if profile['role'] in ('admin','online'):
        for case in sent['cases']:
            op = hashlib.sha256((sent['device_id']+case['smr']+str(case['revision'])).encode()).hexdigest()[:32]
            response = workspace.call('source',cfg,operation_id=op,case=case)
            workflow.acknowledge(db,{'cases':[case],'events':[]})
            workspace.cache_cases(db,[response['case']])
    offset=0
    while True:
        response=workspace.call('read',cfg,offset=offset)
        # Preserve pending local source changes; they must not be lost during a remote read.
        pending_ids=({r[0] for r in db.execute('SELECT smr FROM workflow_cases WHERE revision>synced_revision')}
                     if profile['role'] in ('admin','online') else set())
        workspace.cache_cases(db,[c for c in response['cases'] if c['smr'] not in pending_ids])
        if response.get('next') is None: break
        offset=response['next']
    return {'remaining':workflow.pending_count(db),'commands':0,'rejected':0}
