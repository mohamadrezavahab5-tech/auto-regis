// Execute the shipped Apps Script, not a reimplementation of its business rules.
const fs = require('node:fs'), vm = require('node:vm'), crypto = require('node:crypto');
const assert = require('node:assert/strict');
class Sheet {
  constructor(name,id) { this.name=name;this.id=id;this.data=[]; }
  getSheetId(){return this.id;} getMaxRows(){return 100000;} getMaxColumns(){return 30;}
  getLastRow(){return this.data.length;} setFrozenRows(){return this;} setRightToLeft(){return this;}
  getRange(row,col,n=1,m=1){const sheet=this;return {
    getValues:()=>Array.from({length:n},(_,i)=>Array.from({length:m},(_,j)=>sheet.data[row-1+i]?.[col-1+j]??'')),
    getDisplayValues(){return this.getValues().map(r=>r.map(String));},
    setValues(values){values.forEach((r,i)=>{sheet.data[row-1+i]??=[];r.forEach((v,j)=>sheet.data[row-1+i][col-1+j]=v);});return this;},
    setFontWeight(){return this;},setBackground(){return this;}
  };}
}
const sheets=[], book={getId:()=> 'S'.repeat(33),getSheets:()=>sheets,
  getSheetByName:name=>sheets.find(s=>s.name===name),insertSheet:name=>{const s=new Sheet(name,sheets.length+1);sheets.push(s);return s;}};
let locked=false, failBatch=false;
const context={Date,console,SpreadsheetApp:{openById:id=>{assert.equal(id,book.getId());return book;}},
  Utilities:{getUuid:()=>crypto.randomUUID(),DigestAlgorithm:{SHA_256:1},Charset:{UTF_8:1},
    computeDigest:(_algorithm,value)=>Array.from(crypto.createHash('sha256').update(value).digest())},
  LockService:{getScriptLock:()=>({waitLock:()=>{assert.equal(locked,false);locked=true;},releaseLock:()=>{locked=false;}})},
  ScriptApp:{getOAuthToken:()=> 'test-only'},
  ContentService:{MimeType:{JSON:'json'},createTextOutput:value=>({setMimeType:()=>JSON.parse(value)})},
  UrlFetchApp:{fetch:(_url,options)=>{
    assert.equal(locked,true);if(failBatch){failBatch=false;return {getResponseCode:()=>400,getContentText:()=> 'invalid batch'};}
    const requests=JSON.parse(options.payload).requests;
    const staged=new Map(sheets.map(s=>[s.id,structuredClone(s.data)]));
    requests.forEach(r=>{if(!r.updateCells)return;const u=r.updateCells,data=staged.get(u.start.sheetId);
      u.rows.forEach((row,i)=>{data[u.start.rowIndex+i]??=[];row.values.forEach((cell,j)=>{
        const v=cell.userEnteredValue;data[u.start.rowIndex+i][u.start.columnIndex+j]=v.stringValue??v.numberValue??v.boolValue;
      });});});
    sheets.forEach(s=>s.data=staged.get(s.id));return {getResponseCode:()=>200};
  }}
};
const helpers=fs.readFileSync('scripts/own-sheet.gs','utf8').split('function doPost(')[0]
  .replace('__OWN_ID__',book.getId());
const script=fs.readFileSync('scripts/workspace-server.gs','utf8').replace('__APP_SHARED_KEY__','test-key');
vm.createContext(context);vm.runInContext(helpers+'\n'+script,context);
vm.runInContext('delete globalThis.output',context);
let serial=0;
const call=(action,payload={},actor='alice',machine='pc-a')=>context.doPost({postData:{contents:JSON.stringify({
  action,app_key:'test-key',actor,machine_id:machine,client_version:'test',operation_id:(++serial).toString(16).padStart(32,'0'),...payload})}});
const source=(smr,extra={})=>({smr,channel:'online',active:true,fingerprint:'a'.repeat(64),source_status:'PENDING',
  source_loaded_at:new Date().toISOString(),online:{source:'engine',action:'APPROVE'},...extra});
const get=smr=>JSON.parse(book.getSheetByName('Workspace state').data.find(r=>r[0]===smr)[2]);

assert.equal(call('health').sheet_ok,true);
assert.equal(call('health',{},'bob','pc-b').sheet_ok,true);
assert.equal(call('health',{app_key:'wrong'}).error_code,'AUTH_ERROR');
assert.equal(call('source_batch',{cases:[source('SMR-1'),source('SMR-2')]}).cases.length,2);
assert.equal(book.getSheetByName('Workspace state').data.length,3); // multiple inserts must not overwrite one row
assert.equal(book.getSheetByName('Workflow').data.length,3);
const id='a'.repeat(32), c=source('SMR-3');
assert.equal(call('source_batch',{operation_id:id,cases:[c]}).ok,true);
const count=book.getSheetByName('Workspace state').data.length;
assert.equal(call('source_batch',{operation_id:id,cases:[c]}).ok,true);
assert.equal(book.getSheetByName('Workspace state').data.length,count);
assert.equal(call('source_batch',{operation_id:id,cases:[source('SMR-4')]}).ok,false);
failBatch=true;
assert.equal(call('source_batch',{cases:[source('SMR-5')]}).error_code,'HTTP_ERROR');
assert.equal(book.getSheetByName('Workspace state').data.length,count);
const revision=get('SMR-1').revision, claim='b'.repeat(32);
assert.equal(call('claim_execution',{smr:'SMR-1',revision,claim}).ok,true);
assert.equal(call('claim_execution',{smr:'SMR-1',revision,claim:'c'.repeat(32)},'bob','pc-b').ok,false);
assert.equal(call('decide',{smr:'SMR-1',revision,team:'online',decision:'MANUAL',note:'review'}).ok,false);
assert.equal(call('finish_execution',{smr:'SMR-1',revision,claim,state:'SENT'},'bob').ok,false);
assert.equal(call('finish_execution',{smr:'SMR-1',revision,claim,state:'SENT'}).ok,true);
assert.equal(call('claim_execution',{smr:'SMR-1',revision,claim:'d'.repeat(32)},'bob').ok,false);
assert.equal(call('decide',{smr:'SMR-2',revision:99,team:'online',decision:'APPROVE',note:'stale'}).ok,false);
assert.equal(call('decide',{smr:'SMR-2',revision:get('SMR-2').revision,team:'online',decision:'MANUAL',note:'human'}).ok,true);
assert.equal(call('source_batch',{cases:[source('SMR-2')]}).ok,true);
assert.equal(get('SMR-2').online.actor,'alice');
const published=call('publish_release',{version:'1.3.20',url:'https://drive.google.com/file/d/'+'f'.repeat(30)+'/view',
  sha256:'c'.repeat(64),notes:'shared release'},'mohammadreza.vahab');
assert.equal(published.version,'1.3.20');
assert.equal(call('release').releases[0].version,'1.3.20');
assert.equal(call('publish_release',{version:'1.3.21',url:'https://drive.google.com/file/d/'+'e'.repeat(30)+'/view',
  sha256:'d'.repeat(64),notes:''},'alice').error_code,'AUTH_ERROR');
assert.equal(call('publish_release',{version:'1.3.19',url:'https://drive.google.com/file/d/'+'e'.repeat(30)+'/view',
  sha256:'d'.repeat(64),notes:''},'mohammadreza.vahab').ok,false);
vm.runInContext("globalThis.conflict=v5flow({active:true,channel:'both',online:{action:'APPROVE'},instore:{action:'CANCEL'}})",context);
assert.equal(context.conflict,'CONFLICT');
assert.equal(locked,false);
assert.ok(book.getSheetByName('Workspace audit').data.some(r=>r[5]==='NBO_SUCCESS'));
assert.equal(sheets.some(s=>s.name==='Users'),false);
console.log('PASS: actual Apps Script — two devices, atomic batch, replay, stale revision, global claim, audit, conflict.');
