// LEGACY REFERENCE ONLY — shared Workspace runtime uses workspace_google.py.
// AutoReview v3. Bound to the owner's spreadsheet. Never opens another spreadsheet.
const SECRET = '__SECRET__';
const EXPECTED_SHEET_ID = '__OWN_ID__';
const WF_HEAD = ['کد درخواست','مسیر','وب‌سایت','دسته‌بندی','وضعیت مرجع NBO','پیشنهاد موتور','نظر Online','بررسی‌کننده Online','توضیح Online','نظر Instore','بررسی‌کننده Instore','توضیح Instore','وضعیت گردش کار','نسخه پرونده','آخرین تغییر','دلیل پیشنهادی','شناسه دستگاه'];
const CMD_HEAD = ['شناسه فرمان','کد درخواست','تیم','تصمیم','کد دلیل NBO','توضیح / مرجع بررسی','نام بررسی‌کننده','نسخه پرونده','ارسال؟','وضعیت پردازش','پیام','زمان پردازش','درخواست ثبت‌شده'];
const EVENT_HEAD = ['شناسه رویداد','زمان','کد درخواست','نوع رویداد','کاربر','نسخه','جزئیات'];
const RESULT_HEAD = ['SMR','Site','Category','Decision','Reason code (NBO)','Reason label (NBO)','Notes','Checked at','Batch','Evidence'];
const MANUAL_HEAD = ['SMR','Site','Why manual','What to check by hand','Assigned to','Checked at','Resolved?','Resolution'];
const RUN_HEAD = ['Date (Jalali)','Backlog size','Duplicates','Approve','Edit','Cancel','Manual','Errors','Run by','Mode (dry-run / live)'];
const faAction = {APPROVE:'تأیید', EDIT:'نیاز به اصلاح', CANCEL:'لغو', MANUAL:'بررسی دستی', REOPEN:'بازگشایی'};
function output(value) { return ContentService.createTextOutput(JSON.stringify(value)).setMimeType(ContentService.MimeType.JSON); }
function safe(value) { return typeof value === 'string' && /^[=+\-@]/.test(value) ? "'" + value : value; }
function cells(sh, row, values) {
  if (!values.length) return;
  const needed = row + values.length - 1;
  if (needed > sh.getMaxRows()) sh.insertRowsAfter(sh.getMaxRows(), needed - sh.getMaxRows());
  sh.getRange(row, 1, values.length, values[0].length).setValues(values.map(r => r.map(safe)));
}
function table(ss, title, header) {
  let sh = ss.getSheetByName(title);
  if (!sh) sh = ss.insertSheet(title);
  if (sh.getMaxColumns() < header.length) sh.insertColumnsAfter(sh.getMaxColumns(), header.length - sh.getMaxColumns());
  if (sh.getLastRow() === 0) {
    cells(sh, 1, [header]); sh.setFrozenRows(1); sh.setRightToLeft(true);
    sh.getRange(1,1,1,header.length).setFontWeight('bold').setBackground('#eeeeee');
  } else {
    const actual = sh.getRange(1,1,1,header.length).getDisplayValues()[0];
    if (actual.some((v,i) => v !== header[i])) throw new Error('ساختار تب تغییر کرده: ' + title);
  }
  return sh;
}
function rows(sh, width) { return sh.getLastRow() > 1 ? sh.getRange(2,1,sh.getLastRow()-1,width).getValues() : []; }
function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    if (!body || body.secret !== SECRET) return output({ok:false,error:'forbidden'});
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    if (ss.getId() !== EXPECTED_SHEET_ID) throw new Error('این اسکریپت متعلق به شیت دیگری است');
    if (body.action === 'ping') return output({ok:true,sheet:ss.getName(),sheet_id:ss.getId(),url:ss.getUrl(),version:3,own_workflow:true,online_instore:false});
    const lock = LockService.getScriptLock(); lock.waitLock(30000);
    try {
      if (body.action === 'workflow_sync') {  return output(sync(body,ss)); }
      if (body.action === 'workflow_commands') {  return output(commands(ss)); }
      if (body.action === 'workflow_ack') {  return output(ack(body,ss)); }
      if (body.action === 'append') return output(appendRun(body,ss));
      return output({ok:false,error:'این نسخه فقط شیت اختصاصی را تغییر می‌دهد'});
    } finally { lock.releaseLock(); }
  } catch(err) { return output({ok:false,error:String(err.message || err)}); }
}
function sync(body, ss) {
  if (!Array.isArray(body.cases) || !Array.isArray(body.events) || body.cases.length > 100 || body.events.length > 100) throw new Error('اندازه بسته نامعتبر');
  const sh = table(ss,'Workflow',WF_HEAD), log = table(ss,'Audit',EVENT_HEAD);
  const current = rows(sh,WF_HEAD.length), byId = {};
  current.forEach((r,i) => { if (byId[r[0]] !== undefined) throw new Error('کد درخواست تکراری در Workflow'); byId[r[0]]=i; });
  body.cases.forEach(c => {
    if (!c.smr || !Number.isSafeInteger(c.revision) || c.revision < 1) throw new Error('پرونده نامعتبر');
    const i = byId[c.smr];
    if (i !== undefined && Number(current[i][13]) > c.revision) throw new Error('نسخه قدیمی؛ همگام‌سازی متوقف شد');
    const o=c.online || {}, t=c.instore || {}, s=c.suggestion || {};
    const row=[c.smr,c.channel==='both'?'Online + Instore':'Online',c.site || '',c.category || '',c.source_status || '',
      faAction[s.action] || '',faAction[o.action] || '',o.actor || '',o.note || '',faAction[t.action] || '',t.actor || '',t.note || '',
      c.state_fa,c.revision,c.updated_at,(s.reason_codes || []).join('، '),body.device_id];
    if (i === undefined) { cells(sh,sh.getLastRow()+1,[row]); byId[c.smr]=current.length; current.push(row); }
    else { cells(sh,i+2,[row]); current[i]=row; }
  });
  const known = new Set(rows(log,EVENT_HEAD.length).map(r=>String(r[0])));
  const events=[];
  body.events.forEach(e => { if (!known.has(e.event_id)) { events.push([e.event_id,e.at,e.smr,e.kind,e.actor,e.revision,JSON.stringify(e.detail)]); known.add(e.event_id); } });
  cells(log,log.getLastRow()+1,events);
  SpreadsheetApp.flush();
  return {ok:true,cases:body.cases.length,events:body.events.length};
}
function commands(ss) {
  const sh=table(ss,'Decisions',CMD_HEAD), pending=[];
  rows(sh,CMD_HEAD.length).forEach((r,i)=> {
    if (pending.length>=100 || r[8]!==true || ['accepted','rejected'].includes(String(r[9]))) return;
    let command;
    if (r[12]) { command=JSON.parse(String(r[12])); }
    else {
      command={command_id:Utilities.getUuid(),smr:String(r[1]).trim(),team:String(r[2]).trim(),action:String(r[3]).trim(),
        reason:String(r[4]).trim(),note:String(r[5]).trim(),actor:String(r[6]).trim(),revision:Number(r[7])};
      sh.getRange(i+2,1).setValue(command.command_id);
      sh.getRange(i+2,13).setValue(JSON.stringify(command));
    }
    sh.getRange(i+2,10).setValue('pending'); pending.push(command);
  });
  SpreadsheetApp.flush();
  return {ok:true,commands:pending};
}
function ack(body,ss) {
  const sh=table(ss,'Decisions',CMD_HEAD), values=rows(sh,CMD_HEAD.length);
  (body.receipts || []).forEach(receipt=> {
    const i=values.findIndex(r=>r[0]===receipt.command_id);
    if (i<0 || !['accepted','rejected'].includes(receipt.status)) throw new Error('رسید نامعتبر');
    sh.getRange(i+2,10,1,3).setValues([[receipt.status,safe(receipt.error || ''),new Date().toISOString()]]);
  });
  SpreadsheetApp.flush(); return {ok:true};
}
function appendRun(body,ss) {
  const sh=table(ss,'Results',RESULT_HEAD), manual=table(ss,'Manual queue',MANUAL_HEAD);
  const known=new Set(rows(sh,10).map(r=>String(r[8])+'|'+String(r[0])));
  const knownManual=new Set(rows(manual,8).map(r=>String(r[0])+'|'+String(r[5])));
  const outputRows=[], manualRows=[];
  (body.rows || []).forEach(r=> {
    const key=String(body.run_id)+'|'+r.smr, at=r.decided_at || '';
    if (!known.has(key)) { outputRows.push([r.smr,r.site,r.category,r.action_fa,r.codes,r.reasons_fa,r.notes_fa,at,body.run_id,'']); known.add(key); }
    const mk=r.smr+'|'+at;
    if (r.action==='MANUAL' && !knownManual.has(mk)) { manualRows.push([r.smr,r.site,r.notes_fa,'تصمیم را در Decisions یا اپ ثبت کنید','',at,false,'']); knownManual.add(mk); }
  });
  cells(sh,sh.getLastRow()+1,outputRows); cells(manual,manual.getLastRow()+1,manualRows);
  SpreadsheetApp.flush(); return {ok:true,appended:outputRows.length,duplicate:!outputRows.length};
}
