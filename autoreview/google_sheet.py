"""Direct Sheets API, own spreadsheet only. Atomic batches and durable client retries.

All input strings use stringValue/RAW, never executable formulas. One writer per
workbook; human commands remain separate from the app-owned Workflow projection.
"""
import json
import logging
import time
import uuid
from datetime import datetime
from urllib.parse import quote

import httpx
from google.oauth2 import service_account
from google.auth.transport.requests import Request

from . import google_credentials
from .store import now

log = logging.getLogger("autoreview.sheet")

WORKFLOW_HEAD = ['کد درخواست','مسیر','وب‌سایت','دسته‌بندی','وضعیت مرجع NBO','پیشنهاد موتور','نظر Online','بررسی‌کننده Online','توضیح Online','نظر Instore','بررسی‌کننده Instore','توضیح Instore','وضعیت گردش کار','نسخه پرونده','آخرین تغییر','دلیل پیشنهادی','شناسه دستگاه','ترتیب']
COMMAND_HEAD = ['شناسه فرمان','کد درخواست','تیم','تصمیم','کد دلیل NBO','توضیح / مرجع بررسی','نام بررسی‌کننده','نسخه پرونده','ارسال؟','وضعیت پردازش','پیام','زمان پردازش','درخواست ثبت‌شده']
EVENT_HEAD = ['شناسه رویداد','زمان','کد درخواست','نوع رویداد','کاربر','نسخه','جزئیات']
RESULT_HEAD = ['SMR','Site','Category','Decision','Reason code (NBO)','Reason label (NBO)','Notes','Checked at','Batch','Evidence']
MANUAL_HEAD = ['SMR','Site','Why manual','What to check by hand','Assigned to','Checked at','Resolved?','Resolution']
LABELS = {'APPROVE':'تأیید','EDIT':'نیاز به اصلاح','CANCEL':'لغو','MANUAL':'بررسی دستی','REOPEN':'بازگشایی'}

# Online + Instore requests, inside the OWNER'S sheet (owner 2026-10-01: the flow moves into his sheet; the teams' own shared
# sheets are never touched). Columns 0-6 and 12-13 belong to the app; 7-11 are the Instore team's and the app never writes
# them - it only reads them (workflow.sheet_verdict). Same result words as the team's old Online-Instore dropdown.
OI_TAB = 'Online + Instore'
OI_HEAD = ['کد درخواست','وب‌سایت','دسته‌بندی','تاریخ بررسی Online','نتیجه Online','دلایل Online','بررسی‌کننده Online',
           'تاریخ بررسی Instore','نتیجه Instore','دلیل Instore (برچسب NBO)','توضیح Instore','بررسی‌کننده Instore',
           'وضعیت','نسخه پرونده','ترتیب']
OI_TEAM_COLS = range(7, 12)
OI_RESULTS = ['تایید قرارداد', 'نیاز به ادیت', 'لغو قرارداد', 'بررسی دستی']
EXEC_TAB = 'Execution'
EXEC_HEAD = ['کد درخواست','نسخه پرونده','نتیجه اجرا','زمان','توضیح']
UPD_TAB = 'Updates'                     # releases the owner publishes; every app updates itself from here (updates.py)
UPD_HEAD = ['نسخه','لینک دانلود','SHA-256','توضیحات','تاریخ انتشار','منتشرکننده']
# Legal (company) registrations from CRM (owner 2026-10-02): the app only lists them; a person checks them by hand in
# columns 7-9, which the app never writes.
LEGAL_TAB = 'Legal'
LEGAL_HEAD = ['کد درخواست CRM','تاریخ ایجاد','نام تجاری','وب‌سایت','وضعیت CRM','آخرین تغییر در CRM','اضافه شده در',
              'نتیجه بررسی Legal','توضیح','بررسی‌کننده','ترتیب']
LEGAL_APP_COLS = 7
# The readable log (sheet_log.py) and a short guide to the tabs (owner 2026-10-03: "the sheet is not clear at all",
# "the log in the sheet must be recorded exactly").
# One log per path (owner: "the Online log apart, the Online + Instore log apart") and where each thing was done -
# in this app or outside it, straight in NBO.
LOG_TAB, LOG_BOTH_TAB = 'لاگ Online', 'لاگ Online + Instore'
LOG_TABS = {'online': LOG_TAB, 'both': LOG_BOTH_TAB}
LOG_HEAD = ['زمان', 'کد درخواست', 'سایت', 'چه شد', 'نتیجه', 'از کجا', 'چه کسی', 'دلیل / توضیح', 'شناسه']
GUIDE_TAB = 'راهنما'
GUIDE_HEAD = ['بخش', 'توضیح']
GUIDE_ROWS = [
    ['این شیت چیست', 'دفترِ کارِ AutoReview: اپ همه‌چیز را اینجا ثبت می‌کند. تیم Online در خود اپ کار می‌کند؛ در این شیت فقط '
                     'تیم Instore (ستون‌های آبیِ تب «Online + Instore») و همکار Legal (سه ستون آخر تب «Legal») چیزی می‌نویسند. '
                     'بقیه‌ی خانه‌ها قفل است.'],
    ['تب «گزارش»', 'عددها و نمودارها: چه چیزی الان منتظر کیست، و موتور امروز / ۷ روز / ۳۰ روز چه تصمیم‌هایی گرفته. فقط خواندنی.'],
    ['تب «Online + Instore»', 'درخواست‌هایی که هم آنلاین‌اند هم حضوری. ستون «وضعیت» می‌گوید نوبت کیست. تیم Instore فقط ستون‌های '
                              'آبی را پر می‌کند: تاریخ، نتیجه، دلیل، توضیح، نام بررسی‌کننده. ردیف‌هایی که نوبت Instore است بالای فهرست‌اند.'],
    ['تب «Workflow»', 'همه‌ی درخواست‌های باز، با پیشنهاد موتور، نظر Online، نظر Instore و وضعیت. فقط خواندنی.'],
    ['تب‌های «لاگ Online» و «لاگ Online + Instore»', 'هر اتفاق برای هر درخواست، یک ردیف، به ترتیب زمان: بررسی موتور و '
     'نتیجه‌اش، نظر هر تیم، عوض شدن وضعیت در NBO، و هر ثبتی که در NBO انجام شده. ستون «از کجا» می‌گوید کار از همین اپ انجام شده '
     'یا بیرون از اپ (مستقیم در NBO)، و «چه کسی» می‌گوید چه کسی و دستی یا خودکار. درخواست‌های فقط‌آنلاین در لاگ Online و '
     'درخواست‌های آنلاین + حضوری در لاگ دیگر است. برای پیدا کردن یک درخواست: Ctrl+F و کد آن.'],
    ['تب «Execution»', 'آخرین نتیجه‌ی ثبت هر درخواست در NBO: آماده، ارسال‌شده، متوقف، یا تأییدشده در NBO.'],
    ['تب «Legal»', 'درخواست‌های حقوقی CRM برای بررسی دستی. همکار Legal سه ستون آخر را پر می‌کند؛ بررسی‌نشده‌ها بالای فهرست‌اند.'],
    ['وضعیت‌ها یعنی چه', 'منتظر Online: موتور هنوز بررسی نکرده • نیازمند بررسی دستی: موتور مطمئن نبود، یک نفر باید نگاه کند • '
                        'نوبت Instore: Online نظر داده، Instore باید نظر بدهد • آماده‌ی تأیید: همه‌ی نظرها کامل است ولی هنوز در NBO '
                        'ثبت نشده • تأییدشده در NBO / بسته‌شده در NBO: کار تمام است.'],
    ['«انجام شده» یعنی چه', 'فقط وقتی کاری «انجام شده» است که وضعیت NBO عوض شده باشد. «آماده» و «پیشنهاد» یعنی هنوز کسی باید '
                           'در اپ «ثبت در NBO» را بزند.'],
]
TABS = {'Workflow': WORKFLOW_HEAD, OI_TAB: OI_HEAD, 'Decisions': COMMAND_HEAD, EXEC_TAB: EXEC_HEAD, 'Audit': EVENT_HEAD,
        'Results': RESULT_HEAD, 'Manual queue': MANUAL_HEAD, UPD_TAB: UPD_HEAD, LEGAL_TAB: LEGAL_HEAD,
        LOG_TAB: LOG_HEAD, LOG_BOTH_TAB: LOG_HEAD, GUIDE_TAB: GUIDE_HEAD}
# Tabs only the app writes: nobody's work lives in their headers, so a header someone typed over is simply put back.
# Stopping the whole sync for it cut the sheet off for an hour over one cell ("X" in the guide's A1, live 2026-10-03).
# The tabs people fill (Online + Instore, Legal, Decisions) and Workflow stay strict: a reshaped one still stops.
APP_OWNED = frozenset({LOG_TAB, LOG_BOTH_TAB, GUIDE_TAB, EXEC_TAB})
REPORT_TAB = 'گزارش'                    # numbers and charts (sheet_report.py): app-owned, free layout, no header contract
OI_KEY, WF_KEY, LEGAL_KEY = OI_HEAD.index('ترتیب'), WORKFLOW_HEAD.index('ترتیب'), LEGAL_HEAD.index('ترتیب')

# ---- order: what really needs someone's action comes first (owner 2026-10-02) ------------------------------------------
# Each people's tab has a hidden 'ترتیب' column; the sheet sorts WHOLE rows by it, so a team's own cells always move with
# their request. The group depends on who reads the tab: Online + Instore is the Instore team's list, Workflow the Online
# team's record (an Online edit / cancel is applied without Instore - only an approval needs both teams).
OI_GROUPS = {'WAIT_INSTORE': 1, 'CONFLICT': 2, 'MANUAL': 3, 'READY': 4, 'EDIT': 4, 'CANCEL': 4, 'WAIT_ONLINE': 5,
             'DONE_APPROVED': 8, 'DONE_CLOSED': 8, 'OUT_OF_SCOPE': 9}
WF_GROUPS = {'CONFLICT': 1, 'MANUAL': 2, 'READY': 3, 'EDIT': 3, 'CANCEL': 3, 'WAIT_INSTORE': 4, 'WAIT_ONLINE': 5,
             'DONE_APPROVED': 8, 'DONE_CLOSED': 8, 'OUT_OF_SCOPE': 9}
# what the Instore team reads in 'وضعیت': whose turn it is, in the app's own states
OI_STATE_TEXT = {
    'WAIT_INSTORE': 'نوبت Instore: نظرتان را ثبت کنید', 'CONFLICT': 'اختلاف نظر دو تیم؛ منتظر تصمیم',
    'MANUAL': 'نیازمند بررسی دستی تیم Online', 'WAIT_ONLINE': 'منتظر نظر Online',
    'READY': 'تأیید هر دو تیم؛ آماده‌ی اعمال در NBO', 'EDIT': 'نیاز به اصلاح؛ آماده‌ی اعمال در NBO',
    'CANCEL': 'لغو؛ آماده‌ی اعمال در NBO', 'DONE_APPROVED': 'تأییدشده در NBO', 'DONE_CLOSED': 'بسته‌شده در NBO',
    'OUT_OF_SCOPE': 'خارج از صف'}
# CRM statuses after which a Legal check changes nothing any more (the request was cancelled or is registered)
CRM_CLOSED = frozenset({'لغو درخواست', 'ثبت نام انجام شده', 'درحال فعال سازی فنی', 'اتمام فعال سازی فنی'})
SORT_EVERY = {OI_TAB: 600, 'Workflow': 300, LEGAL_TAB: 600}     # seconds; people type in Online + Instore and Legal
_SORTED = {}                                                    # (sheet id, tab) -> when it was last sorted


def _epoch(when):
    try:
        return int(datetime.fromisoformat(str(when).replace('Z', '+00:00')).timestamp())
    except (TypeError, ValueError):
        return 0


def order_key(group, when, newest_first=False):
    """Text that sorts like (group, time): inside a group the oldest first (or the newest first). Fixed width, so the
    sheet's own A-Z sort puts the rows in exactly this order."""
    t = max(0, min(_epoch(when), 9_999_999_999))
    return f"{group}{(9_999_999_999 - t) if newest_first else t:010d}"


def workflow_key(case):
    group = WF_GROUPS.get(case.get('state') or 'OUT_OF_SCOPE', 9)
    return order_key(group, case.get('updated_at'), newest_first=group >= 8)


def in_order(keys):
    """Rows without a key belong at the bottom."""
    return keys == sorted(keys, key=lambda k: (k == '', k))


def _cell(v):
    if isinstance(v, bool):
        key = 'boolValue'
    elif isinstance(v, (int, float)):
        key = 'numberValue'
    else:
        key, v = 'stringValue', str(v or '')
    return {'userEnteredValue': {key: v}}


def _rgb(hex_colour):
    h = hex_colour.lstrip('#')
    return {'red': int(h[0:2], 16) / 255, 'green': int(h[2:4], 16) / 255, 'blue': int(h[4:6], 16) / 255}


# ---- look (owner 2026-10-02: "pretty and clean") -------------------------------------------------------------------------
# version 2: SnappPay's colours (blue #007DFA, navy) like the app; a new version restyles every sheet once
STYLE_MARK, STYLE_VERSION = 'autoreview_style', '4'
INK, HEAD, TEAM, BAND, SECTION, TABLE_HEAD = '#101828', '#0B1A33', '#0F7C8C', '#F5F8FC', '#E6F2FF', '#D6E8FF'
TURN, CONFLICT_BG, MUTED = '#FFF3D0', '#FDE2E1', '#8A94A6'
ACTION_COLOURS = {'APPROVE': '#1C9553', 'EDIT': '#D99A00', 'CANCEL': '#C93A3A', 'MANUAL': '#6E59A5'}
# column widths in pixels; None = hidden helper column. The team's own columns get a blue header.
TABLE_LOOK = {
    EXEC_TAB: dict(widths=[140, 100, 380, 180, 520], team=None, colours=()),
    LOG_TAB: dict(widths=[140, 130, 200, 290, 170, 190, 200, 460, None], team=None, colours=()),
    LOG_BOTH_TAB: dict(widths=[140, 130, 200, 290, 170, 190, 200, 460, None], team=None, colours=()),
    GUIDE_TAB: dict(widths=[200, 1000], team=None, colours=()),
    OI_TAB: dict(widths=[130, 190, 150, 110, 120, 260, 120, 110, 130, 220, 220, 120, 270, None, None], team=(7, 12),
                 colours=(('1', TURN, None), ('2', CONFLICT_BG, None), ('8', None, MUTED), ('9', None, MUTED))),
    'Workflow': dict(widths=[130, 110, 190, 150, 120, 110, 110, 120, 220, 110, 120, 220, 230, None, 150, 160, None, None],
                     team=None, colours=(('1', CONFLICT_BG, None), ('2', TURN, None), ('8', None, MUTED), ('9', None, MUTED))),
    LEGAL_TAB: dict(widths=[130, 160, 170, 190, 170, 160, 160, 150, 220, 120, None], team=(7, 10),
                    colours=(('2', None, MUTED), ('3', None, MUTED))),
}


def col_letter(n):
    """1 -> A, 27 -> AA."""
    letter = ''
    while n:
        n, r = divmod(n - 1, 26)
        letter = chr(65 + r) + letter
    return letter


LOCK_MARK = 'AutoReview'               # every protection the app owns starts with this (older warning-only ones too)
RETRY_WAITS = (2, 6, 15)          # seconds; tests set this to ()
_CHECKED = {}                     # sheet id -> when ensure_tabs last passed in this process


def forget_checks():
    """The next sync re-checks the tabs and the lock (after the owner changed who may edit)."""
    _CHECKED.clear()


class GoogleSheetError(RuntimeError):
    pass


class TimedRequest(Request):
    def __call__(self, *args, **kwargs):
        kwargs['timeout'] = 20
        return super().__call__(*args, **kwargs)


class Client:
    def __init__(self, sheet_id, info=None, transport=None):
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{25,80}', sheet_id):
            raise ValueError('شناسه شیت نامعتبر است')
        self.sheet_id = sheet_id
        self.base = 'https://sheets.googleapis.com/v4/spreadsheets/' + sheet_id
        self.http = httpx.Client(timeout=30, transport=transport)
        self.credentials = None
        if transport is None:
            self.credentials = service_account.Credentials.from_service_account_info(
                google_credentials.validate(info or google_credentials.load()),
                scopes=['https://www.googleapis.com/auth/spreadsheets'])
        self.meta = None

    def __enter__(self): return self
    def __exit__(self, *_): self.http.close()

    def request(self, method, suffix='', **kwargs):
        headers = {}
        if self.credentials is not None:
            try:
                if not self.credentials.valid:
                    self.credentials.refresh(TimedRequest())
            except Exception:
                raise GoogleSheetError('احراز هویت Google ناموفق بود؛ اینترنت، زمان ویندوز و اعتبار کلید را بررسی کن') from None
            headers['Authorization'] = 'Bearer ' + self.credentials.token
        for wait in RETRY_WAITS + (None,):
            try:
                r = self.http.request(method, self.base + suffix, headers=headers, **kwargs)
            except httpx.HTTPError:
                raise GoogleSheetError('اتصال Google قطع است؛ صف ارسال محفوظ می‌ماند') from None
            # Google's per-minute read quota (429) or a passing server error: wait briefly and try again
            if (r.status_code == 429 or r.status_code >= 500) and wait is not None:
                time.sleep(wait)
                continue
            break
        if r.status_code != 200:
            try: message = r.json().get('error', {}).get('message', '')
            except ValueError: message = ''
            message = ' '.join(str(message).split())[:300]
            if 'has not been used' in message or 'disabled' in message:
                raise GoogleSheetError('Google Sheets API پروژه هنوز فعال نیست')
            if r.status_code in (403,404):
                raise GoogleSheetError('به شیت دسترسی نداریم؛ Service Account را روی همین شیت با نقش Editor اضافه کن')
            if r.status_code == 429 or r.status_code >= 500:
                raise GoogleSheetError('Google موقتاً پاسخ نمی‌دهد؛ بعداً دوباره تلاش می‌شود')
            log.warning("Google Sheets %s: %s", r.status_code, message[:400])
            detail = f': {message}' if message else ''
            raise GoogleSheetError(f'خطای Google Sheets ({r.status_code}){detail}')
        return r.json()

    def metadata(self):
        if self.meta is None:
            self.meta = self.request('GET', params={'fields':'spreadsheetId,properties.title,sheets.properties'})
        return self.meta

    def ping(self):
        m = self.metadata()
        return dict(ok=True,version=3,own_workflow=True,sheet_id=m['spreadsheetId'],sheet=m['properties']['title'])

    def sheet(self, name):
        for s in self.metadata()['sheets']:
            if s['properties']['title'] == name: return s['properties']
        raise GoogleSheetError('تب لازم در شیت نیست: ' + name)

    def read(self, name, header):
        """One request per tab: only the contract columns, down to the last filled row (empty rows in between keep their
        place, so a row number always matches the sheet)."""
        self.sheet(name)
        width = len(header)
        rg = "'" + name.replace("'", "''") + f"'!A1:{col_letter(width)}"
        rows = self.request('GET', '/values/' + quote(rg, safe=''), params={'valueRenderOption': 'UNFORMATTED_VALUE'}).get('values', [])
        values = [(list(r) + [''] * width)[:width] for r in rows]
        if (not values or values[0] != header) and name in APP_OWNED:
            self.batch([self.update(name, 0, list(header))])       # the app's own tab: its header is restored
            values = [list(header)] + values[1:]
        if not values or values[0] != header:
            raise GoogleSheetError('ستون‌های تب تغییر کرده: ' + name)
        while len(values) > 1 and not any(v != '' for v in values[-1]):
            values.pop()
        return values[1:]

    def column(self, name, first_header):
        """Only column A of a tab (e.g. the Audit event ids): a tab that only grows is never read in full width."""
        rg = "'" + name.replace("'", "''") + "'!A1:A"
        rows = self.request('GET', '/values/' + quote(rg, safe=''), params={'valueRenderOption': 'UNFORMATTED_VALUE'}).get('values', [])
        values = [str(r[0]) if r else '' for r in rows]
        if (not values or values[0] != first_header) and name in APP_OWNED:
            self.batch([self.update(name, 0, list(TABS[name]))])
            values = [first_header] + values[1:]
        if not values or values[0] != first_header:
            raise GoogleSheetError('ستون‌های تب تغییر کرده: ' + name)
        return values[1:]

    def update(self, name, row, values, col=0):
        return {'updateCells':{'start':{'sheetId':self.sheet(name)['sheetId'],'rowIndex':row,'columnIndex':col},
            'rows':[{'values':[_cell(v) for v in values]}], 'fields':'userEnteredValue'}}

    def sort_if_needed(self, name, keys, key_col):
        """Sorts the tab's WHOLE rows by its order key when they are out of order - at most every SORT_EVERY seconds, as
        people may be working in it. Best effort: the data is already written; a failed sort is simply tried later."""
        mark = (self.sheet_id, name)
        if in_order(keys) or time.monotonic() - _SORTED.get(mark, float('-inf')) < SORT_EVERY.get(name, 600):
            return False
        props = self.sheet(name)
        body = {'requests': [{'sortRange': {
            'range': {'sheetId': props['sheetId'], 'startRowIndex': 1, 'endRowIndex': 1 + len(keys), 'startColumnIndex': 0,
                      'endColumnIndex': props['gridProperties']['columnCount']},
            'sortSpecs': [{'dimensionIndex': key_col, 'sortOrder': 'ASCENDING'}]}}]}
        try:
            self.request('POST', ':batchUpdate', json=body)
        except GoogleSheetError as e:
            log.warning("sorting %s failed: %s", name, e)
            return False
        _SORTED[mark] = time.monotonic()
        return True

    def batch(self, requests):
        if not requests: return
        # Grow grids first in the SAME atomic batch - rows and columns (live 2026-10-02: a Workflow tab made with exactly
        # 17 columns had no room for the new 18th). Never remove existing rows or columns.
        needed,cols={},{}
        for r in requests:
            u=r.get('updateCells',{})
            if u:
                sid=u['start']['sheetId']; needed[sid]=max(needed.get(sid,0),u['start']['rowIndex']+len(u['rows']))
                width=u['start'].get('columnIndex',0)+max((len(x.get('values',[])) for x in u['rows']),default=0)
                cols[sid]=max(cols.get(sid,0),width)
        growth=[]
        for s in self.metadata()['sheets']:
            p=s['properties']; count=p['gridProperties']['rowCount']; target=needed.get(p['sheetId'],0)
            if target>count:
                growth.append({'appendDimension':{'sheetId':p['sheetId'],'dimension':'ROWS','length':max(500,target-count)}})
            have=p['gridProperties'].get('columnCount',0)
            if cols.get(p['sheetId'],0)>have:
                growth.append({'appendDimension':{'sheetId':p['sheetId'],'dimension':'COLUMNS','length':cols[p['sheetId']]-have}})
        self.request('POST',':batchUpdate',json={'requests':growth+requests})
        if growth: self.meta=None

    def sync(self, payload, keys=None):
        """keys: {smr: workflow_key(case)} for every case - rows this round does not rewrite still get the current order."""
        current=self.read('Workflow',WORKFLOW_HEAD)
        event_ids=self.column('Audit',EVENT_HEAD[0])
        device=payload['device_id']
        ids={}
        for i,r in enumerate(current):
            if not r[0]: continue
            if r[0] in ids: raise GoogleSheetError('کد درخواست تکراری در Workflow')
            ids[r[0]]=i
        req=[]
        for case in payload['cases']:
            i=ids.get(case['smr'])
            if i is not None and int(current[i][13])>case['revision']:
                raise GoogleSheetError('نسخه پرونده در شیت جلوتر است؛ بازنویسی متوقف شد')
            o,t,s=case.get('online') or {},case.get('instore') or {},case.get('suggestion') or {}
            row=[case['smr'],'Online + Instore' if case['channel']=='both' else 'Online',case.get('site',''),
                case.get('category',''),case.get('source_status',''),LABELS.get(s.get('action'),''),LABELS.get(o.get('action'),''),
                o.get('actor',''),o.get('note',''),LABELS.get(t.get('action'),''),t.get('actor',''),t.get('note',''),
                case['state_fa'],case['revision'],case['updated_at'],'، '.join(s.get('reason_codes') or []),device,
                workflow_key(case)]
            if i is None: i=len(current); ids[case['smr']]=i; current.append(row)
            else: current[i]=row
            req.append(self.update('Workflow',i+1,row))
        written={c['smr'] for c in payload['cases']}
        for smr,i in ids.items():
            k=(keys or {}).get(smr)
            if k is not None and smr not in written and str(current[i][WF_KEY])!=k:
                req.append(self.update('Workflow',i+1,[k],WF_KEY)); current[i][WF_KEY]=k
        known={e for e in event_ids if e}
        index=len(event_ids)+1
        readable=payload.get('log') or {}                # {event_id: (path, row)} for the people's log tabs - same batch, same dedupe
        log_index={LOG_TABS[k]: len(self.column(LOG_TABS[k],LOG_HEAD[0]))+1 for k in {p for p,_r in readable.values()}}
        for e in payload['events']:
            if e['event_id'] in known: continue
            req.append(self.update('Audit',index,[e['event_id'],e['at'],e['smr'],e['kind'],e['actor'],e['revision'],json.dumps(e['detail'],ensure_ascii=False)]))
            if e['event_id'] in readable:
                path,row=readable[e['event_id']]; tab=LOG_TABS[path]
                req.append(self.update(tab,log_index[tab],row)); log_index[tab]+=1
            known.add(e['event_id']); index+=1
        self.batch(req)
        self.sort_if_needed('Workflow', [str(r[WF_KEY]) for r in current], WF_KEY)
        return dict(ok=True,cases=len(payload['cases']),events=len(payload['events']))

    def commands(self):
        values=self.read('Decisions',COMMAND_HEAD)
        commands=[]; req=[]
        for i,r in enumerate(values):
            if len(commands)>=100: break
            if r[8] is not True or r[9] in ('accepted','rejected'): continue
            if r[12]:
                try: cmd=json.loads(r[12])
                except (ValueError,TypeError): raise GoogleSheetError('درخواست ثبت‌شده خراب است؛ ردیف Decisions را بررسی کن') from None
            else:
                cmd=dict(command_id=uuid.uuid4().hex,smr=str(r[1]).strip(),team=str(r[2]).strip(),action=str(r[3]).strip(),
                    reason=str(r[4]).strip(),note=str(r[5]).strip(),actor=str(r[6]).strip(),revision=r[7])
                req.append(self.update('Decisions',i+1,[cmd['command_id']]))
                req.append(self.update('Decisions',i+1,[json.dumps(cmd,ensure_ascii=False)],12))
            req.append(self.update('Decisions',i+1,['pending'],9)); commands.append(cmd)
        self.batch(req)
        return dict(ok=True,commands=commands)

    def ack(self, receipts):
        values=self.read('Decisions',COMMAND_HEAD); req=[]
        for receipt in receipts:
            matches=[i for i,r in enumerate(values) if r[0]==receipt['command_id']]
            if len(matches)!=1: raise GoogleSheetError('شناسه فرمان در Decisions حذف یا تکراری شده است')
            req.append(self.update('Decisions',matches[0]+1,[receipt['status'],receipt.get('error',''),now()],9))
        self.batch(req)
        return dict(ok=True)

    # ---- structure ----------------------------------------------------------------------------------------------------------
    def header_row(self, name, width):
        rg = "'" + name.replace("'", "''") + f"'!A1:{col_letter(width)}1"
        row = (self.request('GET', '/values/' + quote(rg, safe=''), params={'valueRenderOption': 'UNFORMATTED_VALUE'}).get('values') or [[]])[0]
        row = [str(v) for v in row][:width]
        return row + [''] * (width - len(row))

    def ensure_tabs_once(self, reason_labels=(), editors=()):
        """ensure_tabs at most every half hour per process: the per-sync header checks of read() still guard every write."""
        last = _CHECKED.get(self.sheet_id)
        if last is None or time.monotonic() - last > 1800:
            created = self.ensure_tabs(reason_labels)
            try:
                self.lock(editors)
            except GoogleSheetError as e:
                log.warning("sheet protection not updated: %s", e)
            try:
                self.tidy()
            except GoogleSheetError as e:
                log.warning("sheet tab order not updated: %s", e)
            try:
                self.style()                        # the look only: a failure must never stop the data
            except GoogleSheetError as e:
                log.warning("sheet look not applied: %s", e)
            _CHECKED[self.sheet_id] = time.monotonic()
            return created
        return []

    def ensure_tabs(self, reason_labels=()):
        """Adds every tab the app needs to the owner's sheet, with its header row. Never changes a tab that already has a
        header: a different header stops everything (somebody reshaped it) instead of writing into the wrong columns.
        -> names of the tabs created now."""
        titles = {s['properties']['title'] for s in self.metadata()['sheets']}
        missing = [t for t in TABS if t not in titles]
        adds = [{'addSheet': {'properties': {'title': t, 'rightToLeft': True,
                                             'gridProperties': {'frozenRowCount': 1, 'columnCount': max(26, len(TABS[t]))}}}}
                for t in missing]
        if REPORT_TAB not in titles:
            adds.append({'addSheet': {'properties': {'title': REPORT_TAB, 'rightToLeft': True, 'index': 0,
                                                     'gridProperties': {'rowCount': 80, 'columnCount': 20}}}})
            missing.append(REPORT_TAB)
        if adds:
            self.request('POST', ':batchUpdate', json={'requests': adds})
            self.meta = None
        fresh, extended = [], []
        for name, head in TABS.items():
            current = self.header_row(name, len(head))
            if current == head:
                continue
            filled = len(current)
            while filled and current[filled - 1] == '':
                filled -= 1
            if filled and current[:filled] == head[:filled]:
                extended.append((name, filled))      # a newer app added columns at the end: only their titles are added
                continue
            if any(current) and name in APP_OWNED:
                extended.append((name, 0))                # rewrite the whole header of the app's own tab
                continue
            if any(current):
                raise GoogleSheetError('ستون‌های تب تغییر کرده: ' + name)
            fresh.append(name)
        req = [self.update(name, 0, TABS[name][filled:], filled) for name, filled in extended]
        if not fresh:
            self.batch(req)
            return missing
        for name in fresh:
            sid = self.sheet(name)['sheetId']
            req.append(self.update(name, 0, TABS[name]))
            req.append({'repeatCell': {'range': {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1},
                        'cell': {'userEnteredFormat': {'textFormat': {'bold': True},
                                                       'backgroundColor': {'red': 0.93, 'green': 0.95, 'blue': 0.94}}},
                        'fields': 'userEnteredFormat(textFormat,backgroundColor)'}})
            if name == 'Decisions':
                req += [_list_rule(sid, 2, ['Online', 'Instore']),
                        _list_rule(sid, 3, ['تایید', 'نیاز به اصلاح', 'لغو', 'بررسی دستی', 'بازگشایی']),
                        {'setDataValidation': {'range': _col(sid, 8), 'rule': {'condition': {'type': 'BOOLEAN'}}}}]
            if name == OI_TAB:
                req.append(_list_rule(sid, 8, OI_RESULTS))
                if reason_labels:
                    req.append(_list_rule(sid, 9, list(dict.fromkeys(reason_labels)), strict=False))
        self.batch(req)
        return missing

    # ---- who may edit what ----------------------------------------------------------------------------------------------
    def wanted_locks(self, sheets_meta):
        """Every tab is locked, except the Instore team's cells of Online + Instore (below the header). -> [(sheetId, range)]"""
        out = []
        for s in sheets_meta:
            sid, title = s['properties']['sheetId'], s['properties']['title']
            if title != OI_TAB:
                out.append((sid, {'sheetId': sid}))
                continue
            first, last = min(OI_TEAM_COLS), max(OI_TEAM_COLS) + 1
            out += [(sid, {'sheetId': sid, 'startColumnIndex': 0, 'endColumnIndex': first}),
                    (sid, {'sheetId': sid, 'startColumnIndex': last}),
                    (sid, {'sheetId': sid, 'startRowIndex': 0, 'endRowIndex': 1, 'startColumnIndex': first, 'endColumnIndex': last})]
        return out

    # Owner 2026-10-02: "are all these tabs needed? user friendly - Online works only in the app, the sheet is the record".
    # People see three tabs; the app's bookkeeping tabs (and the old template's) are hidden, never deleted - their data
    # stays, and they can be shown again from the sheet's tab list.
    VISIBLE = (GUIDE_TAB, REPORT_TAB, OI_TAB, 'Workflow', LOG_TAB, LOG_BOTH_TAB, EXEC_TAB, LEGAL_TAB)
    HIDDEN = frozenset(TABS) - set(VISIBLE) | {'Daily summary', 'Reasons', 'Guide'}

    def tidy(self):
        """The people's tabs first and visible, the bookkeeping tabs hidden. -> number of tabs changed."""
        props = {s['properties']['title']: s['properties'] for s in self.metadata()['sheets']}
        req = []
        for index, title in enumerate(t for t in self.VISIBLE if t in props):
            p = props[title]
            if p.get('hidden') or p.get('index') != index:
                req.append({'updateSheetProperties': {'properties': {'sheetId': p['sheetId'], 'hidden': False, 'index': index},
                                                      'fields': 'hidden,index'}})
        for title in self.HIDDEN & set(props):
            if not props[title].get('hidden'):
                req.append({'updateSheetProperties': {'properties': {'sheetId': props[title]['sheetId'], 'hidden': True},
                                                      'fields': 'hidden'}})
        if req:
            self.request('POST', ':batchUpdate', json={'requests': req})
            self.meta = None
        return len(req)

    def lock(self, editors=()):
        """Remove only legacy AutoReview protections; never create editor restrictions."""
        meta = self.request('GET', params={'fields': 'sheets(protectedRanges(protectedRangeId,description))'})
        ours = [p for sh in meta['sheets'] for p in sh.get('protectedRanges', [])
                if (p.get('description') or '').startswith(LOCK_MARK)]
        if not ours:
            return False
        self.batch([{'deleteProtectedRange': {'protectedRangeId': p['protectedRangeId']}} for p in ours])
        return True

    # ---- Online + Instore tab -------------------------------------------------------------------------------------------
    def instore_entries(self):
        """-> [(smr, {result, reason, note, actor, date})] as the Instore team left them."""
        out = []
        for r in self.read(OI_TAB, OI_HEAD):
            smr = str(r[0]).strip()
            if smr:
                out.append((smr, dict(date=r[7], result=r[8], reason=r[9], note=r[10], actor=r[11])))
        return out

    def write_online_instore(self, rows, statuses=None):
        """rows: workflow.sheet_row(case) for every Online + Instore case. Writes only the app's own cells, only when they
        changed; new cases are added at the bottom, then whole rows are sorted so the Instore team's turn comes first
        (OI_GROUPS); no row is ever removed."""
        statuses = statuses or {}
        current = self.read(OI_TAB, OI_HEAD)
        where = {str(r[0]).strip(): i for i, r in enumerate(current) if str(r[0]).strip()}
        keys = [str(r[OI_KEY]) for r in current]
        req, end = [], len(current)
        for row in rows:
            status = statuses.get(row['smr'])
            code = row.get('state_code') or 'OUT_OF_SCOPE'
            group = OI_GROUPS.get(code, 9)
            state = OI_STATE_TEXT.get(code, row['state']) + (f' — {status}' if status else '')
            key = order_key(group, row.get('updated_at'), newest_first=group >= 8)
            left = [row['smr'], row['site'], row['category'], row['online_date'], row['online_result'], row['online_reasons'],
                    row['online_by']]
            right = [state, row['revision'], key]
            i = where.get(row['smr'])
            if i is None:
                i, end = end, end + 1
                where[row['smr']] = i
                keys.append(key)
            else:
                have = current[i]
                keys[i] = key
                if [str(v) for v in have[:7]] == [str(v) for v in left] and [str(v) for v in have[12:15]] == [str(v) for v in right]:
                    continue
            req.append(self.update(OI_TAB, i + 1, left))
            req.append(self.update(OI_TAB, i + 1, right, 12))
        self.batch(req)
        self.sort_if_needed(OI_TAB, keys, OI_KEY)
        return len(req) // 2

    # ---- Legal tab --------------------------------------------------------------------------------------------------------
    def write_legal(self, rows, limit=500):
        """rows: reference.legal_rows(). New requests are added at the bottom, known ones get their CRM status / change time
        refreshed; the Legal team's columns are never written, no row is ever removed. At most `limit` rows per call (the
        first upload of a few thousand finishes over a few rounds). -> rows written."""
        current = self.read(LEGAL_TAB, LEGAL_HEAD)
        where = {str(r[0]).strip(): i for i, r in enumerate(current) if str(r[0]).strip()}
        keys = [str(r[LEGAL_KEY]) for r in current]
        req, end, stamp, added, pending, budget = [], len(current), now(), 0, 0, limit * 6
        for row in rows:
            values = [row['caseid'], row['created_on'], row['brand'], row['site'], row['status'], row['modified_on']]
            i = where.get(row['caseid'])
            checked = i is not None and str(current[i][7]).strip() != ''
            # 1 = not checked and the request still open in CRM, 2 = not checked but already closed in CRM, 3 = checked
            group = 3 if checked else (2 if row['status'] in CRM_CLOSED else 1)
            pending += group == 1
            key = order_key(group, row['created_on'], newest_first=True)
            if i is None:
                if added >= limit or len(req) >= budget:
                    continue
                i, end, added = end, end + 1, added + 1
                where[row['caseid']] = i
                keys.append(key)
                req += [self.update(LEGAL_TAB, i + 1, values + [stamp]), self.update(LEGAL_TAB, i + 1, [key], LEGAL_KEY)]
                continue
            if len(req) >= budget:
                continue                            # the rest next round (the caller comes back sooner while catching up)
            if [str(v) for v in current[i][:6]] != [str(v) for v in values]:
                req.append(self.update(LEGAL_TAB, i + 1, values))
            if keys[i] != key:
                keys[i] = key
                req.append(self.update(LEGAL_TAB, i + 1, [key], LEGAL_KEY))
        self.legal_pending = pending
        self.legal_writes = len(req)
        self.batch(req)
        self.sort_if_needed(LEGAL_TAB, keys, LEGAL_KEY)
        return added

    # ---- report tab ---------------------------------------------------------------------------------------------------------
    def write_report(self, rows):
        """rows: sheet_report.grid() - the whole fixed area is rewritten (values only; the look is set by style())."""
        sid = self.sheet(REPORT_TAB)['sheetId']
        self.batch([{'updateCells': {'start': {'sheetId': sid, 'rowIndex': 0, 'columnIndex': 0},
                                     'rows': [{'values': [_cell(v) for v in r]} for r in rows], 'fields': 'userEnteredValue'}}])

    # ---- look ---------------------------------------------------------------------------------------------------------------
    def style(self):
        """Look of the people's tabs and the report's charts, applied once per STYLE_VERSION (a mark kept in the sheet), so
        a round only reads one small field. The app's own earlier colours / bands / charts on these tabs are replaced.
        -> True when the style was (re)applied."""
        meta = self.request('GET', params={'fields': 'developerMetadata(metadataId,metadataKey,metadataValue),'
                                           'sheets(properties(sheetId,title,gridProperties),conditionalFormats(ranges),'
                                           'bandedRanges(bandedRangeId),charts(chartId))'})
        mark = next((m for m in meta.get('developerMetadata', []) if m.get('metadataKey') == STYLE_MARK), None)
        if mark and mark.get('metadataValue') == STYLE_VERSION:
            return False
        tabs = {s['properties']['title']: s for s in meta.get('sheets', [])}
        req = []
        for title in (GUIDE_TAB, REPORT_TAB, OI_TAB, 'Workflow', LOG_TAB, LOG_BOTH_TAB, EXEC_TAB, LEGAL_TAB):
            s = tabs.get(title)
            if not s:
                continue
            sid = s['properties']['sheetId']
            req += [{'deleteConditionalFormatRule': {'sheetId': sid, 'index': i}}
                    for i in range(len(s.get('conditionalFormats', [])) - 1, -1, -1)]
            req += [{'deleteBanding': {'bandedRangeId': b['bandedRangeId']}} for b in s.get('bandedRanges', [])]
            if title == REPORT_TAB:
                req += [{'deleteEmbeddedObject': {'objectId': c['chartId']}} for c in s.get('charts', [])]
                req += self._report_style(sid)
            else:
                req += self._table_style(title, sid, s['properties'])
        if mark:
            req.append({'updateDeveloperMetadata': {
                'dataFilters': [{'developerMetadataLookup': {'metadataId': mark['metadataId']}}],
                'developerMetadata': {'metadataValue': STYLE_VERSION}, 'fields': 'metadataValue'}})
        else:
            req.append({'createDeveloperMetadata': {'developerMetadata': {
                'metadataKey': STYLE_MARK, 'metadataValue': STYLE_VERSION, 'location': {'spreadsheet': True},
                'visibility': 'DOCUMENT'}}})
        if GUIDE_TAB in tabs:                             # the guide's text and wrapping go with the look
            gid = tabs[GUIDE_TAB]['properties']['sheetId']
            req.append({'updateCells': {'start': {'sheetId': gid, 'rowIndex': 1, 'columnIndex': 0},
                                        'rows': [{'values': [_cell(v) for v in r]} for r in GUIDE_ROWS], 'fields': 'userEnteredValue'}})
            req.append(self._fmt(gid, 1, len(GUIDE_ROWS) + 1, 0, 2, {'wrapStrategy': 'WRAP', 'verticalAlignment': 'TOP'},
                                 'wrapStrategy,verticalAlignment'))
            req.append(self._fmt(gid, 1, len(GUIDE_ROWS) + 1, 0, 1, {'textFormat': {'bold': True}}, 'textFormat'))
        self.request('POST', ':batchUpdate', json={'requests': req})
        self.meta = None
        return True

    @staticmethod
    def _fmt(sid, r0, r1, c0, c1, fmt, fields):
        rng = {'sheetId': sid, 'startRowIndex': r0, 'startColumnIndex': c0, 'endColumnIndex': c1}
        if r1 is not None:
            rng['endRowIndex'] = r1
        return {'repeatCell': {'range': rng, 'cell': {'userEnteredFormat': fmt}, 'fields': 'userEnteredFormat(' + fields + ')'}}

    def _table_style(self, title, sid, props):
        look = TABLE_LOOK[title]
        widths = look['widths']
        width = len(widths)
        key_letter = col_letter({OI_TAB: OI_KEY, 'Workflow': WF_KEY, LEGAL_TAB: LEGAL_KEY}.get(title, 0) + 1)
        head = {'backgroundColor': _rgb(HEAD), 'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE', 'wrapStrategy': 'WRAP',
                'textFormat': {'bold': True, 'fontSize': 10, 'foregroundColor': _rgb('#FFFFFF')}}
        fields = 'backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat'
        req = [self._fmt(sid, 0, 1, 0, width, head, fields),
               self._fmt(sid, 1, None, 0, width, {'verticalAlignment': 'MIDDLE', 'wrapStrategy': 'CLIP',
                                                  'textFormat': {'fontSize': 10, 'foregroundColor': _rgb(INK)}},
                         'verticalAlignment,wrapStrategy,textFormat'),
               {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'ROWS', 'startIndex': 0, 'endIndex': 1},
                                              'properties': {'pixelSize': 44}, 'fields': 'pixelSize'}},
               {'updateSheetProperties': {'properties': {'sheetId': sid, 'gridProperties': {'frozenRowCount': 1, 'frozenColumnCount': 1}},
                                          'fields': 'gridProperties.frozenRowCount,gridProperties.frozenColumnCount'}},
               {'addBanding': {'bandedRange': {'range': {'sheetId': sid, 'startRowIndex': 0, 'startColumnIndex': 0, 'endColumnIndex': width},
                                               'rowProperties': {'headerColor': _rgb(HEAD), 'firstBandColor': _rgb('#FFFFFF'),
                                                                 'secondBandColor': _rgb(BAND)}}}}]
        notes = {
            OI_TAB: {7: 'ورودی تیم Instore: تاریخ بررسی خودتان را ثبت کنید.',
                     8: 'ورودی تیم Instore: نتیجه را از فهرست انتخاب کنید. این نظر به معنی ثبت نهایی در NBO نیست.',
                     9: 'برای اصلاح یا لغو، دلیل معتبر NBO را انتخاب کنید.',
                     10: 'توضیح بررسی و مرجع تصمیم خودتان را بنویسید.',
                     11: 'نام بررسی‌کننده Instore را ثبت کنید.',
                     12: 'نوبت Instore یعنی اقدام شما لازم است. آمادهٔ اعمال یعنی هنوز ثبت نهایی انجام نشده. نتیجهٔ ارسال در Execution است.'},
            'Workflow': {12: 'آماده یعنی منتظر ثبت در NBO؛ انجام‌شده فقط وقتی وضعیت NBO تأیید شده باشد. نتیجهٔ ارسال را در Execution ببینید.'},
            EXEC_TAB: {2: 'پیش‌نمایش: هیچ تغییر واقعی ثبت نشده. ارسال‌شده: منتظر تطبیق با NBO. نتیجه نامشخص: پیش از تکرار، NBO را بررسی کنید.',
                       4: 'شرح نتیجهٔ آخرین اجرا؛ تاریخچهٔ کامل رویدادها در برنامه، صفحهٔ سابقه و جزئیات عملیات است.'},
        }.get(title, {})
        for column, note in notes.items():
            req.append({'updateCells': {'start': {'sheetId': sid, 'rowIndex': 0, 'columnIndex': column},
                                       'rows': [{'values': [{'note': note}]}], 'fields': 'note'}})
        if look['team']:
            req.append(self._fmt(sid, 0, 1, look['team'][0], look['team'][1], dict(head, backgroundColor=_rgb(TEAM)), fields))
        for i, w in enumerate(widths):
            props_ = {'hiddenByUser': w is None} if w is None else {'pixelSize': w, 'hiddenByUser': False}
            req.append({'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': i, 'endIndex': i + 1},
                                                      'properties': props_, 'fields': ','.join(props_)}})
        total = props.get('gridProperties', {}).get('columnCount', width)
        if total > width:                                   # the empty columns after the table
            req.append({'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': width, 'endIndex': total},
                                                      'properties': {'hiddenByUser': True}, 'fields': 'hiddenByUser'}})
        for n, (group, background, text) in enumerate(look['colours']):
            fmt = {}
            if background:
                fmt['backgroundColor'] = _rgb(background)
            if text:
                fmt['textFormat'] = {'foregroundColor': _rgb(text)}
            req.append({'addConditionalFormatRule': {'index': n, 'rule': {
                'ranges': [{'sheetId': sid, 'startRowIndex': 1, 'startColumnIndex': 0, 'endColumnIndex': width}],
                'booleanRule': {'condition': {'type': 'CUSTOM_FORMULA',
                                              'values': [{'userEnteredValue': f'=LEFT(${key_letter}2,1)="{group}"'}]},
                                'format': fmt}}}})
        return req

    def _report_style(self, sid):
        from .sheet_report import DAYS, ROW, TOP, WIDTH
        f = self._fmt
        text = lambda size, colour=INK, bold=False: {'fontSize': size, 'bold': bold, 'foregroundColor': _rgb(colour)}
        req = [{'updateSheetProperties': {'properties': {'sheetId': sid, 'gridProperties': {'hideGridlines': True, 'frozenRowCount': 0}},
                                          'fields': 'gridProperties.hideGridlines,gridProperties.frozenRowCount'}},
               {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 1},
                                              'properties': {'pixelSize': 250}, 'fields': 'pixelSize'}},
               {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 1, 'endIndex': WIDTH},
                                              'properties': {'pixelSize': 122}, 'fields': 'pixelSize'}},
               {'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': WIDTH, 'endIndex': WIDTH + 1},
                                              'properties': {'pixelSize': 28}, 'fields': 'pixelSize'}},
               f(sid, 0, ROW['note'] + 1, 0, WIDTH, {'verticalAlignment': 'MIDDLE', 'textFormat': text(10)}, 'verticalAlignment,textFormat'),
               f(sid, ROW['title'], ROW['title'] + 1, 0, 1, {'textFormat': text(18, HEAD, True)}, 'textFormat'),
               f(sid, ROW['updated'], ROW['updated'] + 1, 0, 1, {'textFormat': text(9, MUTED)}, 'textFormat'),
               f(sid, ROW['note'], ROW['note'] + 1, 0, 1, {'textFormat': text(9, MUTED)}, 'textFormat'),
               f(sid, ROW['now_values'], ROW['now_values'] + 1, 0, 6,
                 {'horizontalAlignment': 'CENTER', 'textFormat': text(22, HEAD, True)}, 'horizontalAlignment,textFormat'),
               f(sid, ROW['now_labels'], ROW['now_labels'] + 1, 0, 6,
                 {'horizontalAlignment': 'CENTER', 'backgroundColor': _rgb(TABLE_HEAD), 'textFormat': text(10, INK, True)},
                 'horizontalAlignment,backgroundColor,textFormat')]
        for key in ('now_head', 'perf_head', 'states_head', 'daily_head', 'reasons_head', 'manual_head'):
            req.append(f(sid, ROW[key], ROW[key] + 1, 0, WIDTH, {'backgroundColor': _rgb(SECTION), 'textFormat': text(12, HEAD, True)},
                         'backgroundColor,textFormat'))
        for key, cols, rows in (('perf_cols', WIDTH, 3), ('states_cols', 2, 7), ('daily_cols', 5, DAYS),
                                ('reasons_cols', 2, TOP), ('manual_cols', 2, TOP)):
            req.append(f(sid, ROW[key], ROW[key] + 1, 0, cols,
                         {'horizontalAlignment': 'CENTER', 'backgroundColor': _rgb(TABLE_HEAD), 'textFormat': text(10, INK, True)},
                         'horizontalAlignment,backgroundColor,textFormat'))
            req.append(f(sid, ROW[key] + 1, ROW[key] + 1 + rows, 1, cols, {'horizontalAlignment': 'CENTER'}, 'horizontalAlignment'))

        def rng(r0, r1, c):
            return {'sheetId': sid, 'startRowIndex': r0, 'endRowIndex': r1, 'startColumnIndex': c, 'endColumnIndex': c + 1}

        def place(row):
            return {'overlayPosition': {'anchorCell': {'sheetId': sid, 'rowIndex': row, 'columnIndex': WIDTH + 1},
                                        'widthPixels': 560, 'heightPixels': 320}}

        def bars(title, r_head, rows, colour, at):
            return {'addChart': {'chart': {'position': place(at), 'spec': {'title': title, 'basicChart': {
                'chartType': 'BAR', 'legendPosition': 'NO_LEGEND', 'headerCount': 1,
                'axis': [{'position': 'BOTTOM_AXIS'}, {'position': 'LEFT_AXIS'}],
                'domains': [{'domain': {'sourceRange': {'sources': [rng(r_head, r_head + 1 + rows, 0)]}}}],
                'series': [{'series': {'sourceRange': {'sources': [rng(r_head, r_head + 1 + rows, 1)]}}, 'targetAxis': 'BOTTOM_AXIS',
                            'colorStyle': {'rgbColor': _rgb(colour)}}]}}}}}

        states = ROW['states_rows']
        # four charts beside the tables, 17 rows apart (a 320 px chart is about 15 rows high)
        req.append({'addChart': {'chart': {'position': place(3), 'spec': {
            'title': 'درخواست‌های باز بر اساس وضعیت', 'pieChart': {
                'legendPosition': 'RIGHT_LEGEND', 'pieHole': 0.45,
                'domain': {'sourceRange': {'sources': [rng(states, states + 7, 0)]}},
                'series': {'sourceRange': {'sources': [rng(states, states + 7, 1)]}}}}}}})
        daily = ROW['daily_cols']
        req.append({'addChart': {'chart': {'position': place(20), 'spec': {
            'title': f'تصمیم‌های روزانه‌ی موتور ({DAYS} روز)', 'basicChart': {
                'chartType': 'COLUMN', 'stackedType': 'STACKED', 'legendPosition': 'BOTTOM_LEGEND', 'headerCount': 1,
                'axis': [{'position': 'BOTTOM_AXIS'}, {'position': 'LEFT_AXIS'}],
                'domains': [{'domain': {'sourceRange': {'sources': [rng(daily, daily + 1 + DAYS, 0)]}}}],
                'series': [{'series': {'sourceRange': {'sources': [rng(daily, daily + 1 + DAYS, c)]}}, 'targetAxis': 'LEFT_AXIS',
                            'colorStyle': {'rgbColor': _rgb(ACTION_COLOURS[a])}}
                           for c, a in enumerate(('APPROVE', 'EDIT', 'CANCEL', 'MANUAL'), start=1)]}}}}})
        req.append(bars('پرتکرارترین دلیل‌های اصلاح و لغو (۳۰ روز)', ROW['reasons_cols'], TOP, ACTION_COLOURS['EDIT'], 37))
        req.append(bars('علت‌های بررسی دستی (۳۰ روز)', ROW['manual_cols'], TOP, ACTION_COLOURS['MANUAL'], 54))
        return req

    def append_log(self, rows):
        """rows: [(path 'online' | 'both', row)] for the people's log tabs, each added under its tab's last row.
        -> number written."""
        if not rows:
            return 0
        at = {LOG_TABS[k]: len(self.column(LOG_TABS[k], LOG_HEAD[0])) + 1 for k in {p for p, _r in rows}}
        req = []
        for path, row in rows:
            tab = LOG_TABS[path]
            req.append(self.update(tab, at[tab], row))
            at[tab] += 1
        self.batch(req)
        return len(rows)

    # ---- NBO execution receipts -----------------------------------------------------------------------------------------
    def upsert_execution(self, records):
        """records: execution.records() -> one row per (request, revision), updated in place when its state changes."""
        current = self.read(EXEC_TAB, EXEC_HEAD)
        where = {(str(r[0]), str(r[1])): i for i, r in enumerate(current) if r[0]}
        req, end = [], len(current)
        for rec in records:
            values = [rec['smr'], rec['revision'], rec['label'], rec['updated_at'], rec['detail']]
            key = (str(rec['smr']), str(rec['revision']))
            i = where.get(key)
            if i is None:
                i, end = end, end + 1
                where[key] = i
            elif [str(v) for v in current[i]] == [str(v) for v in values]:
                continue
            req.append(self.update(EXEC_TAB, i + 1, values))
        self.batch(req)
        return len(req)

    # ---- releases -------------------------------------------------------------------------------------------------------
    def releases(self):
        return [dict(version=str(r[0]).strip(), url=str(r[1]).strip(), sha256=str(r[2]).strip(), notes=str(r[3]),
                     published_at=str(r[4])) for r in self.read(UPD_TAB, UPD_HEAD) if str(r[0]).strip()]

    def publish_release(self, version, url, sha256, notes, by):
        rows = self.read(UPD_TAB, UPD_HEAD)
        if any(str(r[0]).strip() == version for r in rows):
            raise GoogleSheetError(f'نسخه‌ی {version} قبلاً منتشر شده؛ شماره‌ی نسخه را بالا ببر')
        self.batch([self.update(UPD_TAB, len(rows) + 1, [version, url, sha256, notes, now(), by])])

    def append_run(self, run_id, source_rows):
        results=self.read('Results',RESULT_HEAD); manual=self.read('Manual queue',MANUAL_HEAD)
        known={(r[8],r[0]) for r in results}; known_manual={(r[0],r[5]) for r in manual}
        req=[]; added=0
        for row in source_rows:
            at=row.get('decided_at','')
            if (run_id,row['smr']) not in known:
                result=[row['smr'],row['site'],row['category'],row['action_fa'],row['codes'],row['reasons_fa'],row['notes_fa'],at,run_id,'']
                req.append(self.update('Results',len(results)+1,result)); results.append(result); added+=1
                known.add((run_id,row['smr']))
            if row['action']=='MANUAL' and (row['smr'],at) not in known_manual:
                result=[row['smr'],row['site'],row['notes_fa'],'تصمیم را در Decisions یا اپ ثبت کنید','',at,False,'']
                req.append(self.update('Manual queue',len(manual)+1,result)); manual.append(result); known_manual.add((row['smr'],at))
        self.batch(req)
        return dict(ok=True,appended=added,duplicate=not added)


def _col(sheet_id, col):
    return {'sheetId': sheet_id, 'startRowIndex': 1, 'startColumnIndex': col, 'endColumnIndex': col + 1}


def _list_rule(sheet_id, col, values, strict=True):
    return {'setDataValidation': {'range': _col(sheet_id, col), 'rule': {
        'condition': {'type': 'ONE_OF_LIST', 'values': [{'userEnteredValue': v} for v in values]},
        'strict': strict, 'showCustomUi': True}}}
