"""Plain-language status and persisted request activity; missing history is never invented."""
import json

from . import execution, workflow


def status(case, receipt=None):
    state = workflow.state(case) if case else 'OUT_OF_SCOPE'
    code = (receipt or {}).get('state')
    if code == 'UNCERTAIN':
        return 'نتیجه نامشخص', 'وضعیت را در NBO بررسی کنید؛ دوباره ارسال نکنید'
    if code == 'SENDING':
        return 'در حال ارسال', 'منتظر پایان عملیات بمانید'
    if state == 'DONE_APPROVED':
        return 'انجام شده — تأیید در NBO', 'اقدامی لازم نیست'
    if state == 'DONE_CLOSED':
        return 'بسته شده در NBO', 'وضعیت نهایی NBO را در جزئیات ببینید'
    if code == 'SENT':
        return 'ارسال شده — منتظر تطبیق', 'با دریافت بعدی NBO، نتیجهٔ نهایی تطبیق داده می‌شود'
    if code == 'BLOCKED':
        return 'انجام نشده — اجرا متوقف شد', 'علت توقف را در سابقه بررسی کنید'
    if code == 'REHEARSED':
        return 'فقط پیش‌نمایش انجام شده', 'برای تغییر NBO، ثبت واقعی هنوز لازم است'
    return {
        'READY': ('آمادهٔ ثبت — هنوز انجام نشده', 'ثبت در NBO'),
        'EDIT': ('اصلاح پیشنهاد شده — هنوز انجام نشده', 'بررسی دلیل و ثبت اصلاح در NBO'),
        'CANCEL': ('لغو پیشنهاد شده — هنوز انجام نشده', 'بررسی دلیل و ثبت لغو در NBO'),
        'WAIT_ONLINE': ('منتظر Online', 'نظر Online باید ثبت شود'),
        'WAIT_INSTORE': ('منتظر Instore', 'نظر Instore باید ثبت شود'),
        'MANUAL': ('نیازمند بررسی انسان', 'به رسیدگی دستی بروید'),
        'CONFLICT': ('اختلاف نظر تیم‌ها', 'مشخص کنید کدام تیم باید نظرش را اصلاح کند'),
    }.get(state, ('خارج از صف فعال', 'وضعیت مرجع را بررسی کنید'))


EVENTS = {
    'WORKFLOW_IMPORTED': 'ورود درخواست به صف', 'WORKFLOW_SUGGESTED': 'پیشنهاد موتور ثبت شد',
    'WORKFLOW_HUMAN_DECISION': 'نظر همکار ثبت شد', 'WORKFLOW_ENGINE_VERDICT': 'نظر Online از موتور ثبت شد',
    'WORKFLOW_SOURCE_CHANGED': 'اطلاعات مرجع تغییر کرد', 'WORKFLOW_NBO_STATUS': 'وضعیت NBO به‌روز شد',
}


def audit_rows(db, query='', limit=1000):
    params = []
    where = ''
    if query.strip():
        where = ' WHERE smr LIKE ? OR stage LIKE ? OR detail LIKE ?'
        params = ['%' + query.strip() + '%'] * 3
    rows = db.execute('SELECT id,ts,smr,stage,detail FROM audit' + where + ' ORDER BY id DESC LIMIT ?',
                      (*params, limit)).fetchall()
    out = []
    for ident, ts, smr, stage, raw in rows:
        try:
            detail = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            detail = {}
        title = execution.LABELS.get(stage[4:], stage) if stage.startswith('NBO_') else EVENTS.get(stage, stage)
        actor = detail.get('actor') or detail.get('user') or '—'      # the app itself; no person is named for it
        out.append(dict(id=ident, ts=ts, smr=smr or '', title=title, actor=actor, stage=stage, detail=detail))
    return out


def detail_text(row):
    from . import jalali
    names = {'action': 'نوع اقدام', 'detail': 'شرح نتیجه', 'note': 'توضیح', 'reason': 'دلیل',
             'revision': 'نسخه پرونده', 'source_status': 'وضعیت مرجع قبل از اجرا',
             'target': 'اقدام مورد نظر', 'mode': 'روش اجرا', 'actor': 'کاربر', 'user': 'کاربر',
             'team': 'تیم', 'before': 'قبل', 'after': 'بعد', 'error': 'خطا'}
    modes = {'rehearsal': 'پیش‌نمایش؛ بدون ثبت واقعی', 'manual': 'ثبت با درخواست کاربر', 'automatic': 'ثبت خودکار'}
    lines = [f"درخواست: {row['smr'] or 'برنامه'}", f"زمان: {jalali.jdatetime(row['ts'])}",
             f"رویداد: {row['title']}", f"کاربر: {row['actor']}"]
    for key, value in row['detail'].items():
        if key in ('actor', 'user'):
            continue
        if key == 'mode':
            value = modes.get(value, value)
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, indent=2)
        lines.append(f"{names.get(key, key)}: {value}")
    return '\n\n'.join(lines)
