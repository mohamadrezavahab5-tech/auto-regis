// v4: Google-hosted authorization and workflow. Users never receive the service-account key.
const USER_HEAD = ['Username','Role','Enabled','Token hash','Updated at'];
const STATE_HEAD = ['SMR','Revision','Case JSON'];
const OPS_HEAD = ['Operation ID','User','Result JSON'];
const OWNER = 'mohammadreza.vahab';
function digest(text) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8)
    .map(b=>('0'+((b+256)%256).toString(16)).slice(-2)).join('');
}
function authenticate(body,ss) {
  if (!/^[A-Za-z0-9_-]{40,128}$/.test(body.token || '')) throw new Error('دسترسی معتبر نیست');
  const found=rows(table(ss,'Users',USER_HEAD),5).filter(r=>r[0]===body.username && r[2]===true && r[3]===digest(body.token));
  if (found.length!==1) throw new Error('دسترسی غیرفعال یا نامعتبر است');
  if (!['admin','online','instore','viewer'].includes(found[0][1])) throw new Error('نقش معتبر نیست');
  return {username:found[0][0],role:found[0][1]};
}
function permission(user,roles) { if (!roles.includes(user.role)) throw new Error('این عملیات برای نقش شما مجاز نیست'); }
function flowState(c) {
  if (!c.active) {
    if (c.outcome==='approved') return ['DONE_APPROVED','تأییدشده در NBO'];
    if (c.outcome==='closed') return ['DONE_CLOSED','بسته‌شده در NBO'];
    return ['OUT_OF_SCOPE','خارج از صف فعال'];
  }
  const o=(c.online || {}).action, t=c.channel==='both'?(c.instore || {}).action:null;
  const votes=[o,t].filter(Boolean);
  if (votes.includes('APPROVE') && votes.some(v=>['EDIT','CANCEL'].includes(v))) return ['CONFLICT','اختلاف نظر دو تیم'];
  if (votes.includes('CANCEL')) return ['CANCEL','پیشنهاد لغو؛ اعمال نشده'];
  if (votes.includes('EDIT')) return ['EDIT','نیاز به اصلاح؛ اعمال نشده'];
  if (votes.includes('MANUAL')) return ['MANUAL','نیازمند بررسی دستی'];
  if (o!=='APPROVE') return (c.suggestion || {}).action==='MANUAL'?['MANUAL','نیازمند بررسی دستی']:['WAIT_ONLINE','منتظر نظر Online'];
  if (c.channel==='both' && t!=='APPROVE') return ['WAIT_INSTORE','منتظر نظر Instore'];
  return ['READY','آماده تأیید در NBO؛ اعمال نشده'];
}
function cellValue(v) {
  return {userEnteredValue:typeof v==='boolean'?{boolValue:v}:typeof v==='number'?{numberValue:v}:{stringValue:String(v==null?'':v)}};
}
function writeRequest(sh,row,values) {
  return {updateCells:{start:{sheetId:sh.getSheetId(),rowIndex:row-1,columnIndex:0},rows:[{values:values.map(cellValue)}],fields:'userEnteredValue'}};
}
function atomic(ss,requests) {
  // One Sheets batch commits the case, projection, audit and replay receipt together.
  const dimensions={};
  requests.forEach(r=> {const u=r.updateCells;if(u)dimensions[u.start.sheetId]=Math.max(dimensions[u.start.sheetId]||0,u.start.rowIndex+u.rows.length);});
  const growth=[];
  ss.getSheets().forEach(sh=>{const n=dimensions[sh.getSheetId()];if(n>sh.getMaxRows())growth.push({appendDimension:{sheetId:sh.getSheetId(),dimension:'ROWS',length:Math.max(500,n-sh.getMaxRows())}});});
  const response=UrlFetchApp.fetch('https://sheets.googleapis.com/v4/spreadsheets/'+ss.getId()+':batchUpdate',{
    method:'post',contentType:'application/json',headers:{Authorization:'Bearer '+ScriptApp.getOAuthToken()},
    payload:JSON.stringify({requests:growth.concat(requests)}),muteHttpExceptions:true});
  if(response.getResponseCode()!==200)throw new Error('ثبت کامل نشد؛ دوباره تلاش کنید');
}
function projection(c) {
  const o=c.online||{},t=c.instore||{},s=c.suggestion||{};
  return [c.smr,c.channel==='both'?'Online + Instore':'Online',c.site||'',c.category||'',c.source_status||'',
    faAction[s.action]||'',faAction[o.action]||'',o.actor||'',o.note||'',faAction[t.action]||'',t.actor||'',t.note||'',
    c.state_fa,c.revision,c.updated_at,(s.reason_codes||[]).join('، '),'workspace'];
}
function doPost(e) {
  try {
    const body=JSON.parse(e.postData.contents), ss=SpreadsheetApp.getActiveSpreadsheet();
    if(ss.getId()!==EXPECTED_SHEET_ID)throw new Error('شیت اشتباه');
    const lock=LockService.getScriptLock();lock.waitLock(30000);
    try {
      const user=authenticate(body,ss);
      if(body.action==='whoami')return output({ok:true,user:user,version:4});
      if(body.action==='list_users') {
        permission(user,['admin']);
        return output({ok:true,users:rows(table(ss,'Users',USER_HEAD),5).filter(r=>r[0]).map(r=>({username:r[0],role:r[1],enabled:r[2]===true}))});
      }
      if(body.action==='set_user')return output(setUser(body,user,ss));
      if(body.action==='release') {
        // Releases the owner published in his Updates tab; the app verifies the SHA-256 before running anything.
        const upd=ss.getSheetByName('Updates');
        const list=upd && upd.getLastRow()>1 ? upd.getRange(2,1,upd.getLastRow()-1,5).getValues() : [];
        return output({ok:true,releases:list.filter(r=>r[0]).map(r=>({version:String(r[0]),url:String(r[1]),sha256:String(r[2]),
          notes:String(r[3]),published_at:String(r[4])}))});
      }
      const state=table(ss,'Workspace state',STATE_HEAD), data=rows(state,3);
      if(body.action==='read') {
        const offset=Math.max(0,Number(body.offset)||0), page=data.slice(offset,offset+200).filter(r=>r[0]).map(r=>JSON.parse(r[2]));
        return output({ok:true,cases:page,next:offset+200<data.length?offset+200:null,user:user});
      }
      if(!['source','decide'].includes(body.action))throw new Error('عملیات نامعتبر');
      permission(user,body.action==='source'?['admin','online']:['admin','online','instore']);
      if(!/^[a-f0-9]{32}$/.test(body.operation_id || ''))throw new Error('شناسه عملیات معتبر نیست');
      const ops=table(ss,'Workspace operations',OPS_HEAD), recorded=rows(ops,3).find(r=>r[0]===body.operation_id);
      if(recorded){if(recorded[1]!==user.username)throw new Error('مالک عملیات متفاوت است');return output(JSON.parse(recorded[2]));}
      const smr=body.action==='source'?(body.case || {}).smr:body.smr;
      if(!/^SMR-\d+$/.test(smr || ''))throw new Error('کد درخواست نامعتبر');
      const index=data.findIndex(r=>r[0]===smr);
      let c=index<0?null:JSON.parse(data[index][2]);
      if(body.action==='source')c=sourceCase(body.case,c);
      else c=humanDecision(body,user,c,ss);
      c.revision=(index<0?0:Number(data[index][1]))+1;c.updated_at=new Date().toISOString();
      const status=flowState(c);c.state=status[0];c.state_fa=status[1];
      const wf=table(ss,'Workflow',WF_HEAD), shown=rows(wf,WF_HEAD.length), wi=shown.findIndex(r=>r[0]===smr);
      const audit=table(ss,'Audit',EVENT_HEAD);
      const result={ok:true,case:c};
      atomic(ss,[writeRequest(state,index<0?state.getLastRow()+1:index+2,[smr,c.revision,JSON.stringify(c)]),
        writeRequest(wf,wi<0?wf.getLastRow()+1:wi+2,projection(c)),
        writeRequest(audit,audit.getLastRow()+1,[body.operation_id,c.updated_at,smr,body.action,user.username,c.revision,
          JSON.stringify(body.action==='decide'?{team:body.team,decision:body.decision,note:body.note,reason:body.reason}: {source:'NBO/engine'})]),
        writeRequest(ops,ops.getLastRow()+1,[body.operation_id,user.username,JSON.stringify(result)])]);
      return output(result);
    }finally{lock.releaseLock();}
  }catch(err){return output({ok:false,error:String(err.message||err)});}
}
function sourceCase(src,old) {
  if(!src || !['online','both'].includes(src.channel) || typeof src.active!=='boolean' || !/^[a-f0-9]{64}$/.test(src.fingerprint||''))throw new Error('داده مبدأ معتبر نیست');
  // Reject stale source snapshots; a colleague cannot restore an older site's approvals.
  if(!src.source_loaded_at || !Number.isFinite(Date.parse(src.source_loaded_at)))throw new Error('زمان دریافت مرجع لازم است');
  if(old && old.source_loaded_at && Date.parse(src.source_loaded_at)<Date.parse(old.source_loaded_at))throw new Error('مرجع NBO قدیمی‌تر است');
  const changed=!old || old.fingerprint!==src.fingerprint || old.active!==src.active;
  const c={smr:src.smr,channel:src.channel,site:String(src.site||''),category:String(src.category||''),
    source_status:String(src.source_status||''),active:src.active,outcome:src.outcome||null,fingerprint:src.fingerprint,
    source_loaded_at:src.source_loaded_at,suggestion:src.suggestion||null,online_hold:!!src.online_hold,
    online:changed?null:old.online,instore:changed?null:old.instore};
  if(old && JSON.stringify((old.suggestion||{}).reason_codes)!==JSON.stringify((c.suggestion||{}).reason_codes))c.online=null;
  if(old && (old.suggestion||{}).action!==(c.suggestion||{}).action)c.online=null;
  // The engine's verdict (owner rule: it counts as the Online verdict) may fill an empty Online slot; a person's never moves.
  const engine=src.online||null;
  if(engine && engine.source==='engine' && !(c.online && c.online.source!=='engine'))c.online=engine;
  if(!engine && c.online && c.online.source==='engine')c.online=null;
  return c;
}
function humanDecision(body,user,c,ss) {
  if(!c || !c.active)throw new Error('درخواست در صف فعال نیست');
  if(body.revision!==c.revision)throw new Error('نسخه پرونده تغییر کرده؛ دوباره بررسی کنید');
  if(!['online','instore'].includes(body.team) || (user.role!=='admin' && body.team!==user.role))throw new Error('اجازه ثبت نظر این تیم را ندارید');
  if(body.team==='instore' && c.channel!=='both')throw new Error('نظر Instore لازم نیست');
  if(!['APPROVE','EDIT','CANCEL','MANUAL','REOPEN'].includes(body.decision))throw new Error('تصمیم نامعتبر');
  if(!String(body.note||'').trim())throw new Error('توضیح بررسی لازم است');
  if(['EDIT','CANCEL'].includes(body.decision)) {
    const reasonSheet=ss.getSheetByName('Reasons');
    const valid=reasonSheet && rows(reasonSheet,6).some(r=>r[1]===body.decision && r[2]===body.reason);
    if(!valid)throw new Error('کد دلیل معتبر NBO لازم است');
  }
  c[body.team]=body.decision==='REOPEN'?null:{action:body.decision,actor:user.username,note:String(body.note).trim(),
    reason:body.reason||'',at:new Date().toISOString(),source:'workspace'};
  return c;
}
function setUser(body,user,ss) {
  permission(user,['admin']);
  const name=String(body.user||'').trim().toLowerCase();
  if(!/^[a-z0-9][a-z0-9._-]{1,79}$/.test(name) || !['admin','online','instore','viewer'].includes(body.role))throw new Error('کاربر یا نقش نامعتبر');
  if(body.role==='admin' && name!==OWNER)throw new Error('مدیر اصلی ثابت است');
  if(name===OWNER && (body.role!=='admin'||body.enabled!==true))throw new Error('مدیر اصلی قابل غیرفعال‌کردن نیست');
  const sh=table(ss,'Users',USER_HEAD), current=rows(sh,5), i=current.findIndex(r=>r[0]===name);
  const hash=body.token_hash || (i<0?'':current[i][3]);
  if(!/^[a-f0-9]{64}$/.test(hash))throw new Error('کد دسترسی معتبر نیست');
  atomic(ss,[writeRequest(sh,i<0?sh.getLastRow()+1:i+2,[name,body.role,body.enabled===true,hash,new Date().toISOString()])]);
  return {ok:true};
}
