"use strict";
// DOM integration for the internal-documents dialog (work-assist.js) against a running service. Read-only (GET only).
// linkedom VM, not browser rendering. Default: the v4 scenario (status board, documents, a tab without issues).
// --issue: the v5 scenario with a completed issue guide already on the server (banner, issue card, guide, answer-key review).
const {parseHTML,HTMLSelectElement,HTMLInputElement,HTMLTextAreaElement,HTMLElement}=require("linkedom");
const vm=require("vm"),assert=require("node:assert/strict"),{webcrypto}=require("node:crypto");
const [base,projectId,flag]=process.argv.slice(2),issueMode=flag==="--issue";
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||"")||!/^[a-f0-9]{32}$/.test(projectId||""))throw Error("Use a loopback URL and a project id");
Object.defineProperty(HTMLSelectElement.prototype,"value",{get(){const options=[...this.querySelectorAll("option")];return options.find(o=>o.hasAttribute("selected"))?.getAttribute("value")||options[0]?.getAttribute("value")||"";},set(value){for(const option of this.querySelectorAll("option"))option.toggleAttribute("selected",option.getAttribute("value")===String(value));}});
Object.defineProperty(HTMLInputElement.prototype,"checked",{get(){return this.hasAttribute("checked");},set(value){this.toggleAttribute("checked",!!value);}});
Object.defineProperty(HTMLTextAreaElement.prototype,"value",{get(){return this.textContent;},set(value){this.textContent=String(value);}});
HTMLElement.prototype.scrollIntoView=function(){};
HTMLElement.prototype.showModal=function(){this.open=true;this.setAttribute("open","");};
HTMLElement.prototype.close=function(){this.open=false;this.removeAttribute("open");this.dispatchEvent(new this.ownerDocument.defaultView.Event("close"));};
const storage=new Map(),localStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,String(v))},requests=[];
const localFetch=(path,options={})=>{const method=(options.method||"GET").toUpperCase();assert.equal(method,"GET","read-only verification");const url=new URL(path,base);assert.equal(url.origin,base);requests.push(method+" "+url.pathname);return fetch(url,options);};
const wait=async(condition,label)=>{const until=Date.now()+15000;while(!condition()){if(Date.now()>until)throw Error("Timed out: "+label);await new Promise(r=>setTimeout(r,15));}};
const tab=(dialog,label)=>{const b=[...dialog.querySelectorAll(".work-tab")].find(b=>b.textContent===label);assert.ok(b,label);b.click();};

async function workFlow(document,line,checks){
  assert.match(line.textContent,/^업무 현황T4 · 2026-05-15 기준업무 8개 · .+열기 ›$/);
  line.click();
  const dialog=document.querySelector(".work-dialog");assert.ok(dialog);
  await wait(()=>dialog.querySelector("#work-as-of"),"as-of selector");
  assert.ok(dialog.querySelector(".work-disclosure summary").textContent.includes("SB17")&&dialog.querySelector(".work-disclosure").textContent.includes("합성"));
  const select=dialog.querySelector("#work-as-of");
  assert.ok([...select.querySelectorAll("option")].some(o=>o.textContent.startsWith("T3 · 2026-04-21")));
  select.value="2026-04-21|T3";select.onchange();
  tab(dialog,"진행 현황");
  await wait(()=>dialog.querySelector(".work-list")&&dialog.querySelector(".work-status").textContent.includes("3건"),"T3 work list");
  const row=title=>[...dialog.querySelectorAll(".work-row")].find(r=>r.querySelector(".work-row-title strong").textContent.startsWith(title));
  const cpb=row("IV CPB");
  assert.equal(cpb.querySelector(".work-pill").textContent,"진행 중");
  assert.ok(cpb.querySelector(".work-row-sub").textContent.includes("다음: 결과 예정 05/08(서면) · 04/24에서 변경"));
  assert.ok(cpb.querySelector(".work-badge.is-new"),"new since T2");
  assert.equal(cpb.querySelectorAll(".work-timeline li").length,4);
  assert.ok(cpb.querySelector(".work-row-body").textContent.includes("아직 없음"));
  const rerun=row("IV FcRn 독립 재분석");assert.equal(rerun.querySelector(".work-pill").textContent,"완료");
  assert.ok(rerun.querySelector(".work-result-unit").textContent.includes("98.3%"));assert.ok(rerun.querySelector(".work-opinion").textContent.includes("이시험"));
  assert.equal(row("유사성 범위 재산정안").querySelector(".work-pill").textContent,"QA 미승인");
  assert.equal([...dialog.querySelectorAll(".work-card-group h3")].map(h=>h.textContent).join("|"),"시험·작업|문서·답변");
  assert.ok(!row("PFS 전하 변이").querySelector(".work-badge"),"restated record is not progress");
  assert.ok(row("PFS 전하 변이").querySelector(".work-timeline li.is-restated"),"restated record kept in history");
  assert.ok(dialog.querySelector(".work-summary").textContent.includes("T2(03/27) 이후 새 업무 3 · 진행된 업무 0"));
  assert.equal(dialog.querySelector(".work-issue-banner"),null,"no issue in v4");
  [...cpb.querySelectorAll(".work-chip")].find(c=>c.textContent==="U17").click();
  assert.ok(cpb.querySelector(".work-unit-detail").textContent.includes("4월 24일경"));
  tab(dialog,"문서");
  await wait(()=>dialog.querySelectorAll(".work-doc").length===17,"17 documents on T3");
  const draftProtocol=[...dialog.querySelectorAll(".work-doc")].find(d=>d.textContent.includes("1.1-draft"));
  assert.ok(draftProtocol.textContent.includes("미승인 개정안(적용 안 됨)"));
  assert.ok([...dialog.querySelectorAll(".work-doc")].find(d=>d.textContent.includes("PROT-P17-FA-001 v1.0")).textContent.includes("현재 기준"));
  assert.ok(!dialog.textContent.includes("RPT-HB-26-0508"),"future report hidden");
  tab(dialog,"이슈 대응");
  await wait(()=>dialog.querySelector(".work-panel").textContent.includes("등록된 이슈가 없습니다"),"no-issue message");
  checks.push("board line opens the window on its date","as-of options from scenario checkpoints","work list: one status, summary and what it waits for","history, results and opinions per work item","restated record kept as history, not progress","source chip opens unit text","documents hidden after the date","current vs unapproved protocol badges","issue tab without issues");
  dialog.close();
  await wait(()=>line.textContent.startsWith("업무 현황T3 · 2026-04-21 기준업무 7개"),"board line follows the date chosen in the window");
  checks.push("board line follows the date chosen in the window");
}

async function issueFlow(document,line,checks){
  assert.match(line.textContent,/^업무 현황T1 · 2026-03-20 기준업무 3개 · .+ · 이슈 1열기 ›$/);
  line.click();
  const dialog=document.querySelector(".work-dialog");assert.ok(dialog);
  await wait(()=>dialog.querySelector("#work-as-of"),"as-of selector");
  tab(dialog,"진행 현황");
  await wait(()=>dialog.querySelector(".work-issue-banner"),"issue banner on the board");
  assert.ok(dialog.querySelector(".work-issue-banner").textContent.includes("130 mg IV vial FcRn 결합이 유사성 범위 하한 미만"));
  dialog.querySelector(".work-issue-banner").click();
  await wait(()=>dialog.querySelector(".issue-card"),"issue card");
  const card=dialog.querySelector(".issue-card");
  assert.deepEqual([...card.querySelectorAll(".work-chip")].map(c=>c.textContent),["U01","U02","U03","U04","U05","U07"]);
  assert.ok(card.querySelector(".issue-scope").textContent.includes("SB17"),"held-out product is stated");
  assert.equal(card.querySelectorAll(".issue-questions li").length,4);
  [...card.querySelectorAll(".work-chip")].find(c=>c.textContent==="U03").click();
  assert.ok(card.querySelector(".work-unit-detail").textContent.includes("98.0%"));
  await wait(()=>dialog.querySelector(".work-history-item"),"guide history");
  dialog.querySelector(".work-history-item").click();
  await wait(()=>dialog.querySelector(".issue-sections"),"guide rendered");
  assert.deepEqual([...dialog.querySelectorAll(".issue-section h3")].map(h=>h.textContent),["원인 규명 방향","실험 설계","기관 대응 논리","예상 심사 질의","다른 제품 사례"]);
  assert.ok(dialog.querySelector(".issue-result .issue-scope").textContent.includes("검색에서 뺀 제품: SB17"));
  assert.ok(dialog.querySelector(".work-chip.is-public").textContent.startsWith("P1 · BMAB1200 EMA p."),"public source chip");
  assert.ok(dialog.querySelector(".work-badge.is-ai"),"AI suggestion badge");
  assert.ok(dialog.querySelector(".issue-q").textContent.startsWith("Q. "));
  assert.ok(dialog.querySelector(".work-warnings").textContent.includes("공개 근거로 표시했지만"));
  assert.equal(dialog.querySelectorAll(".issue-answer").length,4);
  const review=dialog.querySelector(".issue-review");
  assert.ok(review.querySelector("summary").textContent.includes("정답 대조(평가용)"));
  assert.equal(review.querySelectorAll("li").length,5);
  assert.ok(review.querySelector("li.is-covered"),"covered point marked");
  checks.push("board line counts the issue","issue banner opens the issue tab","issue card: internal facts, questions, held-out scope","guide sections with public/internal chips and badges","audit warning listed","search answers listed","answer-key review with covered points");
  dialog.close();
}

(async()=>{
  const html=await(await localFetch("/")).text(),{document,window}=parseHTML(html);
  const location=new URL(`${base}/?project=${projectId}&stage=cmc`),history={replaceState(_a,_b,url){location.href=new URL(url,location).href;}};
  const context=vm.createContext({window,document,localStorage,location,history,crypto:webcrypto,URL,Blob,console,setTimeout,clearTimeout,fetch:localFetch,CustomEvent:window.CustomEvent});
  for(const script of document.querySelectorAll("script[src]"))vm.runInContext(await(await localFetch(script.getAttribute("src"))).text(),context,{filename:script.getAttribute("src")});
  await wait(()=>document.querySelector("#workspace-project").value===projectId&&!document.querySelector("#memo-work").disabled,"project loaded and work button enabled");
  // The board's one-line summary uses the window's date and counts, and opens the window.
  const line=document.querySelector("#pipeline-work");await wait(()=>!line.hidden,"pipeline work line");
  const checks=[];
  await (issueMode?issueFlow:workFlow)(document,line,checks);
  console.log(JSON.stringify({result:"passed",mode:"DOM_not_browser_rendering",scenario:issueMode?"issue":"work",projectId,checks,requests:requests.length}));
})().catch(error=>{console.error(error);process.exitCode=1;});
