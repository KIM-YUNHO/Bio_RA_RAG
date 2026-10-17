"use strict";
const state={summary:null,busy:false,jobId:null,conversationId:null,turns:0};
const VISUAL_REQUEST=/표|그림|도표|figure|table|graph|그래프/i;

function busy(value){state.busy=value;$("submit").disabled=value;$("question").disabled=value;$("include-nondefault").disabled=value;Inventory.setDisabled(value);}
function showError(message){$("error").textContent=message;$("error").hidden=false;}
function clearError(){$("error").hidden=true;}
function chatLog(){return $("chat-log");}
function scopeFromReport(report){
  const u=report.understanding||{},products=(u.products||[]).join("·")||"전체 제품",agencies=(u.jurisdictions||[]).map(j=>AGENCIES[j]||j).join("·")||"전체 기관";
  const narrowed=Object.keys(u.request_scope?.narrowed_by_question||{}).length;
  return `${products} × ${agencies}${narrowed?" (질문에서 좁힘)":""}`;
}
function setScopeChip(turn,text){const chip=turn.querySelector(".scope-chip");if(chip&&text)chip.textContent="검색 범위: "+text;}
function conversationState(){
  const box=$("conversation-state");box.replaceChildren();box.hidden=!state.conversationId||!state.turns;
  if(box.hidden){$("question").placeholder=$("question").dataset.first;return;}
  box.append(node("span","conversation-state-text",`대화 이어가는 중 · ${state.turns}턴 · 다음 질문은 앞 질문의 제품·기관을 이어받습니다`),button("새 대화","link-button",newChat));
  $("question").placeholder="이어서 질문하세요. 예: 그럼 FDA는?  (제품·기관을 새로 적으면 그 값으로 바뀝니다)";
}
function interpretationBox(report){
  const c=report.conversation;if(!c||c.turn_kind!=="follow_up")return null;
  const box=node("div","interpretation"),inherited=c.inherited||{};
  box.append(node("span","interpretation-label","이렇게 이해했습니다"),node("p","interpretation-text",c.standalone_question));
  const carried=[...(inherited.products||[]),...(inherited.jurisdictions||[]).map(j=>AGENCIES[j]||j)];
  if(carried.length)box.append(node("span","scope-chip carried","앞 질문에서 이어받음: "+carried.join(" · ")));
  if(String(c.standalone_check||"").startsWith("fallback"))box.append(node("p","validation-note","모델이 쓴 문장이 범위 검사를 통과하지 못해, 이어받은 제품·기관을 원래 질문 앞에 붙였습니다."));
  else if(c.standalone_check==="standalone_with_inherited_names_added")box.append(node("p","validation-note","이어받은 제품·기관 이름이 빠져 있어 앞에 붙였습니다."));
  box.append(button("이 문장을 고쳐서 다시 묻기","link-button",()=>{$("question").value=c.standalone_question;$("question").focus();}));
  return box;
}
function startTurn(question,scopeText){
  $("empty").hidden=true;const turn=node("article","turn"),bubble=node("div","bubble user");
  bubble.append(node("p","user-text",question));if(scopeText)bubble.append(node("span","scope-chip","범위: "+scopeText));
  turn.append(bubble);chatLog().append(turn);turn.scrollIntoView({block:"start",behavior:"smooth"});return turn;
}
function pending(turn){
  const box=node("div","bubble answer progress"),stage=node("strong"),message=node("p");box.setAttribute("role","status");box.setAttribute("aria-live","polite");box.setAttribute("aria-atomic","true");
  const text=node("div","progress-copy");text.append(stage,message);const spinner=node("span","spinner");spinner.setAttribute("aria-hidden","true");box.append(spinner,text);turn.append(box);
  return {box,update(job){stage.textContent=STAGES[job.stage]||job.stage;message.textContent=(job.message||"")+(job.total?` (${job.current}/${job.total})`:"");}};
}
function citedList(result,ids){return ids.filter(id=>result.evidence[id]).map(id=>({eid:id,title:result.evidence[id].title,page:result.evidence[id].pdf_page}));}
function opener(jobId,resultIndex,result,ids){const cited=citedList(result,ids);return eid=>Viewer.open({jobId,resultIndex,eid,cited:cited.some(c=>c.eid===eid)?cited:[...cited,...citedList(result,[eid])]});}
function answerText(text,result,onCite){const box=node("div","answer-text");box.append(EvidenceView.linkify(text||"",new Set(Object.keys(result.evidence||{})),onCite));return box;}
function visuals(target,{jobId,resultIndex,result,cited,quotesFor,question,onOpen}){
  const shown=cited.filter(id=>EvidenceView.isVisual(result.evidence[id]));
  for(const eid of shown)target.append(EvidenceView.visualCard({jobId,resultIndex,eid,evidence:result.evidence[eid],quotes:quotesFor(eid),onOpen,onPin:()=>PipelineNotes.pin({jobId,resultIndex,kind:"evidence",evidenceId:eid,body:`${result.evidence[eid].title} · p.${result.evidence[eid].pdf_page}`,products:[result.evidence[eid].product_id].filter(Boolean)})}));
  if(!VISUAL_REQUEST.test(question||""))return;
  const related=Object.keys(result.evidence).filter(id=>!shown.includes(id)&&EvidenceView.isVisual(result.evidence[id]));
  if(!related.length)return;
  const details=node("details","scope-details");details.append(node("summary","",`관련 표·그림 ${related.length}개 (답변이 인용하지 않은 검색 결과)`));
  for(const eid of related)details.append(EvidenceView.visualCard({jobId,resultIndex,eid,evidence:result.evidence[eid],quotes:[],onOpen}));
  target.append(details);
}
function citationBox(id,evidence,quotes,onOpen){
  const meta=evidence.document_metadata||{},box=node("div","citation");
  box.append(node("strong","",`[${id}] ${evidence.title} · p.${evidence.pdf_page}`));
  box.append(node("div","",[evidence.source||AGENCIES[evidence.jurisdiction],evidence.product_id||(evidence.product_id===null?"공통 지침":""),meta.status&&`문서 상태 ${meta.status}`,meta.checked&&`확인 시점 ${meta.checked}`].filter(Boolean).join(" · ")));
  const links=node("div","source-links");links.append(button("원문 보기 →","link-button",()=>onOpen(id)));
  const original=sourceLink(evidence.source_url,"기관 출처 ↗");if(original)links.append(original);box.append(links);
  for(const quote of quotes)box.append(node("blockquote","quote",quote));
  const details=node("details");details.append(node("summary","","공급한 원문 문맥"),node("pre","chunk-text",evidence.text));box.append(details);
  return box;
}
function renderMemo(report,target,jobId){
  const memo=report.review_memo;if(!memo)return;
  const panel=node("details","scope-details memo-details");panel.append(node("summary","","쟁점별 검토 메모 · 미팅 질문"));
  panel.append(node("p","validation-note","자동 생성 초안입니다. 미팅 질문은 전문가 검토가 필요합니다."));
  for(const issue of memo.issues){const box=node("section","memo-issue");box.append(node("h3","",issue.request));
    for(const claim of issue.claims){box.append(node("p","",({source_fact:"원문 사실",applicant_position:"신청자 설명",regulator_position:"기관 판단",interpretation:"해석"}[claim.statement_role]||claim.statement_role)+" · "+claim.text));
      if(typeof PipelineNotes!=="undefined")box.append(button("+ 단계 메모로 반영","link-button",()=>PipelineNotes.pin({jobId,resultIndex:0,claimId:claim.claim_id,body:claim.text,products:claim.products||[]})));
      for(const doc of new Set(claim.sources.map(s=>`${s.title} · p.${s.pdf_page}`)))box.append(node("p","memo-source",doc));}
    if(issue.unresolved_requests.length)box.append(node("p","validation-note","추가 확인할 요청: "+issue.unresolved_requests.join(" / ")));
    if(issue.potential_conflicts.length)box.append(node("p","validation-note","서로 다른 투여 조건이 포착됐습니다. 시험·분석 범위를 확인해야 합니다."));panel.append(box);}
  if(memo.meeting_questions.length){panel.append(node("h3","","미팅 전 검토할 질문 초안"));for(const q of memo.meeting_questions)panel.append(node("p","",q.question_ko));}
  panel.append(button("검토 메모 저장","download",()=>downloadText(memo.markdown,"text/markdown;charset=utf-8","ra-review-memo.md")));
  if(typeof RAReviewNotes!=="undefined"&&jobId)RAReviewNotes.mount(panel,{jobId,question:report.question,api,meetingQuestions:memo.meeting_questions,memoMarkdown:memo.markdown});
  target.append(panel);
}
function renderResearch(report,card,jobId){
  const result=report.results[0],claims=result.claims||[];
  const cited=[...new Set(claims.flatMap(claim=>claim.sources.map(source=>source.evidence)))];
  const quotesFor=id=>[...new Set(claims.flatMap(claim=>claim.sources.filter(s=>s.evidence===id).map(s=>s.quote)))];
  const onOpen=opener(jobId,0,result,cited);
  card.querySelector(".answer-status").replaceWith(node("span","status answer-status"+(result.status!=="answered"?" pending":""),{answered:"근거 지지 검토",partial:"부분 답변",not_found:"근거 미확보"}[result.status]||result.status));
  card.append(answerText(result.answer_ko,result,onOpen));
  if(claims.length&&typeof PipelineNotes!=="undefined"){const actions=node("div","answer-pin-actions");actions.append(node("span","muted small","근거와 함께 단계별로 정리"),button("+ 답변을 단계 메모로 저장","download",()=>PipelineNotes.pin({jobId,resultIndex:0,kind:"answer",body:result.answer_ko,products:report.understanding.products||[]})));card.append(actions);}
  visuals(card,{jobId,resultIndex:0,result,cited,quotesFor,question:report.question,onOpen});
  const rows=result.question_requirement_coverage?.requirements||[];
  if(rows.length){const coverage=node("details","scope-details");coverage.append(node("summary","","질문 항목별 확인 상태"));
    for(const row of rows)coverage.append(node("p","",`${row.product||"공통"} · ${row.label} · ${row.has_verified_claim?"인용된 주장 있음":"추가 근거 필요"}`));
    for(const row of result.validation?.semantic_review?.coverage||[])if(row.status!=="complete")coverage.append(node("p","validation-note",row.reason));
    coverage.append(node("p","validation-note","인용된 주장 존재와 질문 전체의 충족은 별도로 검사합니다."));card.append(coverage);}
  renderMemo(report,card,jobId);
  const guidelines=new Map();for(const evidence of Object.values(result.evidence)){if(evidence.product_id===null&&evidence.document_metadata?.family==="guideline")guidelines.set(evidence.version_id,evidence);}
  if(guidelines.size){const register=node("details","scope-details");register.append(node("summary","","사용한 지침의 저장 정보"));
    for(const evidence of guidelines.values()){const meta=evidence.document_metadata,entry=node("div","citation");
      entry.append(node("strong","",evidence.title),node("p","",[meta.reference_number,meta.status&&`상태: ${meta.status}`,meta.first_published&&`최초 게시: ${meta.first_published}`,meta.legal_effective_date&&`법적 효력일: ${meta.legal_effective_date}`,meta.checked&&`확인 시점: ${meta.checked}`].filter(Boolean).join(" · ")));
      const link=sourceLink(evidence.source_url,"출처 원문 ↗");if(link)entry.append(link);register.append(entry);}
    card.append(register);}
  if(cited.length){const list=node("details","scope-details citations");list.append(node("summary","",`인용 근거 ${cited.length}개`));
    for(const id of cited)list.append(citationBox(id,result.evidence[id],quotesFor(id),onOpen));card.append(list);}
  if(result.status!=="answered"){const details=node("details","scope-details");details.append(node("summary","","검색·검토 범위 확인"));
    for(const [id,evidence] of Object.entries(result.evidence)){const row=node("p","");row.append(`[${id}] ${evidence.title} · p.${evidence.pdf_page} `,button("원문 보기","link-button",()=>onOpen(id)));details.append(row,node("pre","chunk-text",evidence.text));}
    card.append(details);}
  if(result.omitted_chunks?.length)card.append(node("p","validation-note","일부 검색 근거는 문맥 길이 제한으로 공급하지 못했습니다. 미확인 항목을 문서 전체의 미보고로 해석하지 마세요."));
  card.append(node("p","validation-note","자동 근거 검토 결과이며 전문가 확인을 대체하지 않습니다. 지침 상태는 저장된 문서의 확인 시점 기준입니다."));
}
function renderLegacy(report,card,jobId){
  card.querySelector(".answer-status").remove();
  if(!report.results.length)card.append(node("p","answer-summary",report.status==="scope_conflict"?"질문의 제품·기관과 선택한 검색 범위가 충돌해 검색을 실행하지 않았습니다. 제품·기관·문서 선택을 확인해 주세요.":report.understanding.intent==="out_of_scope"?"이 질문은 현재 문서 검색의 처리 범위를 벗어나는 것으로 해석됐습니다.":"선택 범위와 질문 조건에서 답변할 검색 단위를 찾지 못했습니다."));
  report.results.forEach((result,resultIndex)=>{
    const section=node("section","unit-answer"),head=node("div","unit-head");
    head.append(node("h3","",`${AGENCIES[result.unit.jurisdiction]||result.unit.jurisdiction||"미상"} · ${result.unit.product||"미상"}`),node("span","status"+(result.status!=="answered"?" pending":""),{answered:"인용 확인",partial:"부분 답변",not_found:"근거 미확보",error:"처리 실패"}[result.status]||result.status));
    const cited=[...new Set(result.items.flatMap(item=>[...item.evidence,...(item.conditions||[]).map(c=>c.evidence)]))].filter(id=>result.evidence[id]);
    const quotesFor=id=>[...new Set(result.items.flatMap(item=>[...(item.evidence.includes(id)?[item.quote]:[]),...(item.conditions||[]).filter(c=>c.evidence===id).map(c=>c.quote)]))];
    const onOpen=opener(jobId,resultIndex,result,cited);
    section.append(head,answerText(result.answer_ko,result,onOpen));if(result.error)section.append(node("p","error",result.error));
    visuals(section,{jobId,resultIndex,result,cited,quotesFor,question:report.question,onOpen});
    if(cited.length){const list=node("details","scope-details citations");list.append(node("summary","",`인용 근거 ${cited.length}개`));for(const id of cited)list.append(citationBox(id,result.evidence[id],quotesFor(id),onOpen));section.append(list);}
    if(result.validation?.rejected_items.length)section.append(node("p","validation-note",`원문 인용·표현 검증을 통과하지 못한 ${result.validation.rejected_items.length}개 항목을 제외했습니다.`));
    if(result.validation?.missing_conditions?.length)section.append(node("p","validation-note","일부 항목의 투여군·비교·분석·시점 조건을 원문 인용으로 확보하지 못했습니다."));
    if(result.omitted_chunks?.length)section.append(node("p","validation-note",`문맥 길이 제한으로 ${result.omitted_chunks.length}개 청크를 통째로 제외했습니다.`));
    card.append(section);
  });
  for(const product of report.scope.excluded_products||[])card.append(node("p","validation-note",`${product.product_id} / ${product.name}: ${product.reason}`));
  card.append(node("p","answer-summary","문자 인용 확인은 의미·행/열 귀속·수치 정확성의 인증을 뜻하지 않습니다."));
}
function answerCard(report,jobId){
  const card=node("div","bubble answer"),meta=node("div","answer-meta");
  meta.append(node("span","status answer-status",""),node("span","answer-cost",`${report.seconds}초 · 입력 ${report.usage.input_tokens.toLocaleString()} / 출력 ${report.usage.output_tokens.toLocaleString()}토큰`),
    button("결과 JSON 저장","download",()=>downloadText(JSON.stringify(report,null,2),"application/json","ra-answer.json")));
  card.append(meta);
  const reading=interpretationBox(report);if(reading)card.append(reading);
  for(const warning of report.understanding.scope_warnings||[])card.append(node("p","validation-note",warning));
  if(report.mode==="ra")renderResearch(report,card,jobId);else renderLegacy(report,card,jobId);
  const scope=node("details","scope-details");scope.append(node("summary","","질문 해석·검색 범위 확인"));
  const u=report.understanding;scope.append(node("pre","",JSON.stringify({intent:u.intent,products:u.products,studies:u.studies,jurisdictions:u.jurisdictions,english_query:u.english_query,scope_warnings:u.scope_warnings,
    search_plan:(report.plan||[]).map(unit=>({product:unit.product,jurisdiction:unit.jurisdiction,studies:unit.studies,document_groups:unit.doc_groups}))},null,2)));
  card.append(scope);return card;
}
async function poll(identity,turn){
  busy(true);state.jobId=identity;const wait=pending(turn);
  try{while(true){const job=await api("/api/jobs/"+encodeURIComponent(identity));wait.update(job);
    if(job.state==="completed"){wait.box.replaceWith(answerCard(job.report,identity));setScopeChip(turn,scopeFromReport(job.report));break;}
    if(job.state==="failed")throw Error(job.error||job.message);
    await new Promise(resolve=>setTimeout(resolve,1000));}}
  catch(error){wait.box.replaceWith(node("div","bubble answer error",error.message));}
  finally{busy(false);}
}
function clearTurns(){Viewer.close();clearError();chatLog().querySelectorAll(".turn").forEach(t=>t.remove());}
async function openJob(identity){
  if(state.busy)return;clearTurns();
  try{const job=await api("/api/jobs/"+encodeURIComponent(identity));
    if(job.conversation_id)return openConversation(job.conversation_id);
    state.conversationId=null;state.turns=0;conversationState();
    history.replaceState(null,"",`#job=${identity}`);
    const turn=startTurn(job.question||"",job.report?scopeFromReport(job.report):"");await poll(identity,turn);}
  catch(error){showError(error.message);}
}
async function openConversation(identity){
  if(state.busy)return;clearTurns();
  try{const conversation=await api("/api/conversations/"+encodeURIComponent(identity));
    state.conversationId=identity;state.turns=conversation.turns.length;history.replaceState(null,"",`#conv=${identity}`);
    for(const item of conversation.turns){
      let job=null;try{job=await api("/api/jobs/"+encodeURIComponent(item.job_id));}catch{}
      const turn=startTurn(item.user_text,job?.report?scopeFromReport(job.report):"");
      if(!job)turn.append(node("div","bubble answer error","저장된 답변을 찾지 못했습니다."));
      else if(job.state==="completed")turn.append(answerCard(job.report,item.job_id));
      else if(job.state==="failed")turn.append(node("div","bubble answer error",job.error||job.message));
      else await poll(item.job_id,turn);
    }
    conversationState();
  }catch(error){showError(error.message);}
}
function newChat(){if(state.busy)return;clearTurns();$("empty").hidden=false;state.jobId=null;state.conversationId=null;state.turns=0;conversationState();history.replaceState(null,"",location.pathname);$("question").focus();}
async function submitQuestion(event){
  event.preventDefault();if(state.busy)return;const question=$("question").value.trim();if(!question)return;clearError();
  let scope;try{scope=Inventory.payload();}catch(error){showError(error.message);return;}
  const turn=startTurn(question,Inventory.describe().text);$("question").value="";busy(true);
  const body={question,...scope,include_nondefault:$("include-nondefault").checked};if(state.conversationId)body.conversation_id=state.conversationId;
  try{const job=await api("/api/questions",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    state.conversationId=job.conversation_id||null;state.turns+=1;history.replaceState(null,"",`#conv=${job.conversation_id}`);
    await poll(job.id,turn);conversationState();}
  catch(error){turn.append(node("div","bubble answer error",error.message));$("question").value=question;busy(false);}
}
let historyOffset=0;
async function loadQuestionHistory(append=false){
  const target=$("history-results");if(!append){historyOffset=0;target.replaceChildren();}
  try{const records=await api(`/api/jobs?limit=20&offset=${historyOffset}&q=${encodeURIComponent($("history-query").value.trim())}`);
    for(const job of records.jobs){const item=button(`${new Date(job.created_at*1000).toLocaleString("ko-KR")} · ${{answered:"답변",partial:"부분 답변",not_found:"근거 미확보"}[job.status]||STAGES[job.state]||job.state}\n${job.question}`,"history-item",()=>{$("history-panel").open=false;openJob(job.id);});target.append(item);}
    historyOffset+=records.jobs.length;$("history-more").hidden=historyOffset>=records.total;
    if(!records.total)target.append(node("p","","저장된 질문이 없습니다."));}
  catch(error){target.append(node("p","validation-note","질문 목록을 읽지 못했습니다: "+error.message));}
}
function splitter(id,side){
  const handle=$(id),layout=$("layout");
  handle.onpointerdown=event=>{if(side==="left"&&layout.classList.contains("scope-collapsed"))return;event.preventDefault();handle.setPointerCapture(event.pointerId);handle.classList.add("dragging");
    const move=e=>{const rect=layout.getBoundingClientRect();
      if(side==="left"){const w=Math.min(Math.max(e.clientX-rect.left,220),520);layout.style.setProperty("--left-w",w+"px");}
      else{const w=Math.min(Math.max(rect.right-e.clientX,320),rect.width-640);layout.style.setProperty("--right-w",w+"px");}};
    const up=()=>{handle.classList.remove("dragging");handle.removeEventListener("pointermove",move);handle.removeEventListener("pointerup",up);
      Prefs.set("widths",{left:layout.style.getPropertyValue("--left-w"),right:layout.style.getPropertyValue("--right-w")});};
    handle.addEventListener("pointermove",move);handle.addEventListener("pointerup",up);};
}
function layoutInit(){
  const layout=$("layout"),widths=Prefs.get("widths",{});
  if(/^\d+px$/.test(widths.left||""))layout.style.setProperty("--left-w",widths.left);if(/^\d+px$/.test(widths.right||""))layout.style.setProperty("--right-w",widths.right);
  splitter("split-left","left");splitter("split-right","right");
  const collapse=value=>{layout.classList.toggle("scope-collapsed",value);$("scope-toggle").textContent=value?"›":"‹";$("scope-toggle").setAttribute("aria-label",value?"자료 패널 펼치기":"자료 패널 접기");$("scope-toggle").setAttribute("aria-expanded",String(!value));Prefs.set("scopeCollapsed",value);};
  $("scope-toggle").onclick=()=>collapse(!layout.classList.contains("scope-collapsed"));collapse(Prefs.get("scopeCollapsed",false));
}
async function init(){
  layoutInit();Viewer.init();
  if(typeof DataManager!=="undefined")DataManager.init();
  $("question-form").onsubmit=submitQuestion;$("new-chat").onclick=newChat;
  $("question").onkeydown=event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();$("question-form").requestSubmit();}};
  $("include-nondefault").onchange=()=>Inventory.refreshCounts();
  $("history-search").onclick=()=>loadQuestionHistory();$("history-more").onclick=()=>loadQuestionHistory(true);
  $("history-query").onkeydown=event=>{if(event.key==="Enter"){event.preventDefault();loadQuestionHistory();}};
  $("history-panel").ontoggle=()=>{if($("history-panel").open&&!$("history-results").children.length)loadQuestionHistory();};
  $("question").dataset.first=$("question").placeholder;
  window.addEventListener("hashchange",()=>{const params=new URLSearchParams(location.hash.slice(1)),conv=params.get("conv"),id=params.get("job");
    if(state.busy)return;if(conv&&conv!==state.conversationId)openConversation(conv);else if(!conv&&id&&id!==state.jobId)openJob(id);});
  try{const [summary,inventory]=await Promise.all([api("/api/summary"),api("/api/inventory")]);state.summary=summary;
    Inventory.render($("inventory"),inventory,()=>{});
    if(typeof PipelineNotes!=="undefined")await PipelineNotes.init(inventory);
    $("index-info").textContent=`${summary.chunks.toLocaleString()}청크 · 검색 허용 ${summary.eligible_chunks.toLocaleString()}\nBGE-M3 · ${summary.answer_model}\n${summary.run_name}${summary.pipeline_version?"\n"+summary.pipeline_version:""}`;
    const params=new URLSearchParams(location.hash.slice(1));if(params.get("conv"))await openConversation(params.get("conv"));else if(params.get("job"))await openJob(params.get("job"));}
  catch(error){showError(error.message);}
}
init();
