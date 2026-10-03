from datetime import datetime, timedelta, timezone

import pytest

from autoreview import execution, store, workflow


@pytest.fixture
def db():
    db = store.connect()
    workflow.ensure(db)
    execution.ensure(db)
    yield db
    db.close()


def ready(db, both=False):
    row = dict(smr='SMR-12345', site='example.test', category='test', status='PENDING',
               has_online='true', has_instore='true' if both else 'false')
    workflow.refresh(db, [row], {row['smr']})
    for team in (('online', 'instore') if both else ('online',)):
        case = workflow.get(db, row['smr'])
        workflow.decide(db, row['smr'], team, 'APPROVE', 'reviewer', 'checked', case['revision'])
    workflow.acknowledge(db, workflow.pending(db))
    return workflow.get(db, row['smr'])


@pytest.mark.parametrize('both', [False, True])
def test_eligible_requires_correct_teams(db, both):
    case = ready(db, both)
    assert execution.eligibility(case) == ''
    if both:
        case['instore'] = None
        assert execution.eligibility(case)


def test_missing_channel_never_approved(db):
    case = ready(db)
    case['channel'] = 'unknown'
    assert execution.eligibility(case)


def test_stale_source_blocked(db):
    case = ready(db)
    case['source_loaded_at'] = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    assert execution.eligibility(case)


def test_owner_only_and_readiness(db):
    mode = execution.Mode()
    with pytest.raises(PermissionError):
        mode.set(True, 'colleague', db)
    with pytest.raises(ValueError):
        mode.set(True, 'mohammadreza.vahab', db, 'not verified')
    assert not mode.live
    mode.set(True, 'mohammadreza.vahab', db)
    assert mode.live
    mode.set(False, 'mohammadreza.vahab', db, 'network unavailable')
    assert not mode.live
    assert not execution.Mode().live


def test_claim_is_once_and_restart_is_uncertain(db):
    case = ready(db)
    execution.claim(db, case)
    with pytest.raises(ValueError):
        execution.claim(db, case)
    execution.ensure(db)
    assert execution.records(db)[0]['state'] == 'UNCERTAIN'
    with pytest.raises(ValueError):
        execution.claim(db, case)


def test_changed_approval_cannot_use_old_revision(db):
    case = ready(db)
    workflow.decide(db, case['smr'], 'online', 'REOPEN', 'reviewer', 'recheck', case['revision'])
    with pytest.raises(ValueError):
        execution.claim(db, case)
    assert not execution.records(db)


def test_unshared_approval_cannot_execute(db):
    case = ready(db)
    with db:
        db.execute('UPDATE workflow_cases SET synced_revision=0')
    with pytest.raises(ValueError):
        execution.claim(db, case)


def test_preview_is_not_actual_approval(db):
    case = ready(db)
    execution.record(db, case, 'PREVIEW')
    assert execution.records(db)[0]['state'] == 'PREVIEW'
    execution.claim(db, case)
    execution.record(db, case, 'VERIFIED')
    with pytest.raises(ValueError):
        execution.claim(db, case)


def test_edit_and_cancel_verdicts_carry_their_nbo_reason(db):
    row = dict(smr='SMR-7', site='e.test', status='PENDING', has_online='true', has_instore='false')
    workflow.refresh(db, [row], {'SMR-7'})
    case = workflow.get(db, 'SMR-7')
    workflow.decide(db, 'SMR-7', 'online', 'EDIT', 'r', 'n', case['revision'], 'SITEMAP_IS_MISSING',
                    {'edit': {'SITEMAP_IS_MISSING': 'سایت‌مپ وجود ندارد'}})
    case = workflow.get(db, 'SMR-7')
    assert execution.target(case) == ('EDIT', 'SITEMAP_IS_MISSING') and execution.eligibility(case) == ''


def test_a_sent_case_is_not_sent_again_but_a_new_revision_may_be(db):
    case = ready(db)
    execution.claim(db, case)
    execution.record(db, case, 'SENT')
    with pytest.raises(ValueError):
        execution.claim(db, case)

def test_a_send_nbo_did_not_take_is_flagged_and_a_real_one_is_verified():
    """Live 2026-10-03: a cancel was reported sent while NBO only kept the request assigned (SMR-20869729)."""
    from datetime import datetime, timedelta, timezone
    from autoreview import reference, store, workflow
    db = store.connect(); reference.ensure(db); workflow.ensure(db); execution.ensure(db)
    row = lambda smr, status: dict(smr=smr, status=status, site=smr.lower() + '.ir', ownership='INDIVIDUAL',
                                   has_online='true', has_instore='false', created_at='1405/07/09')
    reference.import_nbo(db, [row('SMR-1', 'COMMERCIAL_IN_PROGRESS'), row('SMR-2', 'CANCELLED'), row('SMR-3', 'PENDING')], 'full')
    old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec='seconds')
    for smr in ('SMR-1', 'SMR-2'):
        db.execute("INSERT INTO nbo_execution VALUES (?,?,?,?,?)", (smr, 1, 'SENT', old, 'لغو'))
    db.execute("INSERT INTO nbo_execution VALUES (?,?,?,?,?)", ('SMR-3', 1, 'SENT', store.now(), 'تأیید'))   # just sent: too early
    db.commit()
    assert execution.verify_sent(db) == (1, 1)
    state = dict(db.execute("SELECT smr, state FROM nbo_execution"))
    assert state == {'SMR-1': 'UNCERTAIN', 'SMR-2': 'VERIFIED', 'SMR-3': 'SENT'}
    assert 'COMMERCIAL_IN_PROGRESS' in db.execute("SELECT detail FROM nbo_execution WHERE smr='SMR-1'").fetchone()[0]
    assert execution.verify_sent(db) == (0, 0)
