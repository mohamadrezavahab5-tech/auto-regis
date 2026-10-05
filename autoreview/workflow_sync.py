"""Own-sheet sync, triggered by the UI timer; retries remain in SQLite across restarts."""
import logging
import time

from . import sheets, workflow

log = logging.getLogger("autoreview.sheet")


def sync(db, cfg=None, client=None):
    cfg = cfg or sheets.load()
    if not cfg.get('workflow_sync'):
        raise sheets.SheetError('همگام‌سازی گردش کار خاموش است')
    return sync_workspace(db, cfg)


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
    """All devices read the same cloud authority. Install a complete generation atomically."""
    import hashlib
    import json
    from . import workspace, store, execution
    workspace.health(cfg)
    rejected_count = 0
    execution.ensure(db, recover=False)
    workspace.flush_execution(db, cfg)
    # First direct sync republishes local state without deleting any history.
    if not db.execute("SELECT 1 FROM workflow_meta WHERE key='workspace_v6_initialized'").fetchone():
        with db:
            db.execute('UPDATE workflow_cases SET synced_revision=0')
            db.execute("INSERT INTO workflow_meta VALUES('workspace_v6_initialized','1')")
            db.execute("DELETE FROM workflow_meta WHERE key='shared_generation'")
    sent = workflow.pending(db, limit=50)
    sent_cases_any = False

    # Drain several safe source batches per sync so a large backlog cannot
    # keep approved cases waiting for many sync rounds.
    from .workspace_google import compact

    for _source_batch in range(2):
        candidates = [
            json.loads(r[0])
            for r in db.execute(
                """SELECT w.body
                   FROM workflow_cases AS w
                   WHERE w.revision > w.synced_revision
                     AND NOT EXISTS (
                         SELECT 1
                         FROM nbo_execution AS n
                         WHERE n.smr = w.smr
                           AND n.state IN ('SENDING','UNCERTAIN')
                     )
                   ORDER BY (w.revision - w.synced_revision) DESC, w.smr
                   LIMIT 50"""
            )
        ]

        if not candidates:
            break

        for case in candidates:
            result = store.latest_result(db, case['smr'])
            case['review'] = (
                {k: result.get(k) for k in
                 ('smr','action','reason_codes','decided_at','duration_ms')}
                if result else case.get('review')
            )
            case['previous_executions'] = [
                dict(revision=r[0], state=r[1], at=r[2])
                for r in db.execute(
                    """SELECT revision,state,updated_at
                       FROM nbo_execution
                       WHERE smr=?
                         AND state IN
                         ('SENT','SENDING','UNCERTAIN','VERIFIED','APPROVED_IN_NBO')""",
                    (case['smr'],)
                )
            ]

        chunk = []
        for case in candidates:
            if len(compact(chunk + [case])) > 18000:
                if not chunk:
                    raise sheets.SheetError(
                        '?????? ???? ????? ????? ??? ?? ?? ???? ???? ????? ???? ????? ???',
                        'SHEET_SCHEMA_ERROR'
                    )
                break
            chunk.append(case)

        retry = db.execute(
            "SELECT value FROM workflow_meta WHERE key='source_retry'"
        ).fetchone()
        nonce = int(retry[0]) if retry else 0

        op = hashlib.sha256(
            json.dumps(
                [sent['device_id'], nonce, chunk],
                sort_keys=True,
                ensure_ascii=False
            ).encode()
        ).hexdigest()[:32]

        response = workspace.call(
            'source_batch',
            cfg,
            operation_id=op,
            cases=chunk
        )

        sent_cases_any = True

        accepted = {c['smr'] for c in response.get('cases', [])}
        refused = response.get('rejected', [])
        accounted = accepted | {r['smr'] for r in refused}

        if accounted != {c['smr'] for c in chunk}:
            raise sheets.SheetError(
                '???? ????? ????? ???? ???? ?????? ???? ??????'
            )

        accepted |= {
            r['smr']
            for r in refused
            if r.get('status') == 'stale'
        }

        workflow.acknowledge(
            db,
            dict(
                cases=[c for c in chunk if c['smr'] in accepted],
                events=[]
            )
        )

        hard_rejected = sum(
            r.get('status') != 'stale'
            for r in refused
        )
        rejected_count += hard_rejected

        if hard_rejected:
            # Do not spin on the same conflict in this sync round.
            with db:
                db.execute(
                    "INSERT OR REPLACE INTO workflow_meta VALUES('source_retry',?)",
                    (str(nonce + 1),)
                )
            break

        # Pace Google Sheets requests to stay below per-user/project quotas.
        time.sleep(3.0)
    if sent['events']:
        events = [workspace.safe_event(e) for e in sent['events']]
        receipt = workspace.call('audit_batch', cfg, events=events)
        if set(receipt.get('event_ids', [])) != {e['event_id'] for e in events}:
            raise sheets.SheetError('رسید لاگ مشترک ناقص است؛ لاگ محلی حفظ شد')
        workflow.acknowledge(db, {'cases': [], 'events': sent['events']})
    for _attempt in range(3):
        offset, generation, cases, ledger = 0, None, [], []
        while True:
            known = db.execute("SELECT value FROM workflow_meta WHERE key='shared_generation'").fetchone()
            response = workspace.call('read', cfg, offset=offset, generation=generation,
                known_generation=int(known[0]) if known and not sent_cases_any and offset == 0 else None)
            if response.get('unchanged'):
                return {'remaining': workflow.pending_count(db), 'commands': 0, 'rejected': rejected_count}
            if response.get('restart'):
                break
            generation = response['generation']
            cases.extend(response['cases'])
            if offset == 0:
                ledger = response.get('executions', [])
            following = response.get('next')
            if following is None:
                pending_ids = {r[0] for r in db.execute('SELECT smr FROM workflow_cases WHERE revision>synced_revision')}
                workspace.cache_snapshot(db, [c for c in cases if c['smr'] not in pending_ids], ledger, generation)
                return {'remaining': workflow.pending_count(db), 'commands': 0, 'rejected': rejected_count}
            if not isinstance(following, int) or following <= offset:
                raise sheets.SheetError('صفحه‌بندی سرویس نامعتبر است')
            offset = following
    raise sheets.SheetError('صف مشترک حین دریافت تغییر کرد؛ دادهٔ قبلی محفوظ است و دوباره دریافت می‌شود')
