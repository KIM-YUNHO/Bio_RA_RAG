"use strict";
// Isolated UI fixtures only. Real deletion/locking/HTTP tests use a disposable DB.
const {parseHTML,HTMLElement}=require("linkedom"),{readFileSync}=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const {document,window}=parseHTML(readFileSync("rag_service/web/index.html","utf8"));
HTMLElement.prototype.showModal=function(){this.open=true;};
HTMLElement.prototype.close=function(){this.open=false;this.dispatchEvent(new window.Event("close"));};
HTMLElement.prototype.focus=function(){};
const storage=new Map(),timers=new Map(),calls=[];let timerId=0,failDelete=false,deferOverview=false,resolveOverview;
const localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v))};
const ids=[1,2,3].map(n=>`10000000-0000-0000-0000-00000000000${n}`),rids=[1,2].map(n=>`20000000-0000-0000-0000-00000000000${n}`);
const sessions=new Map(ids.map((id,i)=>[id,{id,updated_at:"2026-10-16T00:00:00Z",can_delete:i!==2,
  state:{stage:i===2?"review":"idle",target:{ingredient:"ustekinumab",product_id:"TEST"+(i+1)}},turns:[],
  request:i?{id:rids[i-1],status:i===1?"done":"awaiting_approval",reports:{}}:null}]));
const requests=rids.map((id,i)=>({id,status:i?"awaiting_approval":"done",created_at:"2026-10-16T00:00:00Z",products:[{product_id:"REQUEST"+(i+1)}],payload:{products:[{product_id:"REQUEST"+(i+1)}]},events:[]}));
const copy=v=>JSON.parse(JSON.stringify(v)),response=(v,ok=true)=>({ok,json:async()=>copy(v)});
const overview=()=>({sessions:[...sessions.values()].filter(s=>!s.deleted).map(s=>({id:s.id,target:s.state.target,updated_at:s.updated_at,request_id:s.request?.id,request_status:s.request?.status,can_delete:s.can_delete})),requests,releases:[],service:{product_index_chunks:10}});
const fetch=async(path,options={})=>{
  const body=options.body?JSON.parse(options.body):null;calls.push({path,method:options.method||"GET",body});
  if(path==="/api/ingestion"){
    if(deferOverview){deferOverview=false;const stale=copy(overview());return new Promise(resolve=>{resolveOverview=()=>resolve(response(stale));});}
    return response(overview());
  }
  const match=path.match(/^\/api\/ingestion\/sessions\/([^/]+)(\/delete)?$/);
  if(match){const s=sessions.get(match[1]);assert.ok(s,"fixture session exists");
    if(match[2]){assert.equal(options.method,"POST");assert.deepEqual(body,{});if(failDelete)return response({error:"삭제 검증 오류"},false);s.deleted=true;return response({id:s.id,deleted:true});}
    return s.deleted?response({error:"missing"},false):response(s);
  }
  const r=requests.find(r=>path===`/api/ingestion/requests/${r.id}`);if(r)return response(r);
  throw Error("Unexpected endpoint: "+path);
};
storage.set("ra-ui:ingestionView",JSON.stringify({kind:"session",id:ids[0]}));storage.set("ra-ui:ingestionSession",JSON.stringify(ids[0]));
const context=vm.createContext({window,document,localStorage,fetch,console,URL,location:{href:"http://127.0.0.1:8775/"},setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id)});
for(const file of ["common.js","data-manager.js"])vm.runInContext(readFileSync("rag_service/web/"+file,"utf8"),context,{filename:file});
const wait=async condition=>{for(let i=0;i<200;i++){if(condition())return;await new Promise(r=>setImmediate(r));}throw Error("DOM update timed out");};
const poll=async()=>{const [id,fn]=[...timers].at(-1);timers.delete(id);await fn();};
const row=id=>document.querySelector(`.collection-session-row[data-session-id="${id}"]`);
const remove=()=>document.querySelector(".collection-delete-confirm-button").click();
const deleteCalls=()=>calls.filter(c=>c.path.endsWith("/delete"));
(async()=>{
  vm.runInContext("DataManager.init()",context);document.querySelector("#data-manager-open").click();await wait(()=>document.querySelector(".ingestion-composer textarea")&&!document.querySelector(".collection-delete-action").disabled);
  assert.equal(row(ids[2]).querySelector(".collection-session-delete").disabled,true);
  document.querySelector(".collection-sessions").open=true;document.querySelector(".collection-releases").open=true;document.querySelector(".collection-sidebar-list").scrollTop=75;
  const input=document.querySelector(".ingestion-composer textarea");input.value="작성하던 요청";input.oninput();
  document.querySelector(".collection-delete-action").click();assert.equal(document.querySelector(".collection-delete-confirm").hidden,false);
  [...document.querySelectorAll(".collection-delete-confirm button")].find(b=>b.textContent==="취소").click();assert.equal(deleteCalls().length,0);assert.equal(input.value,"작성하던 요청");
  document.querySelector(".collection-delete-action").click();await poll();assert.equal(document.querySelector(".collection-delete-confirm").hidden,false);
  failDelete=true;remove();await wait(()=>document.querySelector(".data-manager-dialog > .pipeline-message").textContent==="삭제 검증 오류");assert.equal(document.querySelector(".collection-delete-error[role=alert]").textContent,"삭제하지 못했습니다. 삭제 검증 오류");assert.ok(row(ids[0]));assert.equal(document.querySelector(".ingestion-composer textarea").value,"작성하던 요청");
  // A poll started before deletion cannot put the deleted conversation back.
  failDelete=false;deferOverview=true;const stalePoll=poll();await wait(()=>resolveOverview);remove();await wait(()=>!row(ids[0]));resolveOverview();await stalePoll;
  assert.equal(row(ids[0]),null);assert.ok(document.querySelector(".collection-welcome").textContent.includes("삭제했습니다"));
  assert.equal(document.querySelector(".collection-sessions").open,true);assert.equal(document.querySelector(".collection-releases").open,true);assert.equal(document.querySelector(".collection-sidebar-list").scrollTop,75);
  assert.equal(JSON.parse(storage.get("ra-ui:ingestionSession")),"");assert.equal(JSON.parse(storage.get("ra-ui:ingestionView")),null);
  assert.equal(document.querySelectorAll(".collection-request").length,2);assert.equal(row(ids[2]).querySelector(".collection-session-delete").disabled,true);
  document.querySelector(".collection-sessions").open=false;await poll();assert.equal(document.querySelector(".collection-sessions").open,false);assert.equal(document.querySelector(".collection-releases").open,true);
  document.querySelector(".collection-sessions").open=true;
  // Delete another conversation while keeping the selected request and its data.
  [...document.querySelectorAll(".collection-request")].find(b=>b.textContent.includes("REQUEST1")).click();await wait(()=>document.querySelector(".collection-subject")&&!row(ids[1]).querySelector(".collection-session-delete").disabled);
  const requestCount=requests.length;row(ids[1]).querySelector(".collection-session-delete").click();remove();await wait(()=>!row(ids[1]));await wait(()=>document.querySelector(".collection-footer").textContent.includes("연결된 적재 대화가 없습니다"));assert.equal(requests.length,requestCount);assert.ok(document.querySelector(".collection-subject").textContent.includes("REQUEST1"));
  assert.equal(document.querySelector(".collection-sessions").open,true);assert.equal(document.querySelector(".collection-releases").open,true);
  row(ids[2]).querySelector(".data-manager-item").click();await wait(()=>document.querySelector(".collection-delete-action"));assert.equal(document.querySelector(".collection-delete-action").disabled,true);
  document.querySelector(".data-manager-dialog").close();document.querySelector("#data-manager-open").click();await wait(()=>row(ids[2]));assert.equal(row(ids[0]),null);assert.equal(row(ids[1]),null);
  assert.equal(deleteCalls().filter(c=>c.path.includes(ids[2])).length,0);assert.equal(calls.filter(c=>c.path.endsWith("/approve")||c.path.endsWith("/turns")).length,0);
  document.querySelector(".data-manager-dialog").close();assert.equal(timers.size,0);
  console.log(JSON.stringify({result:"passed",checks:["confirm/cancel","failure preserves conversation and draft","poll-safe deletion","selection and preferences cleared","other request stays open","active request protected","deleted conversation stays gone after reopen","no model/worker/approval calls"]}));
})().catch(error=>{console.error(error);process.exitCode=1;});
