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
