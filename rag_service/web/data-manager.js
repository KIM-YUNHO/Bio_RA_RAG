"use strict";
const DataManager=(()=>{
  const labels={queued:"수집 준비",staging:"스테이징 수집 중",awaiting_approval:"확인표 검토 대기",approved:"등록 준비",promoting:"자료 등록 중",done:"자료 등록 완료",failed:"실패",cancelled:"취소"};
  const eventLabels={planned:"수집 계획 확정",staging_started:"수집 시작",staged:"수집 결과 확인표",promoted:"자료 등록",database_updated:"DB 갱신",failed:"작업 실패"};
  let dialog=null,sidebar=null,main=null,notice=null,connection=null,session=null,view=null,busy=false,timer=null,generation=0,overviewToken=0,overviewData=null,signature="",query="",deleteBox=null,pendingDelete=null;
  const drafts=new Map();
  const post=(path,value)=>api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(value)});
  const alive=screen=>dialog===screen&&screen?.open;
  const note=(message,error=false)=>{if(notice){notice.textContent=message;notice.classList.toggle("is-error",error);}};
  function working(value){busy=value;if(dialog)dialog.querySelectorAll("button,input,textarea").forEach(e=>{if(!e.classList.contains("data-manager-close"))e.disabled=value||e.dataset.unavailable==="true";});}
  function sessionName(s){return s.target?.product_id||Object.values(s.target?.names||{})[0]||s.target?.ingredient||"새 적재 대화";}
  const canDelete=s=>s.can_delete??(!s.request_status&&!s.request||["done","failed","cancelled"].includes(s.request_status||s.request?.status));
  function deleteButton(s,header=false){const b=button(header?"대화 삭제":"삭제",header?"download collection-delete-action":"collection-session-delete",()=>askDelete(s));b.dataset.sessionId=s.id;b.dataset.unavailable=String(!canDelete(s));b.disabled=!canDelete(s);b.setAttribute("aria-label",sessionName({target:s.target||s.state?.target})+" 적재 대화 삭제");b.title=canDelete(s)?"이 적재 대화 삭제":"수집·등록 또는 확인표 검토가 끝난 뒤 삭제할 수 있습니다.";return b;}
  function askDelete(s){
    if(busy||!canDelete(s))return;pendingDelete={id:s.id,name:sessionName({target:s.target||s.state?.target})};deleteBox.replaceChildren();deleteBox.hidden=false;
    const text=node("div"),error=node("p","collection-delete-error");error.setAttribute("role","alert");text.append(node("strong","",pendingDelete.name+" 대화를 삭제할까요?"),node("p","","대화 목록에서 삭제합니다. 수집 요청과 등록한 자료는 유지됩니다."),error);
    const actions=node("div","collection-delete-actions"),cancel=button("취소","download",()=>{pendingDelete=null;deleteBox.hidden=true;deleteBox.replaceChildren();});actions.append(cancel,button("삭제","primary-button collection-delete-confirm-button",removeSession));deleteBox.append(text,actions);cancel.focus();
  }
  function forgetSession(id){drafts.delete(id);if(Prefs.get("ingestionSession","")===id)Prefs.set("ingestionSession","");const saved=Prefs.get("ingestionView",null);if(saved?.kind==="session"&&saved.id===id)Prefs.set("ingestionView",null);}
  async function removeSession(){
    if(busy||!pendingDelete)return;const screen=dialog,id=pendingDelete.id;deleteBox.querySelector(".collection-delete-error").textContent="";working(true);note("대화를 삭제하고 있습니다.");
    try{await post(`/api/ingestion/sessions/${id}/delete`,{});forgetSession(id);if(!alive(screen))return;pendingDelete=null;deleteBox.hidden=true;deleteBox.replaceChildren();
      if(session?.id===id)session=null;if(view?.kind==="session"&&view.id===id){view=null;signature="";main.replaceChildren(node("div","collection-welcome","대화를 삭제했습니다. 다른 대화를 선택하거나 새 수집 요청을 시작하세요."));}
      const current=view;await overview();if(alive(screen)&&view===current&&current?.kind==="request"){const data=await api(`/api/ingestion/requests/${current.id}`);if(alive(screen)&&view===current){signature=JSON.stringify(data);renderRequest(data,true);}}
      if(alive(screen))note("적재 대화를 삭제했습니다.");
    }catch(e){if(alive(screen)){note(e.message,true);const error=deleteBox.querySelector(".collection-delete-error");if(error)error.textContent="삭제하지 못했습니다. "+e.message;}}finally{if(alive(screen))working(false);}
  }
  const productName=p=>p.ema_name||p.us_brand||p.ca_brand||p.product_id||"수집 요청";
  const requestName=r=>(r.products||r.payload?.products||[]).map(productName).join(" · ")||"수집 요청";
  const date=value=>new Date(value).toLocaleString("ko-KR",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"});
  function mark(item,kind,id){item.setAttribute("aria-current",String(view?.kind===kind&&view.id===id));return item;}
  function paintSidebar(data){
    const sessionsOpen=sidebar.querySelector(".collection-sessions")?.open,releasesOpen=sidebar.querySelector(".collection-releases")?.open,scrollTop=sidebar.scrollTop;
    sidebar.replaceChildren();const caption=node("div","collection-list-caption");const rows=data.requests.filter(r=>[requestName(r),JSON.stringify(r.products||[])].join(" ").toLocaleLowerCase().includes(query));caption.append(node("span","","수집 요청"),node("span","",String(rows.length)));sidebar.append(caption);
    if(!rows.length)sidebar.append(node("p","collection-sidebar-empty",query?"검색 결과가 없습니다.":"아직 수집 요청이 없습니다."));
    for(const r of rows){const item=mark(button("","data-manager-item collection-request",()=>openRequest(r.id)),"request",r.id),identity=node("span","collection-request-identity");identity.append(node("strong","",requestName(r)),node("small","",(r.products||[]).map(p=>p.product_id).filter(Boolean).join(" · ")||date(r.created_at)));item.append(identity,node("span","collection-badge "+r.status,labels[r.status]||r.status));sidebar.append(item);}
    const sessions=node("details","collection-sessions");sessions.open=sessionsOpen??(view?.kind==="session");sessions.append(node("summary","",`적재 대화 ${data.sessions.length}개`));
    if(!data.sessions.length)sessions.append(node("p","muted small","아직 적재 대화가 없습니다."));
    for(const s of data.sessions.filter(s=>[sessionName(s),s.target?.ingredient].join(" ").toLocaleLowerCase().includes(query))){const row=node("div","collection-session-row");row.dataset.sessionId=s.id;row.append(mark(button(`${sessionName(s)} · ${new Date(s.updated_at).toLocaleDateString("ko-KR")}`,"data-manager-item",()=>openSession(s.id)),"session",s.id),deleteButton(s));sessions.append(row);}sidebar.append(sessions);
    const releases=node("details","scope-details collection-releases");releases.open=releasesOpen??false;releases.append(node("summary","",`DB 릴리스 ${data.releases.length}개`));
    for(const r of data.releases)releases.append(node("p","small",`${r.id}\n${r.documents}문서 · ${r.chunks.toLocaleString()}청크`));sidebar.append(releases);
    sidebar.append(node("p","validation-note",`현재 검색 자료 · ${(data.service.product_index_chunks||0).toLocaleString()}청크`));sidebar.scrollTop=scrollTop;working(busy);
  }
  async function overview(){
    const screen=dialog,mine=++overviewToken;if(!alive(screen))return null;
    try{const data=await api("/api/ingestion");if(!alive(screen)||mine!==overviewToken)return null;overviewData=data;connection.textContent="적재 서비스 연결됨 · 5초마다 상태 갱신";connection.classList.remove("is-error");paintSidebar(data);return data;}
    catch(e){if(alive(screen)&&mine===overviewToken){connection.textContent="적재 서비스 연결 오류";connection.classList.add("is-error");note(e.message,true);}return null;}
  }
  async function create(){
    if(busy)return;const screen=dialog;working(true);note("새 적재 대화를 만들고 있습니다.");
    try{const data=await post("/api/ingestion/sessions",{});if(!alive(screen))return;session=data;view={kind:"session",id:data.id};signature=JSON.stringify(data);Prefs.set("ingestionSession",data.id);Prefs.set("ingestionView",view);renderSession();await overview();if(alive(screen))note("");}
    catch(e){if(alive(screen))note(e.message,true);}finally{if(alive(screen))working(false);}
  }
  async function openSession(id){
    if(busy)return;const screen=dialog,target={kind:"session",id};view=target;working(true);note("");
    try{const data=await api("/api/ingestion/sessions/"+encodeURIComponent(id));if(!alive(screen)||view!==target)return;session=data;signature=JSON.stringify(data);Prefs.set("ingestionSession",id);Prefs.set("ingestionView",view);renderSession();if(overviewData)paintSidebar(overviewData);return true;}
    catch(e){if(alive(screen))note(e.message,true);}finally{if(alive(screen))working(false);}
  }
  function renderSession(){
    if(!dialog?.open||!session)return;main.replaceChildren();const head=node("div","data-manager-section-head");
    const actions=node("div","collection-session-actions");actions.append(button("진행·확인표 보기","download",()=>turn("/status")),deleteButton(session,true));head.append(node("h3","",sessionName({target:session.state.target})+" · 자료 적재"),actions);main.append(head);
    if(session.request){main.append(progress(session.request.status));const status=node("div","ingestion-progress");status.append(node("strong","",labels[session.request.status]||session.request.status),button("이벤트·로그 보기","link-button",()=>openRequest(session.request.id)));main.append(status);
      if(session.request.status==="awaiting_approval")main.append(node("p","validation-note","수집이 끝났습니다. ‘진행·확인표 보기’에서 결과를 확인한 뒤 자료 등록을 승인하세요."));
      if(session.request.status==="done")main.append(node("p","validation-note","자료 등록이 완료됐습니다. 검색에 사용하려면 전처리·임베딩·릴리스 반영이 필요합니다."));}
    const log=node("div","ingestion-chat");
    if(!session.turns.length)log.append(node("p","muted small","성분·제품명으로 수집할 자료를 요청하세요. 예: ‘Imuldosa 자료 적재해줘’. 자연어 요청 해석에 모델이 사용됩니다."));
    for(const t of session.turns)log.append(node("p","ingestion-user",t.user_text),node("pre","ingestion-reply",t.reply));main.append(log);
    const state=session.state;
    if(state.stage==="choosing"&&state.candidates?.length){const candidates=node("div","data-manager-candidates");
      state.candidates.forEach((c,i)=>candidates.append(button(`${i+1}. ${c.product_id||c.key} · ${Object.values(c.names||{}).join(" / ")}`,"download",()=>control({action:"select",candidate_key:c.key}))));main.append(candidates);}
    if(state.stage==="planned"&&session.plan_sha256){const plan=node("div","ingestion-plan"),request=state.plan.request;
      plan.append(node("strong","","확정할 수집 계획"),node("p","small",`${request.ingredient} · ${(request.products||[]).map(p=>p.product_id).join(" · ")} · ${(request.jurisdictions||[]).map(j=>AGENCIES[j]||j).join(" / ")}`),button("계획대로 스테이징 수집 시작","primary-button",()=>control({action:"start",plan_sha256:session.plan_sha256})));main.append(plan);}
    if(state.stage==="review"&&session.request?.status==="awaiting_approval")for(const [product,digest] of Object.entries(state.shown_reports||{})){
      if(session.request.reports[product]!==digest)continue;const panel=node("div","ingestion-approval");
      panel.append(node("p","small",`${product} · 위 확인표를 검토한 뒤 자료 저장소에 등록합니다.`),button(`${product} 확인표 승인·자료 등록`,"primary-button",()=>approve(product,digest)));main.append(panel);}
    const form=node("form","ingestion-composer"),input=node("textarea");input.rows=2;input.maxLength=2000;input.required=true;input.setAttribute("aria-label","자료 적재 요청");input.placeholder="수집할 제품이나 성분을 입력하세요";input.value=drafts.get(session.id)||"";
    const id=session.id;input.oninput=()=>drafts.set(id,input.value);const submit=button("요청 보내기","primary-button");submit.type="submit";form.append(input,submit);form.onsubmit=e=>{e.preventDefault();const text=input.value.trim();if(text){drafts.set(id,input.value);turn(text);}};main.append(form);
    main.append(node("p","validation-note","비교·참고 자료를 관리하는 창입니다. 수집·등록 이후 검색 반영은 별도이며, 창을 닫아도 시작한 수집은 계속됩니다."));log.scrollTop=log.scrollHeight;working(busy);
  }
  async function perform(suffix,payload,message,clearDraft=false){
    if(busy||!session)return;const screen=dialog,id=session.id;working(true);note(message);
    try{const data=await post(`/api/ingestion/sessions/${id}/${suffix}`,payload);if(!alive(screen))return;if(clearDraft)drafts.delete(id);session=data;signature=JSON.stringify(data);renderSession();await overview();if(alive(screen))note("");}
    catch(e){if(alive(screen))note(e.message,true);}finally{if(alive(screen))working(false);}
  }
  const turn=text=>perform("turns",{text},text==="/status"?"작업 상태를 확인하고 있습니다.":"요청을 처리하고 있습니다.",text!=="/status");
  const control=payload=>perform("actions",payload,payload.action==="start"?"수집 작업을 시작하고 있습니다.":"선택한 제품을 확인하고 있습니다.");
  const approve=(product,digest)=>perform("approve",{product_id:product,report_sha256:digest},"확인표를 확인하고 자료 등록을 시작하고 있습니다.");
  function progress(status){
    const active={queued:1,staging:1,awaiting_approval:2,approved:3,promoting:3,done:4}[status];
    const rail=node("ol","collection-steps");rail.setAttribute("aria-label","자료 수집 진행 단계");
    ["요청 확인","자료 수집","확인표 검토","저장소 등록"].forEach((name,i)=>{const state=active===undefined?"":i<active?"complete":i===active?"current":"",step=node("li","collection-step "+state);if(i===active)step.setAttribute("aria-current","step");step.append(node("span","collection-step-dot",i<active?"✓":String(i+1)),node("span","",name));rail.append(step);});return rail;
  }
  function reportData(request){
    const latest=new Map();for(const e of request.events||[])if(e.event==="staged"&&e.payload.report)latest.set(e.payload.product_id||"",e.payload.report);
    const sources=new Map(),documents=[],trials=new Set();
    for(const [product,report] of latest){let source=null;for(const line of report.split(/\r?\n/)){
      const heading=line.match(/^-- (.+): (\d+) documents, (\d+) pages\/sections\s*$/);
      if(heading){source=heading[1];const row=sources.get(source)||{name:source,documents:0,pages:0};row.documents+=Number(heading[2]);row.pages+=Number(heading[3]);sources.set(source,row);continue;}
      const doc=line.match(/^\s+\[([^\]]+)\]\s+(.+?)\s+\((\d+), (https?:\/\/.+)\)\s*$/);
      if(doc)documents.push({source:source||"기타",product,title:doc[2],pages:Number(doc[3]),url:doc[4],tier:doc[1].trim()});
      const trial=line.match(/^-- trial (NCT\d+)\b/);if(trial)trials.add(trial[1]);
    }}
    return {sources:[...sources.values()],documents,reportCount:latest.size,documentCount:sources.size?[...sources.values()].reduce((sum,s)=>sum+s.documents,0):null,agencyCount:sources.size?[...sources.values()].filter(s=>s.documents>0&&["EMA","FDA","Health Canada","PMDA","MFDS"].includes(s.name)).length:null,trialCount:sources.size?trials.size:null};
  }
  function resultSummary(result){
    const stats=node("div","collection-stats");for(const [label,value,unit] of [["문서",result.documentCount,"개"],["규제 기관",result.agencyCount,"곳"],["임상시험",result.trialCount,"건"]]){const cell=node("div","collection-stat");cell.append(node("span","",label),node("strong","",value===null?"—":String(value)),node("span","",unit));stats.append(cell);}return stats;
  }
  function resultTable(result){
    const wrap=node("div","collection-table-wrap"),table=node("table","collection-source-table"),head=node("thead"),row=node("tr");for(const title of ["기관·출처","주요 자료","문서","상태"]){const th=node("th","",title);th.scope="col";row.append(th);}head.append(row);table.append(head);const body=node("tbody"),descriptions={EMA:"평가 보고서 · 제품 정보",FDA:"심사 문서 · 허가 자료","Health Canada":"제품 설명서","ClinicalTrials.gov":"시험계획서 · 통계분석계획서",PMDA:"심사 보고서"};
    for(const source of result.sources){const tr=node("tr");tr.append(node("td","",source.name),node("td","",descriptions[source.name]||"수집 문서"),node("td","",String(source.documents)),node("td","collection-table-status",source.documents?"✓ 수집 확인":"문서 없음"));body.append(tr);}table.append(body);wrap.append(table);return wrap;
  }
  function documentList(result,previous="전체"){
    const details=node("details","collection-documents");details.append(node("summary","",`전체 문서 확인 · ${result.documents.length}개`));const filters=node("div","collection-document-filters"),list=node("div","collection-document-list");let selected=result.documents.some(d=>d.source===previous)?previous:"전체";
    const paint=()=>{list.replaceChildren();for(const doc of result.documents.filter(d=>selected==="전체"||d.source===selected)){const row=node("article","collection-document"),identity=node("div");const link=sourceLink(doc.url,doc.title);identity.append(link||node("span","",doc.title),node("small","",`${doc.product} · ${doc.pages}쪽 · ${doc.tier}`));row.append(node("span","collection-document-source",doc.source),identity);list.append(row);}filters.querySelectorAll("button").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.source===selected)));};
    for(const name of ["전체",...new Set(result.documents.map(d=>d.source))]){const b=button(name,"download",()=>{selected=name;paint();});b.dataset.source=name;filters.append(b);}details.append(filters,list);paint();return details;
  }
  async function reviewRequest(id,ownerId){const screen=dialog,opened=await openSession(ownerId);if(opened&&alive(screen)&&session?.id===ownerId&&session.request?.id===id&&view?.kind==="session")await turn("/status");}
  function renderRequest(request,preserve=false){
    const oldScroll=preserve?main.scrollTop:0,filter=preserve?main.querySelector(".collection-document-filters [aria-pressed=true]")?.dataset.source:undefined;
    const openDetails=new Set(preserve?[...main.querySelectorAll("details[open]")].map(d=>d.className):[]);main.replaceChildren();
    const payload=request.payload||{},subject=node("div","collection-subject"),identity=node("div"),title=node("div","collection-subject-title");title.append(node("h2","",requestName(request)));
    for(const product of payload.products||[])if(product.product_id)title.append(node("span","collection-product-code",product.product_id));identity.append(title,node("p","collection-subject-description",[payload.ingredient,(payload.jurisdictions||[]).map(j=>AGENCIES[j]||j).join(" / ")].filter(Boolean).join(" · ")));subject.append(identity,button("새로고침","download",sync));main.append(subject,progress(request.status));
    const head=node("div","data-manager-section-head");head.append(node("h3","",labels[request.status]||request.status));main.append(head,node("p","collection-review-description",request.status==="awaiting_approval"?"문서와 제품 정보를 확인한 뒤 자료 저장소에 등록합니다.":request.status==="done"?"자료 등록이 완료됐습니다. 검색 반영 상태는 별도로 확인하세요.":request.status==="failed"?"요청 기록에서 실패한 단계와 상세 내용을 확인하세요.":"작업 상태와 수집 결과를 자동으로 갱신합니다."));
    const result=reportData(request);main.append(resultSummary(result));
    if(result.sources.length)main.append(resultTable(result));else main.append(node("p","collection-empty-result",result.reportCount?"원본 확인표에서 세부 내용을 확인하세요.":"수집 결과 확인표가 생성되면 기관별 문서가 표시됩니다."));
    if(result.documents.length)main.append(documentList(result,filter));
    const owner=overviewData?.sessions.find(s=>s.request_id===request.id),records=node("details","collection-records");records.append(node("summary","","요청 기록·원본 확인표"));
    for(const e of request.events||[]){const row=node("section","ingestion-event");row.append(node("strong","",eventLabels[e.event]||e.event),node("span","muted small",new Date(e.at).toLocaleString("ko-KR")));row.append(node("pre","ingestion-reply",e.payload.report||JSON.stringify(e.payload,null,2)));records.append(row);}main.append(records);
    const logs=node("details","scope-details ingestion-logs");logs.append(node("summary","","작업 로그"));let loaded=false;
    logs.ontoggle=async()=>{if(!logs.open||loaded)return;const screen=dialog;try{const data=await api(`/api/ingestion/requests/${request.id}/logs`);if(!alive(screen)||!logs.isConnected)return;loaded=true;logs.append(node("p","muted small",data.note));for(const l of data.logs)logs.append(node("strong","",l.name),node("pre","ingestion-reply",l.text));if(!data.logs.length)logs.append(node("p","muted small","기록된 로그가 없습니다."));}catch(e){if(logs.isConnected)logs.append(node("p","workspace-form-error",e.message));}};
    main.append(logs);for(const details of main.querySelectorAll("details"))if(openDetails.has(details.className))details.open=true;
    const footer=node("footer","collection-footer"),actions=node("div","collection-footer-actions");footer.append(node("p","collection-info",request.search_note||"자료 등록 완료와 전처리·임베딩·검색 반영은 별도입니다."));actions.append(node("span","collection-updated",request.created_at?"요청 · "+date(request.created_at):""));
    if(owner)actions.append(button(request.status==="awaiting_approval"?"확인표 검토·자료 등록":"이 요청의 적재 대화·확인표 열기",request.status==="awaiting_approval"?"primary-button":"download",()=>request.status==="awaiting_approval"?reviewRequest(request.id,owner.id):openSession(owner.id)));else actions.append(node("span","validation-note","연결된 적재 대화가 없습니다."));footer.append(actions);main.append(footer);main.scrollTop=oldScroll;working(busy);
  }
  async function openRequest(id){
    if(busy)return;const screen=dialog,target={kind:"request",id};view=target;working(true);note("");
    try{const data=await api(`/api/ingestion/requests/${id}`);if(!alive(screen)||view!==target)return;signature=JSON.stringify(data);Prefs.set("ingestionView",view);renderRequest(data);if(overviewData)paintSidebar(overviewData);}
    catch(e){if(alive(screen))note(e.message,true);}finally{if(alive(screen))working(false);}
  }
  async function sync(){
    const screen=dialog,target=view,previousSignature=signature;if(!alive(screen)||busy)return;await overview();if(!alive(screen)||view!==target||!target||busy)return;
    try{const data=await api(`/api/ingestion/${target.kind==="session"?"sessions":"requests"}/${target.id}`);if(!alive(screen)||view!==target||busy||signature!==previousSignature)return;const next=JSON.stringify(data);if(next===signature)return;signature=next;if(target.kind==="session"){session=data;renderSession();}else renderRequest(data,true);}
    catch(e){if(alive(screen))note(e.message,true);}
  }
  async function open(){
    if(dialog)return;const epoch=++generation;session=null;view=null;overviewData=null;signature="";busy=false;pendingDelete=null;
    dialog=node("dialog","workspace-dialog data-manager-dialog");dialog.setAttribute("aria-labelledby","collection-title");const screen=dialog,head=node("div","workspace-dialog-head"),title=node("div","collection-brand"),titleText=node("div"),heading=node("h2","","자료 수집·적재");heading.id="collection-title";titleText.append(heading,node("p","","비교·참고 제품의 규제 자료를 관리합니다"));title.append(node("span","collection-brand-bar"),titleText);connection=node("p","ingestion-connection","적재 서비스 연결 확인 중");connection.setAttribute("role","status");
    const close=button("×","icon-button data-manager-close",()=>screen.close());close.setAttribute("aria-label","자료 관리 닫기");const topActions=node("div","collection-top-actions");topActions.append(connection,button("+ 새 수집 요청","primary-button",create),close);head.append(title,topActions);
    const body=node("div","data-manager-body"),aside=node("aside","data-manager-sidebar"),searchLabel=node("label","collection-search"),search=node("input");search.type="search";search.placeholder="제품명으로 찾기";search.setAttribute("aria-label","수집 요청 검색");search.value=query;search.oninput=()=>{query=search.value.trim().toLocaleLowerCase();if(overviewData)paintSidebar(overviewData);};searchLabel.append(search);sidebar=node("div","collection-sidebar-list");aside.append(searchLabel,sidebar);main=node("section","data-manager-main");main.setAttribute("aria-label","자료 수집 요청 상세");main.append(node("div","collection-welcome","수집 요청을 선택하거나 ‘새 수집 요청’으로 제품·성분을 입력하세요."));body.append(aside,main);deleteBox=node("section","collection-delete-confirm");deleteBox.hidden=true;deleteBox.setAttribute("aria-label","적재 대화 삭제 확인");notice=node("div","pipeline-message");notice.setAttribute("role","status");dialog.append(head,deleteBox,body,notice);document.body.append(dialog);
    screen.addEventListener("close",()=>{clearTimeout(timer);screen.remove();if(dialog===screen){dialog=null;session=null;view=null;busy=false;generation++;}},{once:true});screen.showModal();
    const data=await overview();if(!alive(screen)||generation!==epoch)return;
    if(data){const saved=Prefs.get("ingestionView",null),previous=Prefs.get("ingestionSession","");if(saved?.kind==="request"&&data.requests.some(r=>r.id===saved.id))await openRequest(saved.id);else if(saved?.kind==="session"&&data.sessions.some(s=>s.id===saved.id))await openSession(saved.id);else{const pending=data.requests.find(r=>!['done','failed','cancelled'].includes(r.status));if(pending||data.requests[0])await openRequest((pending||data.requests[0]).id);else if(data.sessions.some(s=>s.id===previous))await openSession(previous);}}
    const refresh=async()=>{if(!alive(screen)||generation!==epoch)return;try{await sync();}finally{if(alive(screen)&&generation===epoch)timer=setTimeout(refresh,5000);}};if(alive(screen)&&generation===epoch)timer=setTimeout(refresh,5000);
  }
  function init(){$("data-manager-open").onclick=open;}
  return Object.freeze({init});
})();
