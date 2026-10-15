"use strict";
// Optional test-only linkedom dependency via NODE_PATH. DOM test, not rendering.
// HTTP and worker state are simulated; real DB/agent coverage is in test_workspace_postgres.py.
const {parseHTML,HTMLElement}=require("linkedom");
const {readFileSync}=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const {document,window}=parseHTML(readFileSync("rag_service/web/index.html","utf8"));
HTMLElement.prototype.showModal=function(){this.open=true;};
HTMLElement.prototype.close=function(){this.open=false;this.dispatchEvent(new window.Event("close"));};
const storage=new Map(),timers=new Map(),calls=[];let nextTimer=0,failTurn=false;
const localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v))};
const id="10000000-0000-0000-0000-000000000001",rid="20000000-0000-0000-0000-000000000001",hash="a".repeat(64);
const target={key:"CODE:TEST1",product_id:null,names:{EU:"Test product"},ingredient:"ustekinumab"};
let session={id,updated_at:"2026-10-15T00:00:00Z",state:{stage:"choosing",candidates:[target],target:null,shown_reports:{}},turns:[],request:null,plan_sha256:null};
const request={id:rid,status:"queued",created_at:"2026-10-15T00:00:00Z",payload:{ingredient:"ustekinumab",jurisdictions:["EU","US"],products:[{product_id:"TEST1",ema_name:"Test <img src=x>"}]},search_note:"수집 완료와 검색 반영은 별도입니다.",events:[]};
const report="-- EMA: 2 documents, 12 pages/sections\n  [core ] Test <img src=x> report (8, https://example.test/report.pdf)\n  [label] Product information (4, https://example.test/label.pdf)\n-- FDA: 1 documents, 6 pages/sections\n  [core] Clinical review (6, https://example.test/review.pdf)\n-- ClinicalTrials.gov: 2 documents, 10 pages/sections\n  [protocol] Trial protocol (6, https://example.test/protocol.pdf)\n  [sap] Statistical analysis plan (4, https://example.test/sap.pdf)\n-- trial NCT00000001";
const copy=value=>JSON.parse(JSON.stringify(value));
const response=(value,ok=true)=>({ok,json:async()=>copy(value)});
const fetch=async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):null;calls.push({path,body});
  if(path==="/api/ingestion/sessions")return response({id:"30000000-0000-0000-0000-000000000001",state:{stage:"idle",target:null},turns:[],request:null});
  if(path==="/api/ingestion")return response({database_ready:true,sessions:[{id,target:session.state.target,updated_at:session.updated_at,request_id:session.request?.id}],requests:session.request?[{id:rid,status:request.status,products:[{product_id:"TEST1"}]}]:[],releases:[],service:{product_index_chunks:10}});
  if(path===`/api/ingestion/sessions/${id}`)return response(session);
  if(path===`/api/ingestion/sessions/${id}/actions`){
    if(body.action==="select"){assert.equal(body.candidate_key,target.key);session.state={...session.state,stage:"planned",target,candidates:[],plan:{request:{ingredient:"ustekinumab",products:[{product_id:"TEST1"}],jurisdictions:["EU"]}}};session.plan_sha256=hash;}
    else{assert.deepEqual(body,{action:"start",plan_sha256:hash});session.state.stage="staging";session.request={id:rid,status:"queued",reports:{}};session.plan_sha256=null;}
    return response(session);
  }
  if(path===`/api/ingestion/sessions/${id}/turns`){
    if(failTurn)return response({error:"fixture request error"},false);
    assert.equal(body.text,"/status");session.state.stage="review";session.state.shown_reports={TEST1:hash};session.turns.push({user_text:"/status",reply:"확인표: 원문·문서 목록"});return response(session);
  }
  if(path===`/api/ingestion/sessions/${id}/approve`){assert.deepEqual(body,{product_id:"TEST1",report_sha256:hash});session.state.stage="promoting";session.request.status=request.status="approved";return response(session);}
  if(path===`/api/ingestion/requests/${rid}`)return response(request);
  if(path===`/api/ingestion/requests/${rid}/logs`)return response({logs:[{name:"worker.log",text:"worker connected"}],note:"작업 로그"});
  throw Error("Unexpected endpoint: "+path);
};
const context=vm.createContext({window,document,localStorage,fetch,console,URL,location:{href:"http://127.0.0.1:8775/"},setTimeout:fn=>{timers.set(++nextTimer,fn);return nextTimer;},clearTimeout:id=>timers.delete(id)});
for(const file of ["common.js","data-manager.js"])vm.runInContext(readFileSync("rag_service/web/"+file,"utf8"),context,{filename:file});
const wait=async condition=>{for(let n=0;n<100;n++){if(condition())return;await new Promise(resolve=>setImmediate(resolve));}throw Error("DOM update timed out");};
const textButton=text=>[...document.querySelectorAll("button")].find(b=>b.textContent===text);
const click=text=>{const b=textButton(text);assert.ok(b,"Missing button: "+text);b.click();};
const poll=async()=>{const entry=[...timers].at(-1);assert.ok(entry,"poll scheduled");timers.delete(entry[0]);await entry[1]();};
(async()=>{
  vm.runInContext("DataManager.init()",context);document.querySelector("#data-manager-open").click();
  await wait(()=>textButton("새 적재 대화 · 2026. 10. 15."));click("새 적재 대화 · 2026. 10. 15.");
  await wait(()=>document.querySelector(".data-manager-candidates button"));document.querySelector(".data-manager-candidates button").click();
  await wait(()=>textButton("계획대로 스테이징 수집 시작"));
  assert.equal(calls.filter(c=>c.path.endsWith("/turns")).length,0);
  let input=document.querySelector(".ingestion-composer textarea");input.value="작성 중인 요청";input.oninput();session.updated_at="2026-10-15T00:01:00Z";await poll();assert.equal(document.querySelector(".ingestion-composer textarea").value,"작성 중인 요청");
  click("계획대로 스테이징 수집 시작");await wait(()=>document.querySelector(".ingestion-progress strong")?.textContent==="수집 준비");
  session.request.status=request.status="awaiting_approval";session.request.reports={TEST1:hash};request.events.push({event:"staged",at:"2026-10-15T00:02:00Z",payload:{product_id:"TEST1",report_sha256:hash,report}});await poll();
  assert.equal(document.querySelector(".ingestion-progress strong").textContent,"확인표 검토 대기");assert.equal(textButton("TEST1 확인표 승인·자료 등록"),undefined);
  click("이벤트·로그 보기");await wait(()=>textButton("확인표 검토·자료 등록"));assert.equal(calls.filter(c=>c.path.endsWith("/turns")).length,0);click("확인표 검토·자료 등록");await wait(()=>textButton("TEST1 확인표 승인·자료 등록"));assert.equal(calls.filter(c=>c.path.endsWith("/approve")).length,0);click("TEST1 확인표 승인·자료 등록");await wait(()=>document.querySelector(".ingestion-progress strong").textContent==="등록 준비");assert.equal(textButton("TEST1 확인표 승인·자료 등록"),undefined);
  failTurn=true;input=document.querySelector(".ingestion-composer textarea");input.value="보관할 실패 요청";input.oninput();document.querySelector(".ingestion-composer").dispatchEvent(new window.Event("submit",{cancelable:true}));await wait(()=>document.querySelector(".data-manager-dialog .pipeline-message").textContent==="fixture request error");await poll();assert.equal(document.querySelector(".data-manager-dialog .pipeline-message").textContent,"fixture request error");assert.equal(document.querySelector(".ingestion-composer textarea").value,"보관할 실패 요청");
  click("이벤트·로그 보기");await wait(()=>textButton("이 요청의 적재 대화·확인표 열기"));
  assert.deepEqual([...document.querySelectorAll(".collection-stat strong")].map(x=>x.textContent),["5","2","1"]);assert.equal(document.querySelectorAll(".collection-source-table tbody tr").length,3);
  assert.equal(document.querySelectorAll(".collection-document").length,5);assert.equal(document.querySelector(".collection-document a").href,"https://example.test/report.pdf");assert.equal(document.querySelector(".collection-document img"),null);
  const fda=[...document.querySelectorAll(".collection-document-filters button")].find(b=>b.textContent==="FDA");fda.click();assert.equal(document.querySelectorAll(".collection-document").length,1);
  const search=document.querySelector(".collection-search input");search.value="missing product";search.oninput();assert.equal(document.querySelectorAll(".collection-request").length,0);search.value="";search.oninput();assert.equal(document.querySelectorAll(".collection-request").length,1);
  session.request.status=request.status="done";await poll();assert.equal(document.querySelector(".data-manager-section-head h3").textContent,"자료 등록 완료");assert.equal(document.querySelectorAll(".collection-document").length,1);
  const logs=document.querySelector(".ingestion-logs");logs.open=true;await logs.ontoggle();assert.ok(logs.textContent.includes("worker connected"));
  document.querySelector(".data-manager-dialog").close();assert.equal(timers.size,0);
  document.querySelector("#data-manager-open").click();await wait(()=>document.querySelector(".collection-subject"));assert.equal(document.querySelector(".data-manager-section-head h3").textContent,"자료 등록 완료");click("이 요청의 적재 대화·확인표 열기");await wait(()=>document.querySelector(".ingestion-composer textarea"));assert.equal(document.querySelector(".ingestion-composer textarea").value,"보관할 실패 요청");assert.equal(document.querySelector(".ingestion-progress strong").textContent,"자료 등록 완료");
  session.request.status=request.status="failed";request.events=[{event:"staged",at:"2026-10-15T00:02:00Z",payload:{product_id:"TEST1",report:"Unknown report format"}}];click("이벤트·로그 보기");await wait(()=>document.querySelector(".collection-stats"));assert.deepEqual([...document.querySelectorAll(".collection-stat strong")].map(x=>x.textContent),["—","—","—"]);assert.equal(document.querySelectorAll(".collection-step.complete").length,0);
  click("+ 새 수집 요청");await wait(()=>document.querySelector(".ingestion-composer textarea"));assert.equal(calls.filter(c=>c.path==="/api/ingestion/sessions").length,1);document.querySelector(".data-manager-dialog").close();
  assert.equal(calls.filter(c=>c.path.endsWith("/turns")&&c.body.text!=="/status").length,1);
  console.log(JSON.stringify({result:"passed",checks:["service connection","candidate control","plan start control","live request polling","review before approval","approval status","draft preservation","persistent errors","event/log view","request and session restore","report-derived counts","agency document filters","request search","plain-text safety"]}));
})().catch(error=>{console.error(error);process.exitCode=1;});
