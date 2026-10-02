"""NBO approval contract discovered from its own frontend, 2026-10-01.

Transport is deliberately injected: approval rights and the live response schema
must be verified before a production transport is registered with the app.
No CRM writes belong here.
"""
from . import execution, workflow

BASE_PATH = '/api/chandler/api/v1/backoffice/registrations'
ASSIGN_PATH = BASE_PATH + '/assign'
CHANGE_PATH = BASE_PATH + '/change-status'
TRANSITIONS_PATH = BASE_PATH + '/valid-transitions'
APPROVED = 'COMMERCIAL_APPROVED'


def change_body(detail):
    if detail.get('id') is None or isinstance(detail.get('version'), bool) or not isinstance(detail.get('version'), int):
        raise ValueError('شناسه یا نسخهٔ NBO معتبر نیست')
    return {'id': detail['id'], 'version': detail['version'], 'status': APPROVED}


def verify_identity(case, detail):
    if detail.get('requestNumber') != case['smr']:
        raise ValueError('شناسهٔ درخواست NBO با پرونده یکی نیست')
    types = detail.get('merchantTypes')
    if not isinstance(types, list) or 'ONLINE' not in types:
        raise ValueError('نوع سرویس NBO معتبر نیست')
    if ('IN_STORE' in types) != (case['channel'] == 'both'):
        raise ValueError('مسیر Online/Instore در NBO تغییر کرده است')
    change_body(detail)


def approve(db, case, backend, is_live):
    """One versioned attempt. Backend must verify full source evidence before any PUT.

    backend.read(smr), matches_source(case,detail), transitions(), assign(body),
    change(body). Reads after writes are mandatory. Failure after sending is
    ambiguous; a later run must reconcile it rather than resending automatically.
    """
    if not is_live():
        raise ValueError('حالت Real فعال نیست')
    reason = execution.eligibility(case)
    if reason:
        raise ValueError(reason)
    detail = backend.read(case['smr'])
    verify_identity(case, detail)
    if detail['status'] == APPROVED:
        execution.record(db, case, 'VERIFIED', 'از قبل در NBO تأیید شده بود؛ تغییری ارسال نشد')
        return
    if not backend.matches_source(case, detail):
        raise ValueError('اطلاعات NBO با مرجع بررسی‌شده تغییر کرده است')
    if detail['status'] not in ('PENDING', 'COMMERCIAL_IN_PROGRESS'):
        raise ValueError('وضعیت فعلی NBO برای تأیید مجاز نیست')
    execution.claim(db, case)
    sent = False

    def check_before_send():
        if not is_live():
            raise ValueError('ارسال بعدی با سوییچ Fake متوقف شد')
        current = workflow.get(db, case['smr'])
        if not current or current['revision'] != case['revision'] or execution.eligibility(current):
            raise ValueError('تأییدهای پرونده تغییر کرده‌اند')

    try:
        if detail['status'] == 'PENDING':
            if detail.get('registrationAssignee'):
                raise ValueError('درخواست به شخص دیگری تخصیص داده شده؛ بررسی دستی لازم است')
            check_before_send()
            sent = True
            backend.assign({k: change_body(detail)[k] for k in ('id', 'version')})
            assigned = backend.read(case['smr'])
            verify_identity(case, assigned)
            if assigned['id'] != detail['id'] or assigned['version'] <= detail['version']:
                raise ValueError('نتیجهٔ تخصیص درخواست تأیید نشد')
            if not backend.matches_source(case, assigned):
                raise ValueError('اطلاعات پرونده هنگام تخصیص تغییر کرده است')
            detail = assigned
        transitions = backend.transitions()
        if APPROVED not in transitions.get(detail['status'], []):
            raise ValueError('NBO این انتقال وضعیت را مجاز اعلام نکرده است')
        check_before_send()
        sent = True
        backend.change(change_body(detail))
        after = backend.read(case['smr'])
        verify_identity(case, after)
        if after['id'] != detail['id'] or after['status'] != APPROVED or after['version'] <= detail['version']:
            raise ValueError('پاسخ موفق کافی نیست؛ وضعیت تأیید در NBO مشاهده نشد')
        execution.record(db, case, 'VERIFIED', 'وضعیت تأیید با خواندن دوباره از NBO بررسی شد')
    except Exception:
        # Do not persist backend exception messages: they can contain server bodies.
        execution.record(db, case, 'UNCERTAIN' if sent else 'BLOCKED',
                         'اجرای درخواست کامل نشد؛ قبل از هر تلاش بعدی وضعیت NBO بررسی شود')
        raise
