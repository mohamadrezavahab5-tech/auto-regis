from datetime import datetime, timedelta, timezone

from autoreview import insights, store, workflow

LABELS = {'edit': {'SITEMAP_IS_MISSING': 'x'}, 'cancel': {'DUP': 'y'}}


def setup(db, n=4):
    workflow.ensure(db)
    rows = [dict(smr=f'SMR-{i}', site=f's{i}.ir', status='PENDING', has_online='true', has_instore='false') for i in range(n)]
    workflow.refresh(db, rows, {r['smr'] for r in rows})
    return rows


def test_agreement_counts_people_against_the_engine_and_names_the_overruled_rule():
    db = store.connect()
    setup(db)
    workflow.suggest(db, 'SMR-0', dict(action='APPROVE', reason_codes=[], notes=[]), engine_counts=True)
    workflow.suggest(db, 'SMR-1', dict(action='EDIT', reason_codes=['SITEMAP_IS_MISSING'], notes=[]), engine_counts=True)
    workflow.suggest(db, 'SMR-2', dict(action='MANUAL', reason_codes=[], notes=[]), engine_counts=True)
    for smr, action, reason in (('SMR-0', 'APPROVE', ''), ('SMR-1', 'APPROVE', ''), ('SMR-2', 'CANCEL', 'DUP')):
        workflow.decide(db, smr, 'online', action, 'سارا', 'checked', workflow.get(db, smr)['revision'], reason, LABELS)
    a = insights.accuracy(workflow.cases(db))
    assert (a['compared'], a['agreed'], round(a['rate'])) == (2, 1, 50)
    assert a['by_rule'][0] == ('SITEMAP_IS_MISSING', 1, 1)
    assert a['manual'] == {'CANCEL': 1}
    assert a['disagreements'][0]['smr'] == 'SMR-1' and a['disagreements'][0]['actor'] == 'سارا'


def test_control_room_speed_eta_oldest_and_people():
    db = store.connect()
    setup(db)
    now = datetime.now(timezone.utc)                       # the human decision below is stamped now
    workflow.decide(db, 'SMR-0', 'online', 'APPROVE', 'سارا', 'ok', workflow.get(db, 'SMR-0')['revision'])
    results = [((now - timedelta(hours=h)).isoformat(), 'MANUAL') for h in (1, 2, 3)]
    created = {'SMR-1': '۱۴۰۵/۰۷/۰۱', 'SMR-2': '۱۴۰۵/۰۷/۰۸'}
    r = insights.control_room(workflow.cases(db), insights.events(db), results, created, now=now)
    assert r['undecided'] == 3 and r['open'] == 4
    assert r['speed'] == 1 and r['eta_days'] == 3            # people's decisions only: the engine cannot clear these
    none = insights.control_room(workflow.cases(db), [], results, created, now=now)
    assert none['speed'] is None and none['eta_days'] is None
    assert r['oldest'][0][1] == 'SMR-1' and r['oldest'][0][0] >= 9
    assert dict(r['people'])['موتور (AutoReview)'] == 3
