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
