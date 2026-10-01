"""Direct Sheets API, own spreadsheet only. Atomic batches and durable client retries.

All input strings use stringValue/RAW, never executable formulas. One writer per
workbook; human commands remain separate from the app-owned Workflow projection.
"""
import json
import uuid
from urllib.parse import quote

import httpx
from google.oauth2 import service_account
from google.auth.transport.requests import Request

from . import google_credentials
from .store import now

WORKFLOW_HEAD = ['کد درخواست','مسیر','وب‌سایت','دسته‌بندی','وضعیت مرجع NBO','پیشنهاد موتور','نظر Online','بررسی‌کننده Online','توضیح Online','نظر Instore','بررسی‌کننده Instore','توضیح Instore','وضعیت گردش کار','نسخه پرونده','آخرین تغییر','دلیل پیشنهادی','شناسه دستگاه']
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
           'وضعیت','نسخه پرونده']
OI_TEAM_COLS = range(7, 12)
OI_RESULTS = ['تایید قرارداد', 'نیاز به ادیت', 'لغو قرارداد', 'بررسی دستی']
EXEC_TAB = 'Execution'
EXEC_HEAD = ['کد درخواست','نسخه پرونده','نتیجه اجرا','زمان','توضیح']
UPD_TAB = 'Updates'                     # releases the owner publishes; every app updates itself from here (updates.py)
UPD_HEAD = ['نسخه','لینک دانلود','SHA-256','توضیحات','تاریخ انتشار','منتشرکننده']
TABS = {'Workflow': WORKFLOW_HEAD, OI_TAB: OI_HEAD, 'Decisions': COMMAND_HEAD, EXEC_TAB: EXEC_HEAD, 'Audit': EVENT_HEAD,
        'Results': RESULT_HEAD, 'Manual queue': MANUAL_HEAD, UPD_TAB: UPD_HEAD}


def col_letter(n):
    """1 -> A, 27 -> AA."""
    letter = ''
    while n:
        n, r = divmod(n - 1, 26)
        letter = chr(65 + r) + letter
    return letter


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
        try:
            r = self.http.request(method, self.base + suffix, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise GoogleSheetError('اتصال Google قطع است؛ صف ارسال محفوظ می‌ماند') from None
        if r.status_code != 200:
            try: message = r.json().get('error', {}).get('message', '')
            except ValueError: message = ''
            if 'has not been used' in message or 'disabled' in message:
                raise GoogleSheetError('Google Sheets API پروژه هنوز فعال نیست')
            if r.status_code in (403,404):
                raise GoogleSheetError('به شیت دسترسی نداریم؛ Service Account را روی همین شیت با نقش Editor اضافه کن')
            if r.status_code == 429 or r.status_code >= 500:
                raise GoogleSheetError('Google موقتاً پاسخ نمی‌دهد؛ بعداً دوباره تلاش می‌شود')
            raise GoogleSheetError(f'خطای Google Sheets ({r.status_code})؛ اطلاعات اتصال یا ساختار شیت را بررسی کن')
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
        prop = self.sheet(name)
        width = len(header)
        letter = ''
        n = width
        while n: n, r = divmod(n-1,26); letter = chr(65+r)+letter
        end = prop['gridProperties']['rowCount']
        # Bounded pages; only contract columns, never unrelated cells.
        values=[]
        for start in range(1,end+1,1000):
            rg = "'" + name.replace("'", "''") + f"'!A{start}:{letter}{min(start+999,end)}"
            chunk = self.request('GET','/values/'+quote(rg,safe=''),params={'valueRenderOption':'UNFORMATTED_VALUE'}).get('values',[])
            values.extend([list(r)+['']*(width-len(r)) for r in chunk])
            # Keep physical row positions even across blank pages.
            values.extend([['']*width for _ in range(min(1000,end-start+1)-len(chunk))])
        if not values or values[0] != header:
            raise GoogleSheetError('ستون‌های تب تغییر کرده: ' + name)
        while len(values)>1 and not any(v != '' for v in values[-1]): values.pop()
        return values[1:]

    def update(self, name, row, values, col=0):
        def cell(v):
            if isinstance(v,bool): key='boolValue'
            elif isinstance(v,(int,float)): key='numberValue'
            else: key='stringValue'; v=str(v or '')
            return {'userEnteredValue':{key:v}}
        return {'updateCells':{'start':{'sheetId':self.sheet(name)['sheetId'],'rowIndex':row,'columnIndex':col},
            'rows':[{'values':[cell(v) for v in values]}], 'fields':'userEnteredValue'}}

    def batch(self, requests):
        if not requests: return
        # Grow grids first in the SAME atomic batch. Never remove existing rows.
        needed={}
        for r in requests:
            u=r.get('updateCells',{})
            if u:
                sid=u['start']['sheetId']; needed[sid]=max(needed.get(sid,0),u['start']['rowIndex']+len(u['rows']))
        growth=[]
        for s in self.metadata()['sheets']:
            p=s['properties']; count=p['gridProperties']['rowCount']; target=needed.get(p['sheetId'],0)
            if target>count:
                growth.append({'appendDimension':{'sheetId':p['sheetId'],'dimension':'ROWS','length':max(500,target-count)}})
        self.request('POST',':batchUpdate',json={'requests':growth+requests})
        if growth: self.meta=None

    def sync(self, payload):
        current=self.read('Workflow',WORKFLOW_HEAD)
        events=self.read('Audit',EVENT_HEAD)
        device=payload['device_id']
        existing_devices={str(r[16]) for r in current if r[0] and r[16]}
        if existing_devices - {device}:
            raise GoogleSheetError('شیت به دستگاه دیگری متصل است؛ برای انتقال، اتصال قبلی باید بررسی شود')
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
                case['state_fa'],case['revision'],case['updated_at'],'، '.join(s.get('reason_codes') or []),device]
            if i is None: i=len(current); ids[case['smr']]=i; current.append(row)
            req.append(self.update('Workflow',i+1,row))
        known={r[0] for r in events if r[0]}
        index=len(events)+1
        for e in payload['events']:
            if e['event_id'] in known: continue
            req.append(self.update('Audit',index,[e['event_id'],e['at'],e['smr'],e['kind'],e['actor'],e['revision'],json.dumps(e['detail'],ensure_ascii=False)]))
            known.add(e['event_id']); index+=1
        self.batch(req)
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

    def ensure_tabs(self, reason_labels=()):
        """Adds every tab the app needs to the owner's sheet, with its header row. Never changes a tab that already has a
        header: a different header stops everything (somebody reshaped it) instead of writing into the wrong columns.
        -> names of the tabs created now."""
        titles = {s['properties']['title'] for s in self.metadata()['sheets']}
        missing = [t for t in TABS if t not in titles]
        if missing:
            self.request('POST', ':batchUpdate', json={'requests': [
                {'addSheet': {'properties': {'title': t, 'rightToLeft': True,
                                             'gridProperties': {'frozenRowCount': 1, 'columnCount': max(26, len(TABS[t]))}}}}
                for t in missing]})
            self.meta = None
        fresh = []
        for name, head in TABS.items():
            current = self.header_row(name, len(head))
            if current == head:
                continue
            if any(current):
                raise GoogleSheetError('ستون‌های تب تغییر کرده: ' + name)
            fresh.append(name)
        if not fresh:
            return missing
        req = []
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
                for a, b in ((0, 7), (12, 14)):                 # the app's columns: a warning before a person types over them
                    req.append({'addProtectedRange': {'protectedRange': {
                        'range': {'sheetId': sid, 'startColumnIndex': a, 'endColumnIndex': b}, 'warningOnly': True,
                        'description': 'AutoReview این ستون‌ها را پر می‌کند'}}})
        self.batch(req)
        return missing

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
        changed; new cases are added at the bottom; no row is ever removed."""
        statuses = statuses or {}
        current = self.read(OI_TAB, OI_HEAD)
        where = {str(r[0]).strip(): i for i, r in enumerate(current) if str(r[0]).strip()}
        req, end = [], len(current)
        for row in rows:
            status = statuses.get(row['smr'])
            state = row['state'] + (f' — {status}' if status else '')
            left = [row['smr'], row['site'], row['category'], row['online_date'], row['online_result'], row['online_reasons'],
                    row['online_by']]
            right = [state, row['revision']]
            i = where.get(row['smr'])
            if i is None:
                i, end = end, end + 1
                where[row['smr']] = i
            else:
                have = current[i]
                if [str(v) for v in have[:7]] == [str(v) for v in left] and [str(v) for v in have[12:14]] == [str(v) for v in right]:
                    continue
            req.append(self.update(OI_TAB, i + 1, left))
            req.append(self.update(OI_TAB, i + 1, right, 12))
        self.batch(req)
        return len(req) // 2

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
