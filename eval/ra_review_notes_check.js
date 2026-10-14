"use strict";
// Exercise persistence/error/export boundaries with a fake DOM, not visual QA.
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const downloads=[],blobs=[];
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.textContent="";this.value="";this.disabled=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=[...children];}
  setAttribute(name,value){this[name]=value;}
  click(){downloads.push({href:this.href,download:this.download});}
}
const data=new Map(),storage={getItem:key=>data.has(key)?data.get(key):null,setItem:(key,value)=>data.set(key,value)};
const sandbox={window:{localStorage:storage},document:{createElement:tag=>new Element(tag)},Blob,
  URL:{createObjectURL:blob=>{blobs.push(blob);return "blob:review";},revokeObjectURL:()=>{}},setTimeout:callback=>callback()};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync("rag_service/static/review-notes.js","utf8")+"\nthis.review=RAReviewNotes;",sandbox);
const api=sandbox.review,idA="a".repeat(32),idB="b".repeat(32),question="원문 근거 검토",malicious="<script>alert('x')</script>";
function walk(element){return [element,...element.children.flatMap(walk)];}
function mount(id){const target=new Element("div");api.mount(target,{jobId:id,question});return walk(target);}
function button(elements,label){return elements.find(e=>e.tag==="button"&&e.textContent===label);}
(async()=>{
  let elements=mount(idA),note=elements.find(e=>e.tag==="textarea"),status=elements.find(e=>e.tag==="select");
  note.value=malicious;status.value="needs_work";button(elements,"검토 의견 저장").onclick();
  assert.equal(api.loadHistory(storage,idA,question).revisions.length,1);
  elements=mount(idA);assert.equal(elements.find(e=>e.tag==="textarea").value,malicious);
  assert(elements.some(e=>e.tag==="pre"&&e.textContent===malicious));assert(!elements.some(e=>e.tag==="script"));
  elements.find(e=>e.tag==="textarea").value="수정 후 원문 재확인";
  elements.find(e=>e.tag==="select").value="checked";button(elements,"검토 의견 저장").onclick();button(elements,"검토 의견 저장").onclick();
  const history=api.loadHistory(storage,idA,question);assert.equal(history.revisions.length,2);assert.equal(history.revisions[0].note,malicious);
  assert.equal(api.loadHistory(storage,idB,question).revisions.length,0);assert.throws(()=>api.loadHistory(storage,idA,"다른 질문"));
  const before=data.get("ra-review-notes-v1:"+idA);
  assert.throws(()=>api.appendRevision(storage,idA,question,"checked","x".repeat(4001)));assert.equal(data.get("ra-review-notes-v1:"+idA),before);
  button(elements,"검토 이력 저장").onclick();assert.equal(downloads.at(-1).download,"ra-review-notes-"+idA+".md");
  assert.equal(await blobs.at(-1).text(),api.historyMarkdown(history));
  const originalSet=storage.setItem;storage.setItem=()=>{throw Error("QuotaExceededError");};
  elements.find(e=>e.tag==="textarea").value="저장 실패 시 남을 의견";button(elements,"검토 의견 저장").onclick();
  assert.equal(data.get("ra-review-notes-v1:"+idA),before);assert.equal(elements.find(e=>e.tag==="textarea").value,"저장 실패 시 남을 의견");
  assert(elements.some(e=>e.textContent.includes("저장하지 못했습니다")));
  storage.setItem=originalSet;data.set("ra-review-notes-v1:"+idB,"{broken");
  const invalid=mount(idB);assert.equal(button(invalid,"검토 의견 저장").disabled,true);assert.equal(data.get("ra-review-notes-v1:"+idB),"{broken");
  assert.throws(()=>api.loadHistory(storage,"../outside",question));
  const app=fs.readFileSync("rag_service/static/app.js","utf8"),report=JSON.parse(fs.readFileSync("data/runs/ra_workflow_update_2026-10-12_v1/final_live_report.json","utf8"));
  sandbox.state={jobId:"c".repeat(32)};
  sandbox.api=undefined;
  vm.runInContext(app.slice(app.indexOf("function node("),app.indexOf("async function api("))+app.slice(app.indexOf("function renderMemo("),app.indexOf("function renderResearch(")),sandbox);
  const serialized=JSON.stringify(report),target=new Element("div");sandbox.renderMemo(report,target);
  const integrated=walk(target);assert(button(integrated,"검토 의견 저장"));
  integrated.find(e=>e.tag==="textarea").value="자체 인용 확인";button(integrated,"검토 의견 저장").onclick();
  assert.equal(api.loadHistory(storage,sandbox.state.jobId,report.question).revisions[0].note,"자체 인용 확인");
  button(integrated,"검토 메모 저장").onclick();assert.equal(await blobs.at(-1).text(),report.review_memo.markdown);
  assert.equal(JSON.stringify(report),serialized);
  let serverHistory={schema_version:1,job_id:idB,question,revisions:[]},conflict=false,posts=0;
  const serverApi=async(path,options)=>{assert.equal(path,"/api/jobs/"+idB+"/review");
    if(options){posts++;if(conflict)throw Error("409: 다른 창에서 수정됨");const payload=JSON.parse(options.body);
      assert.equal(payload.expected_revision,serverHistory.revisions.length);
      serverHistory={...serverHistory,revisions:[...serverHistory.revisions,{...payload,revision:serverHistory.revisions.length+1,saved_at:"2026-10-12T06:00:00Z"}]};}
    return structuredClone(serverHistory);};
  data.delete("ra-review-notes-v1:"+idB);
  const serverTarget=new Element("div"),originalMemo="# 원래 검토 메모\n";
  await api.mount(serverTarget,{jobId:idB,question,api:serverApi,meetingQuestions:[{issue_id:"I1",question_ko:"원래 질문"}],memoMarkdown:originalMemo});
  const serverElements=walk(serverTarget),inputs=serverElements.filter(e=>e.tag==="textarea");
  inputs[0].value=malicious;inputs[1].value="보완된 미팅 질문";await button(serverElements,"검토 의견 저장").onclick();
  assert.equal(serverHistory.revisions.length,1);assert.equal(serverHistory.revisions[0].meeting_questions[0].question_ko,"보완된 미팅 질문");
  assert(serverElements.some(e=>e.tag==="textarea"&&e.value===malicious));
  button(serverElements,"검토본 메모 저장").onclick();assert.equal(await blobs.at(-1).text(),originalMemo+"\n---\n\n"+api.historyMarkdown(serverHistory));
  conflict=true;inputs[0].value="충돌 후 남을 의견";inputs[1].value="충돌 후 남을 질문";await button(serverElements,"검토 의견 저장").onclick();
  assert.equal(serverHistory.revisions.length,1);assert.equal(inputs[0].value,"충돌 후 남을 의견");assert.equal(inputs[1].value,"충돌 후 남을 질문");
  assert.equal(posts,2);assert.equal(JSON.stringify(report),serialized);
  process.stdout.write(JSON.stringify({saved_and_reopened:true,append_only_revisions:true,unchanged_save_deduplicated:true,
    separate_job_histories:true,question_binding:true,literal_text_rendering:true,exact_markdown_export:true,
    storage_failure_preserves_input_and_history:true,invalid_history_preserved:true,input_and_id_limits:true,
    actual_memo_integration:true,server_review_and_editable_questions:true,server_conflict_preserves_draft:true,reviewed_memo_exact_export:true,original_report_and_memo_preserved:true,
    isolated_fake_dom:true,browser_visual_verification:false},null,2));
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
