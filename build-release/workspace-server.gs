// AutoReview v3. Bound to the owner's spreadsheet. Never opens another spreadsheet.

const EXPECTED_SHEET_ID = '1UHlktMbe6bhDMKQd51Z-H1pvrrgD3ppTl9QmI1cEFrQ';
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

// AutoReview Workspace v5.  This file is appended to own-sheet.gs after its
// old doPost dispatcher has been removed.  It is deliberately a cloud
// authority: clients keep caches, never the shared truth or an SA key.
const APP_SHARED_KEY = 'Q6Fft7TAkttziza_i5iWUwzz4VtdXNqfPP1OsqcYKxcFBWrMX9BNVLeWaqNrucoe';
const V5_AUDIT = ['Event ID','Timestamp','CRM actor','Machine ID','Client version','Action','SMR','Revision','Result','Error code','Detail'];
const V5_STATE = ['SMR','Revision','Case JSON'];
const V5_OPS = ['Operation ID','User','Payload hash','Result JSON'];
const V5_EXEC = ['SMR','Revision','Claim','User','State','Updated at','Detail'];
const V5_META = ['Key','Value'];
const V5_OWNER = 'mohammadreza.vahab';

function v5digest(value) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(value), Utilities.Charset.UTF_8)
    .map(function(b) { return ('0' + ((b + 256) % 256).toString(16)).slice(-2); }).join('');
}
function v5json(value) { return JSON.stringify(value); }
function v5now() { return new Date().toISOString(); }
function v5out(value) { return output(value); }
function v5table(ss, name, head) { return table(ss, name, head); }
function v5rows(sh, width) { return rows(sh, width); }
function v5write(sh, row, values) { cells(sh, row, [values]); }
function v5cell(v) { return {userEnteredValue:typeof v==='boolean'?{boolValue:v}:typeof v==='number'?{numberValue:v}:{stringValue:String(v==null?'':v)}}; }
function v5request(sh, row, values) { return {updateCells:{start:{sheetId:sh.getSheetId(),rowIndex:row-1,columnIndex:0},rows:[{values:values.map(v5cell)}],fields:'userEnteredValue'}}; }
function v5atomic(ss, requests) {
  // This is one Sheets API transaction: state, durable operation receipt and
  // generation either all land, or none land.  It deliberately replaces the
  // convenient sequential Range.setValues path used by the v3 script.
  const max={}; requests.forEach(function(r){const u=r.updateCells;if(u)max[u.start.sheetId]=Math.max(max[u.start.sheetId]||0,u.start.rowIndex+u.rows.length);});
  const growth=[]; ss.getSheets().forEach(function(sh){const n=max[sh.getSheetId()];if(n>sh.getMaxRows())growth.push({appendDimension:{sheetId:sh.getSheetId(),dimension:'ROWS',length:Math.max(100,n-sh.getMaxRows())}});});
  const response=UrlFetchApp.fetch('https://sheets.googleapis.com/v4/spreadsheets/'+ss.getId()+':batchUpdate',{method:'post',contentType:'application/json',headers:{Authorization:'Bearer '+ScriptApp.getOAuthToken()},payload:JSON.stringify({requests:growth.concat(requests)}),muteHttpExceptions:true});
  if(response.getResponseCode()!==200) throw v5error('HTTP_ERROR','Sheets batchUpdate HTTP '+response.getResponseCode()+': '+v5safe(response.getContentText()));
}
function v5error(code, message) { const e=new Error(message);e.code=code;return e; }
function v5auth(body) {
  if (!body || body.app_key!==APP_SHARED_KEY || APP_SHARED_KEY==='__APP_SHARED_'+'KEY__') throw v5error('AUTH_ERROR','کلید برنامه معتبر نیست');
  const actor=String(body.actor||'');
  if (!/^[a-z0-9][a-z0-9._-]{1,79}$/.test(actor) || !body.client_version) throw v5error('AUTH_ERROR','هویت CRM معتبر نیست');
  return {username:actor,role:actor===V5_OWNER?'admin':'online'};
}
function v5allow(user, roles) { /* CRM-authenticated app users share workspace access. */ }
function v5safe(value) {
  return String(value||'').split(APP_SHARED_KEY).join('[REDACTED]').replace(/(?:password|token|cookie|authorization|private_key|app_key)\s*[:=]\s*[^\s,;}]+/gi,'[REDACTED]').slice(0,600);
}
function v5audit(ss, body, action, smr, revision, result, detail) {
  const sh=v5table(ss,'Workspace audit',V5_AUDIT);
  return v5request(sh,sh.getLastRow()+1,[body.operation_id||Utilities.getUuid(),v5now(),body.actor,String(body.machine_id||'').slice(0,80),String(body.client_version||'').slice(0,30),action,smr||'',revision||'',result||'ok','',v5safe(detail)]);
}
function v5projection(ss,c,providedSheet,providedRows) {
  const sh=providedSheet||v5table(ss,'Workflow',WF_HEAD), data=providedRows||v5rows(sh,WF_HEAD.length), i=data.findIndex(r=>r[0]===c.smr);
  const o=c.online||{},t=c.instore||{},s=c.suggestion||{};
  return v5request(sh,i<0?sh.getLastRow()+1:i+2,[c.smr,c.channel==='both'?'Online + Instore':'Online',c.site||'',c.category||'',c.source_status||'',faAction[s.action]||'',faAction[o.action]||'',o.actor||'',o.note||'',faAction[t.action]||'',t.actor||'',t.note||'',c.state_fa||c.state,c.revision,c.updated_at,(s.reason_codes||[]).join('، '),'workspace']);
}
function v5meta(ss) { return v5table(ss, 'Workspace meta', V5_META); }
function v5generation(ss) {
  const rows=v5rows(v5meta(ss),V5_META.length), row=rows.find(function(r){return r[0]==='generation';});
  return Number(row ? row[1] : 0);
}
function v5generationRequest(ss, generation) {
  const sh=v5meta(ss), data=v5rows(sh,V5_META.length), i=data.findIndex(function(r){return r[0]==='generation';});
  return v5request(sh,i<0?sh.getLastRow()+1:i+2,['generation',generation]);
}
function v5state(ss) { return v5rows(v5table(ss, 'Workspace state', V5_STATE), V5_STATE.length); }
function v5busy(ss, smr) {
  return v5rows(v5table(ss, 'Workspace execution', V5_EXEC), V5_EXEC.length)
    .some(function(r) { return r[0] === smr && ['SENDING','UNCERTAIN'].includes(r[4]); });
}
function v5flow(c) {
  if(!c.active)return c.outcome==='approved'?'DONE_APPROVED':c.outcome==='closed'?'DONE_CLOSED':'OUT_OF_SCOPE';
  const o=(c.online||{}).action,t=c.channel==='both'?(c.instore||{}).action:null, votes=[o,t];
  if(votes.includes('APPROVE')&&votes.some(v=>['EDIT','CANCEL'].includes(v)))return 'CONFLICT';
  if(votes.includes('CANCEL'))return 'CANCEL';
  if(votes.includes('EDIT'))return 'EDIT';
  if(votes.includes('MANUAL'))return 'MANUAL';
  if(o!=='APPROVE')return (c.suggestion||{}).action==='MANUAL'?'MANUAL':'WAIT_ONLINE';
  return c.channel==='both'&&t!=='APPROVE'?'WAIT_INSTORE':'READY';
}
const V5_LABELS={DONE_APPROVED:'تأییدشده در NBO',DONE_CLOSED:'بسته‌شده در NBO',OUT_OF_SCOPE:'خارج از صف فعال',CONFLICT:'اختلاف نظر دو تیم',CANCEL:'پیشنهاد لغو؛ اعمال نشده',EDIT:'نیاز به اصلاح؛ اعمال نشده',MANUAL:'نیازمند بررسی دستی',WAIT_ONLINE:'منتظر نظر Online',WAIT_INSTORE:'منتظر نظر Instore',READY:'آماده تأیید در NBO؛ اعمال نشده'};
function v5reviewAt(value) {
  const at=value && (value.decided_at || value.updated_at || value.at || value.checked_at);
  const n=Date.parse(at || ''); return Number.isFinite(n) ? n : -1;
}
function v5newestReview(oldValue, incoming) {
  if (!incoming) return oldValue || null;
  if (!oldValue || v5reviewAt(incoming) >= v5reviewAt(oldValue)) return incoming;
  return oldValue; // late device sync must never replace a newer reviewed result
}
function v5same(value, other) { return v5json(value == null ? null : value) === v5json(other == null ? null : other); }
function v5source(body, user, ss) {
  v5allow(user, ['admin','online']);
  const cases = body.payload && body.payload.cases;
  if (!Array.isArray(cases) || !cases.length || cases.length > 50) throw new Error('بسته مبدأ نامعتبر است');
  const unique = {}; cases.forEach(function(c) { if (!/^SMR-\d+$/.test(String(c.smr || '')) || unique[c.smr]) throw new Error('کد درخواست نامعتبر یا تکراری'); unique[c.smr]=true; });
  const ops = v5table(ss, 'Workspace operations v5', V5_OPS);
  const digest=v5digest(v5json(body.payload));
  const seen=v5rows(ops,V5_OPS.length).filter(function(r){return r[0]===body.operation_id;});
  if(seen.length) { if(seen.length!==1 || seen[0][1]!==user.username || seen[0][2]!==digest) throw new Error('بازپخش عملیات با محتوای متفاوت مجاز نیست'); return JSON.parse(seen[0][3]); }
  const sh=v5table(ss,'Workspace state',V5_STATE), current=v5rows(sh,V5_STATE.length), by={};
  current.forEach(function(r,i){if(by[r[0]]!==undefined)throw new Error('کد درخواست تکراری');by[r[0]]=i;});
  const requests=[], accepted=[], rejected=[]; let changed=false, nextRow=sh.getLastRow()+1;
  const wf=v5table(ss,'Workflow',WF_HEAD),projectionRows=v5rows(wf,WF_HEAD.length); let nextProjection=wf.getLastRow()+1;
  const legacy=v5table(ss,'Workspace execution',V5_EXEC);let nextExecution=legacy.getLastRow()+1;
  const busy=new Set(v5rows(legacy,V5_EXEC.length).filter(r=>['SENDING','UNCERTAIN'].includes(r[4])).map(r=>r[0]));
  cases.forEach(function(src) {
    const i=by[src.smr], prior=i===undefined?null:JSON.parse(current[i][2]);
    if(busy.has(src.smr)) { rejected.push({smr:src.smr,status:'rejected',error:'این درخواست در حال ارسال یا نامشخص است'}); return; }
    if(!['online','both'].includes(src.channel) || !/^[a-f0-9]{64}$/.test(String(src.fingerprint||'')) || typeof src.active!=='boolean' || !Number.isFinite(Date.parse(src.source_loaded_at)) || Date.parse(src.source_loaded_at)>Date.now()+30000) { rejected.push({smr:src.smr,status:'rejected',error:'داده مبدأ معتبر نیست'}); return; }
    if(prior && Date.parse(src.source_loaded_at)<Date.parse(prior.source_loaded_at)) { rejected.push({smr:src.smr,status:'stale'}); return; }
    const identityChanged=!prior || prior.fingerprint!==src.fingerprint || prior.active!==!!src.active || prior.channel!==src.channel;
    // Source refreshes enrich the canonical record.  They keep human votes;
    // an engine vote can move only while the slot is still engine-owned.
    const c={smr:src.smr,channel:src.channel,site:String(src.site||''),category:String(src.category||''),source_status:String(src.source_status||''),active:!!src.active,outcome:src.outcome||null,fingerprint:String(src.fingerprint||''),source_loaded_at:src.source_loaded_at,source_created_at:src.source_created_at||'',review:v5newestReview(prior&&prior.review,src.review),suggestion:src.suggestion||null,online_hold:!!src.online_hold,online:prior?prior.online:(user.username===V5_OWNER?src.online:null),instore:prior?prior.instore:(user.username===V5_OWNER?src.instore:null)};
    if (identityChanged && prior) { c.online=null; c.instore=null; }
    const engine=src.online && src.online.source==='engine' ? src.online : null;
    if(prior && !identityChanged && v5reviewAt(src.review)<v5reviewAt(prior.review)){c.review=prior.review;c.suggestion=prior.suggestion;c.online_hold=prior.online_hold;} 
    if (engine && (!c.online || c.online.source==='engine') && (!prior || v5reviewAt(src.review)>=v5reviewAt(prior.review))) c.online=engine;
    if (!engine && c.online && c.online.source==='engine' && (!prior || v5reviewAt(src.review)>=v5reviewAt(prior.review))) c.online=null;
    // The NBO fingerprint is the review invalidation boundary.  Fresh NBO
    // timestamps and improved engine/review metadata stay in the same revision
    // so an already-recorded human decision is not silently discarded.
    c.revision=identityChanged?(prior?Number(prior.revision)+1:1):(prior?Number(prior.revision):1);c.state=v5flow(c);
    const same=prior && v5same({channel:prior.channel,site:prior.site,category:prior.category,source_status:prior.source_status,active:prior.active,outcome:prior.outcome,fingerprint:prior.fingerprint,source_loaded_at:prior.source_loaded_at,source_created_at:prior.source_created_at,review:prior.review,suggestion:prior.suggestion,online_hold:!!prior.online_hold,online:prior.online,instore:prior.instore,revision:prior.revision}, {channel:c.channel,site:c.site,category:c.category,source_status:c.source_status,active:c.active,outcome:c.outcome,fingerprint:c.fingerprint,source_loaded_at:c.source_loaded_at,source_created_at:c.source_created_at,review:c.review,suggestion:c.suggestion,online_hold:c.online_hold,online:c.online,instore:c.instore,revision:c.revision});
    if(same) { accepted.push(prior); return; }
    if(prior && !identityChanged && !v5same([prior.online,prior.instore,prior.suggestion,prior.online_hold],[c.online,c.instore,c.suggestion,c.online_hold]))c.revision++;
    c.state=v5flow(c);c.state_fa=V5_LABELS[c.state];c.updated_at=v5now();
    requests.push(v5request(sh,i===undefined?nextRow++:i+2,[c.smr,c.revision,v5json(c)]));
    const projected=v5projection(ss,c,wf,projectionRows);if(projected.updateCells.start.rowIndex>=wf.getLastRow())projected.updateCells.start.rowIndex=nextProjection++-1;requests.push(projected);
    if(!prior && Array.isArray(src.previous_executions)&&src.previous_executions.length){
      const entries=src.previous_executions.filter(e=>['SENT','SENDING','UNCERTAIN','VERIFIED','APPROVED_IN_NBO'].includes(e.state));
      if(entries.length){const uncertain=entries.some(e=>['SENDING','UNCERTAIN'].includes(e.state));requests.push(v5request(legacy,nextExecution++,[c.smr,c.revision,v5digest(body.operation_id+c.smr).slice(0,32),user.username,uncertain?'UNCERTAIN':'SENT',v5now(),'Migrated existing execution history']));}
    }
    accepted.push(c);changed=true;
  });
  const generation=v5generation(ss)+(changed?1:0), result={ok:true,cases:accepted.map(c=>({smr:c.smr,revision:c.revision})),rejected:rejected,generation:generation};
  if(changed) requests.push(v5generationRequest(ss,generation));
  // The receipt shares the transaction with all accepted source rows.  A lost
  // response can therefore return this exact result without duplicate rows.
  requests.push(v5request(ops,ops.getLastRow()+1,[body.operation_id,user.username,digest,v5json(result)]));
  requests.push(v5audit(ss,body,'SOURCE','',0,'ok','accepted='+accepted.length+' rejected='+rejected.length));
  v5atomic(ss,requests); return result;
}
function v5decide(body,user,ss) {
  v5allow(user,['admin','online','instore']);
  const p=body.payload || {}, smr=String(p.smr||'');
  if (!/^SMR-\d+$/.test(smr)) throw new Error('کد درخواست نامعتبر');
  const ops=v5table(ss,'Workspace operations v5',V5_OPS), digest=v5digest(v5json(p)), seen=v5rows(ops,V5_OPS.length).filter(function(r){return r[0]===body.operation_id;});
  if(seen.length){if(seen.length!==1||seen[0][1]!==user.username||seen[0][2]!==digest)throw new Error('بازپخش عملیات با محتوای متفاوت مجاز نیست');return JSON.parse(seen[0][3]);}
  if (v5busy(ss,smr)) throw new Error('این درخواست در حال ارسال یا نامشخص است؛ تغییر تصمیم ممنوع است');
  const sh=v5table(ss,'Workspace state',V5_STATE), data=v5rows(sh,V5_STATE.length), i=data.findIndex(function(r){return r[0]===smr;});
  if (i<0) throw new Error('پرونده پیدا نشد'); const c=JSON.parse(data[i][2]);
  if (!c.active || Number(p.revision)!==Number(c.revision)) throw new Error('نسخه پرونده تغییر کرده؛ دوباره بررسی کنید');
  if (!['online','instore'].includes(p.team) || (p.team==='instore' && c.channel!=='both')) throw new Error('اجازه ثبت این نظر را ندارید');
  if (!['APPROVE','EDIT','CANCEL','MANUAL','REOPEN'].includes(p.decision) || !String(p.note||'').trim()) throw new Error('تصمیم یا توضیح معتبر نیست');
  if(['EDIT','CANCEL'].includes(p.decision)){const reasons=ss.getSheetByName('Reasons');if(!reasons || !v5rows(reasons,6).some(r=>r[1]===p.decision&&r[2]===p.reason))throw new Error('کد دلیل معتبر NBO لازم است');}
  c[p.team]=p.decision==='REOPEN'?null:{action:p.decision,actor:user.username,note:String(p.note).trim(),reason:String(p.reason||''),at:v5now(),source:'workspace'};
  c.revision++;c.updated_at=v5now();c.state=v5flow(c);c.state_fa=V5_LABELS[c.state];const generation=v5generation(ss)+1,result={ok:true,case:c,generation:generation};
  v5atomic(ss,[v5projection(ss,c),v5audit(ss,body,'DECIDE',c.smr,c.revision,'ok',p.team+': '+p.decision),v5request(sh,i+2,[c.smr,c.revision,v5json(c)]),v5generationRequest(ss,generation),v5request(ops,ops.getLastRow()+1,[body.operation_id,user.username,digest,v5json(result)])]);return result;
}
function v5claim(body,user,ss) {
  v5allow(user,['admin','online']); const p=body.payload||body;
  if (!/^SMR-\d+$/.test(String(p.smr||'')) || !/^[a-f0-9]{32}$/.test(String(p.claim||''))) throw new Error('شناسه اجرا معتبر نیست');
  const ops=v5table(ss,'Workspace operations v5',V5_OPS),digest=v5digest(v5json(p)),seen=v5rows(ops,V5_OPS.length).filter(function(r){return r[0]===body.operation_id;});
  if(seen.length){if(seen.length!==1||seen[0][1]!==user.username||seen[0][2]!==digest)throw new Error('بازپخش عملیات با محتوای متفاوت مجاز نیست');return JSON.parse(seen[0][3]);}
  if (v5busy(ss,p.smr) || v5rows(v5table(ss,'Workspace execution',V5_EXEC),V5_EXEC.length).some(r=>r[0]===p.smr&&Number(r[1])===Number(p.revision)&&['SENT','VERIFIED','APPROVED_IN_NBO'].includes(r[4]))) throw new Error('این درخواست در سیستم دیگری ارسال شده یا نتیجه‌اش نامشخص است');
  const data=v5state(ss), row=data.find(function(r){return r[0]===p.smr;}); if(!row) throw new Error('پرونده پیدا نشد'); const c=JSON.parse(row[2]);
  if (Number(p.revision)!==Number(c.revision) || !['READY','EDIT','CANCEL'].includes(v5flow(c)) || !['PENDING','COMMERCIAL_IN_PROGRESS'].includes(c.source_status) || !Number.isFinite(Date.parse(c.source_loaded_at)) || Date.now()-Date.parse(c.source_loaded_at)>1800000 || Date.parse(c.source_loaded_at)>Date.now()+30000) throw new Error('پرونده دیگر آماده اجرا نیست');
  if (p.automatic && (user.role!=='admin' || c.channel!=='online')) throw new Error('ثبت خودکار فقط برای Online و مدیر مجاز است');
  const sh=v5table(ss,'Workspace execution',V5_EXEC),generation=v5generation(ss)+1,result={ok:true,claim:p.claim,case:c,generation:generation};
  v5atomic(ss,[v5audit(ss,body,'NBO_ATTEMPT',p.smr,p.revision,'SENDING',''),v5request(sh,sh.getLastRow()+1,[p.smr,p.revision,p.claim,user.username,'SENDING',v5now(),'']),v5generationRequest(ss,generation),v5request(ops,ops.getLastRow()+1,[body.operation_id,user.username,digest,v5json(result)])]);return result;
}
function v5finish(body,user,ss) {
  v5allow(user,['admin','online']); const p=body.payload||body;
  if (!/^SMR-\d+$/.test(String(p.smr||'')) || !/^[a-f0-9]{32}$/.test(String(p.claim||'')) || !['SENT','UNCERTAIN','BLOCKED'].includes(p.state)) throw new Error('نتیجه اجرا معتبر نیست');
  const ops=v5table(ss,'Workspace operations v5',V5_OPS),digest=v5digest(v5json(p)),seen=v5rows(ops,V5_OPS.length).filter(function(r){return r[0]===body.operation_id;});
  if(seen.length){if(seen.length!==1||seen[0][1]!==user.username||seen[0][2]!==digest)throw new Error('بازپخش عملیات با محتوای متفاوت مجاز نیست');return JSON.parse(seen[0][3]);}
  const sh=v5table(ss,'Workspace execution',V5_EXEC), data=v5rows(sh,V5_EXEC.length), i=data.findIndex(function(r){return r[2]===p.claim;});
  if(i<0){const result={ok:true,not_found:true};v5atomic(ss,[v5request(ops,ops.getLastRow()+1,[body.operation_id,user.username,digest,v5json(result)])]);return result;}
  if(data[i][0]!==p.smr || Number(data[i][1])!==Number(p.revision) || data[i][3]!==user.username) throw new Error('مالک اجرا متفاوت است');
  if(data[i][4]===p.state){const result={ok:true,idempotent:true};v5atomic(ss,[v5request(ops,ops.getLastRow()+1,[body.operation_id,user.username,digest,v5json(result)])]);return result;}
  if(data[i][4]!=='SENDING') throw new Error('نتیجه قبلاً ثبت شده');
  const generation=v5generation(ss)+1,result={ok:true,generation:generation};
  v5atomic(ss,[v5audit(ss,body,p.state==='SENT'?'NBO_SUCCESS':p.state==='UNCERTAIN'?'NBO_UNCERTAIN':'NBO_FAILED',p.smr,p.revision,p.state,p.detail),v5request(sh,i+2,[p.smr,p.revision,p.claim,user.username,p.state,v5now(),String(p.detail||'').slice(0,10000)]),v5generationRequest(ss,generation),v5request(ops,ops.getLastRow()+1,[body.operation_id,user.username,digest,v5json(result)])]);return result;
}
function v5read(body,user,ss) {
  const p=body.payload||{}, start=Math.max(0,Number(p.offset)||0), before=v5generation(ss);
  if(p.generation!=null && p.generation!==before) return {ok:true,restart:true,generation:before};
  const all=v5state(ss).filter(function(r){return r[0];}), page=all.slice(start,start+500).map(function(r){return JSON.parse(r[2]);});
  const after=v5generation(ss); if(before!==after) return {ok:true,restart:true,generation:after};
  return {ok:true,version:5,generation:after,cases:page,next:start+500<all.length?start+500:null,executions:start===0?v5rows(v5table(ss,'Workspace execution',V5_EXEC),V5_EXEC.length):[]};
}
function v5auditBatch(body,user,ss) {
  const events=body.events;if(!Array.isArray(events)||events.length>100)throw new Error('بسته لاگ نامعتبر');
  const sh=v5table(ss,'Workspace audit',V5_AUDIT),ids=new Set(v5rows(sh,V5_AUDIT.length).map(r=>r[0]));let next=sh.getLastRow()+1;const req=[];
  events.forEach(e=>{if(!/^[a-f0-9]{32}$/.test(e.event_id||''))throw new Error('شناسه لاگ نامعتبر');if(!ids.has(e.event_id)){ids.add(e.event_id);req.push(v5request(sh,next++,[e.event_id,e.at||v5now(),user.username,body.machine_id||'',body.client_version,String(e.kind||'EVENT').slice(0,60),e.smr||'',e.revision||'',e.result||'',e.error_code||'',v5safe(e.detail)]));}});
  if(req.length)v5atomic(ss,req);return {ok:true,event_ids:events.map(e=>e.event_id)};
}
function doPost(e) {
  try {
    const body=JSON.parse(e.postData.contents), user=v5auth(body), action=body.action;
    const ss=SpreadsheetApp.openById(EXPECTED_SHEET_ID), lock=LockService.getScriptLock();lock.waitLock(30000);
    try {
      if(action==='health')return v5out({ok:true,server_version:5,schema_version:5,sheet_ok:ss.getId()===EXPECTED_SHEET_ID});
      if(action==='whoami')return v5out({ok:true,version:5,user:user});
      if(action==='read'){body.payload={offset:body.offset,generation:body.generation};return v5out(v5read(body,user,ss));}
      if(action==='audit_batch')return v5out(v5auditBatch(body,user,ss));
      if(action==='release'){const sh=ss.getSheetByName('Updates');return v5out({ok:true,releases:sh?v5rows(sh,5).filter(r=>r[0]).map(r=>({version:String(r[0]),url:String(r[1]),sha256:String(r[2]),notes:String(r[3]),published_at:String(r[4])})):[]});}
      if(!/^[a-f0-9]{32}$/.test(body.operation_id||''))throw new Error('شناسه عملیات معتبر نیست');
      if(action==='source_batch'){body.payload={cases:body.cases};return v5out(v5source(body,user,ss));}
      if(action==='decide'){body.payload={smr:body.smr,team:body.team,decision:body.decision,note:body.note,revision:body.revision,reason:body.reason};return v5out(v5decide(body,user,ss));}
      if(action==='claim_execution'){body.payload={smr:body.smr,revision:body.revision,claim:body.claim,automatic:!!body.automatic};return v5out(v5claim(body,user,ss));}
      if(action==='finish_execution'){body.payload={smr:body.smr,revision:body.revision,claim:body.claim,state:body.state,detail:body.detail};return v5out(v5finish(body,user,ss));}
      throw new Error('عملیات نامعتبر');
    }finally{lock.releaseLock();}
  }catch(err){const code=err.code||(String(err.message).includes('ساختار تب')?'SHEET_SCHEMA_ERROR':'SCRIPT_ERROR');return v5out({ok:false,error_code:code,error:v5safe(err.message||err)});}
}
// Run once as the owner during migration. Only protections created by AutoReview are removed.
function releaseAutoReviewProtections(){
  const ss=SpreadsheetApp.openById(EXPECTED_SHEET_ID);
  ss.getSheets().forEach(sh=>[SpreadsheetApp.ProtectionType.SHEET,SpreadsheetApp.ProtectionType.RANGE].forEach(type=>sh.getProtections(type).filter(p=>String(p.getDescription()||'').startsWith('AutoReview')).forEach(p=>p.remove())));
}
