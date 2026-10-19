"use strict";
// Development stages organize notes; they never change the RAG search filters.
const PipelineNotes=(()=>{
  let inventory=null,projects=[],project=null,stage="bridging",all=false,tag="",archived=false,loadToken=0,editor=null;
  let itemType="",definitions={types:{unclassified:"미분류",fact:"사실·결과",criterion:"적용 기준",interpretation:"의견·해석",decision:"결정",action:"후속 조치",question:"미해결 질문"},modalities:{unclassified:"미분류",reported:"원문 보고",observed:"관측",predicted:"예상",planned:"계획",proposal:"제안",not_disclosed:"미공개"},data_maturity:{not_disclosed:"미공개",preliminary:"잠정",final:"확정",not_applicable:"해당 없음"},run_validity:{not_disclosed:"미공개",pending:"확인 대기",valid:"유효",invalid:"무효",not_applicable:"해당 없음"},tasks:{result_review:"결과 검토",partner_meeting:"협력사 미팅 준비",ra_preparation:"기관 질문 준비"}};
  const roleNames={source_fact:"원문 사실",applicant_position:"신청자 설명",regulator_position:"기관 판단",interpretation:"AI 해석",user_note:"직접 의견"};
  const originLabel=memo=>memo.source?.report_origin==="public_source_replay"?"공개자료 재현":memo.origin==="rag"?"RAG 근거":"직접 메모";
  const stages=()=>project?.stages||[];
  const msg=(text,error=false)=>{const box=$("pipeline-message");box.textContent=text;box.classList.toggle("is-error",error);};
  const send=(path,body)=>api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const uuid=()=>crypto.randomUUID().replaceAll("-","");
  const questionCache=new Map();
  function memoQuestion(memo){
    const saved=memo.source?.question;if(typeof saved==="string"&&saved.trim())return Promise.resolve(saved);
    const id=memo.source?.job_id;if(!id)return Promise.resolve("");
    if(!questionCache.has(id))questionCache.set(id,api("/api/jobs/"+encodeURIComponent(id)).then(job=>job.question||job.report?.question||"").catch(()=>""));
    return questionCache.get(id);
  }
  function questionTitle(target,memo){
    const direct=(memo.body.split(/\r?\n/).find(line=>line.trim())||"직접 메모").slice(0,160);
    target.textContent=memo.source?.question||(memo.source?"원 질문을 불러오는 중입니다.":direct);
    if(memo.source)memoQuestion(memo).then(question=>{if(target.isConnected){target.textContent=question||"저장된 RAG 메모";target.title=target.textContent;}});
    else target.title=direct;
  }
  const date=value=>new Date(value).toLocaleString("ko-KR",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"});
  const memoList=id=>(project?.memos||[]).filter(m=>m.stage_ids.includes(id)&&Boolean(m.archived)===archived&&(!tag||m.tags.some(t=>t.toLocaleLowerCase()===tag.toLocaleLowerCase()))&&(!itemType||(m.items||[]).some(i=>i.type===itemType)));
  function emptyMemo(s){
    const message=itemType?"이 유형의 항목을 가진 메모가 없습니다.":tag?"이 태그의 메모가 없습니다.":archived?"보관된 메모가 없습니다.":"이 단계에 메모를 남겨보세요.",canAdd=!tag&&!archived&&!itemType;
    const box=canAdd?button("","pipeline-empty is-action",()=>{stage=s.id;Prefs.set("workspaceStage:"+project.id,stage);all=false;render();openEditor();}):node("div","pipeline-empty");
    const text=node("span","pipeline-empty-text");if(canAdd)text.append(node("span","pipeline-empty-phrase","이 단계에 메모를"),document.createTextNode(" "),node("span","pipeline-empty-phrase","남겨보세요."));else text.textContent=message;
    box.append(text);if(canAdd){box.append(node("span","pipeline-empty-icon","+"));box.setAttribute("aria-label",s.label+" 메모 추가");}return box;
  }
  function setMode(mode){if(!["split","pipeline","chat"].includes(mode))mode="split";$("chat-pane").dataset.workspaceMode=mode;document.querySelectorAll("[data-workspace-mode]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.workspaceMode===mode)));Prefs.set("workspaceMode",mode);if(mode==="pipeline")all=true;render();}
  async function load(id){const token=++loadToken;project=null;render();msg("파이프라인을 불러오는 중입니다.");try{const value=await api("/api/projects/"+encodeURIComponent(id));if(token!==loadToken)return;project=value;Prefs.set("workspaceProject",id);stage=Prefs.get("workspaceStage:"+id,stages()[0]?.id);tag=Prefs.get("workspaceTag:"+id,"");if(!project.memos.some(m=>m.tags.includes(tag)))tag="";if(!stages().some(s=>s.id===stage))stage=stages()[0]?.id;render();msg("");paintWork();}catch(error){if(token===loadToken)msg(error.message,true);}}
  // One line under the stage cards: the work status the 업무 현황 window opens on. Hidden when the pipeline has no documents.
  async function paintWork(){
    const box=$("pipeline-work"),id=project?.id,token=loadToken;box.hidden=true;box.replaceChildren();
    if(!id||typeof WorkAssist==="undefined")return;
    try{const s=await WorkAssist.summary(id);if(!s||token!==loadToken||project?.id!==id)return;
      box.append(node("strong","","업무 현황"),node("span","pipeline-work-date",`${s.point?s.point+" · ":""}${s.asOf} 기준`),node("span","pipeline-work-counts",`업무 ${s.count}개 · ${s.text}`),node("span","pipeline-work-open","열기 ›"));
      box.hidden=false;}
    catch(error){box.hidden=true;}
  }
  function render(){
    const steps=$("pipeline-steps"),list=$("pipeline-memos");steps.replaceChildren();list.replaceChildren();
    $("memo-add").disabled=!project;$("pipeline-all").disabled=!project;$("memo-refresh").disabled=!project;$("memo-brief").disabled=!project;$("memo-work").disabled=!project||typeof WorkAssist==="undefined";
    if(project&&!stages().some(s=>s.id===stage))stage=stages()[0]?.id;
    $("pipeline-dock").dataset.memoView=all?"all":"selected";
    $("pipeline-dock").style.setProperty("--stage-count",String(stages().length));
    list.dataset.stageId=all?"":stage||"";$("pipeline-memo-title").dataset.stageId=all?"":stage||"";
    $("pipeline-all").textContent=all?"선택 단계만 보기":"전 단계 메모";
    $("pipeline-all").setAttribute("aria-pressed",String(all));
    if(!project){$("pipeline-memo-title").textContent="단계별 메모";list.append(node("p","muted small","개발 중인 제품의 파이프라인을 추가하고 단계별로 메모와 근거를 저장하세요."));return;}
    const current=stages();
    for(const [index,s] of current.entries()){
      const b=button("","pipeline-step",()=>{stage=s.id;Prefs.set("workspaceStage:"+project.id,stage);all=false;render();});b.dataset.stageId=s.id;b.id="pipeline-stage-"+s.id;b.title=s.description||s.label;b.setAttribute("aria-pressed",String(stage===s.id&&!all));
      b.append(node("span","pipeline-step-number",String(index+1).padStart(2,"0")),node("strong","",s.short),node("span","pipeline-step-count",`${memoList(s.id).length}건`));steps.append(b);
    }
    const tags=[...new Set(project.memos.flatMap(m=>m.tags))].sort((a,b)=>a.localeCompare(b,"ko"));
    const filter=$("memo-tag-filter");filter.replaceChildren();const every=node("option","","전체 태그");every.value="";filter.append(every);
    if(tag&&!tags.includes(tag))tags.unshift(tag);for(const t of tags){const option=node("option","","#"+t);option.value=t;filter.append(option);}filter.value=tag;
    $("pipeline-memo-title").textContent=all?`${project.name} · 전 단계 메모`:`${stages().find(s=>s.id===stage)?.label||""} · 메모`;
    list.classList.toggle("all-stages",all);
    for(const s of all?stages():stages().filter(s=>s.id===stage)){
      const target=all?node("section","pipeline-column"):list;if(all){target.dataset.stageId=s.id;target.setAttribute("aria-labelledby","pipeline-stage-"+s.id);target.append(node("h3","sr-only",s.label));}
      const own=memoList(s.id);if(!own.length)target.append(emptyMemo(s));
      for(const memo of own)target.append(card(memo));if(all)list.append(target);
    }
  }
  function card(memo){
    const box=node("article","pipeline-memo "+(memo.origin==="rag"?"from-rag":"from-user"));box.dataset.memoId=memo.id;
    const meta=node("div","pipeline-memo-meta");meta.append(node("span","memo-origin",originLabel(memo)));if(memo.products.length)meta.append(node("span","","참고 제품 · "+memo.products.join(" · ")));meta.append(button("수정","link-button",()=>openEditor({memo})));box.append(meta);
    const open=button("","memo-open",()=>openReader(memo)),query=node("span","memo-query"),arrow=node("span","memo-open-arrow","↗");arrow.setAttribute("aria-hidden","true");open.setAttribute("aria-haspopup","dialog");questionTitle(query,memo);open.append(query,arrow);box.append(open);
    if(memo.tags.length){const chips=node("div","memo-tags");for(const t of memo.tags)chips.append(button("#"+t,"memo-tag",()=>{tag=t;Prefs.set("workspaceTag:"+project.id,tag);render();}));box.append(chips);}
    if(memo.items?.length){const summary=node("div","memo-item-summary");for(const kind of new Set(memo.items.map(i=>i.type)))summary.append(node("span","memo-item-badge",`${definitions.types[kind]||kind} ${memo.items.filter(i=>i.type===kind).length}`));const subjects=[...new Set(memo.items.map(i=>i.subject).filter(Boolean))];if(subjects.length)summary.append(node("span","",subjects.join(" · ")));box.append(summary);}
    box.append(node("div","memo-time",`${date(memo.updated_at)} · 수정 ${memo.revision} · ${memo.stage_ids.map(id=>stages().find(s=>s.id===id)?.short||id).join(" / ")}`));return box;
  }
  function openReader(memo){
    const d=dialog("단계 메모"),content=node("div","workspace-dialog-body memo-reader-content"),query=node("h3","memo-reader-question");d.classList.add("memo-reader-dialog");d.setAttribute("aria-label","단계 메모 상세");d.querySelector(".workspace-dialog-head .icon-button").setAttribute("aria-label","메모 상세 닫기");
    const layout=node("div","memo-reader-layout"),evidence=node("div","memo-reader-evidence"),switcher=node("div","memo-reader-switch");switcher.setAttribute("role","group");switcher.setAttribute("aria-label","메모와 근거 보기");
    const setPane=value=>{layout.dataset.activePane=value;for(const b of switcher.querySelectorAll("button"))b.setAttribute("aria-pressed",String(b.dataset.pane===value));};
    for(const [value,label] of [["memo","메모"],["evidence","근거 원문"]]){const b=button(label,"reader-pane-button",()=>setPane(value));b.dataset.pane=value;switcher.append(b);}setPane("memo");
    questionTitle(query,memo);const meta=node("div","pipeline-memo-meta");meta.append(node("span","memo-origin",originLabel(memo)),node("span","",memo.stage_ids.map(id=>stages().find(s=>s.id===id)?.label||id).join(" / ")));
    content.append(meta,query);
    if(memo.items?.length){const section=node("section","memo-item-list");section.append(node("h3","","항목별 정리"));for(const item of memo.items)section.append(itemView(item,()=>{const source=memo.source;if(!source||!item.evidence?.length)return;const e=item.evidence[0];Viewer.open({jobId:source.job_id,resultIndex:source.result_index,eid:e.id,cited:source.evidence.map(x=>({eid:x.id,title:x.title,page:x.pdf_page}))});setPane("evidence");}));content.append(section);}
    else if(memo.source?.kind!=="evidence"&&memo.source){const restore=button("원 답변에서 항목 불러오기","download",async()=>{restore.disabled=true;try{const value=await send(`/api/projects/${project.id}/memos/${memo.id}/structure`,{expected_revision:memo.revision});project=value;render();d.close();openReader(value.memos.find(m=>m.id===memo.id));}catch(e){restore.disabled=false;content.append(node("p","error",e.message));}});content.append(node("p","muted small","기존 본문을 보존하고 저장 당시 보고서의 주장·인용을 불러옵니다. 수정한 메모와 원 답변 항목은 다를 수 있습니다."),restore);}
    if(memo.items?.length){const original=node("details","scope-details");original.append(node("summary","","원 메모 · 추가 설명"),node("p","pipeline-memo-body memo-reader-body",memo.body));content.append(original);}else content.append(node("p","pipeline-memo-body memo-reader-body",memo.body));
    if(memo.source){const refs=node("div","memo-source-links"),source=memo.source,cited=source.evidence.map(e=>({eid:e.id,title:e.title,page:e.pdf_page}));for(const e of source.evidence)refs.append(button(`[${e.id}] 원문 · p.${e.pdf_page}`,"cite",()=>{Viewer.open({jobId:source.job_id,resultIndex:source.result_index,eid:e.id,cited});setPane("evidence");}));content.append(refs);}
    if(memo.tags.length)content.append(node("p","memo-reader-tags",memo.tags.map(t=>"#"+t).join(" · ")));
    content.append(node("p","memo-time",`${date(memo.updated_at)} · 수정 ${memo.revision}`));const actions=node("div","workspace-dialog-actions");actions.append(button("메모 수정","download",()=>{d.close();openEditor({memo});}),button("닫기","primary-button",()=>d.close()));content.append(actions);layout.append(content,evidence);d.append(switcher,layout);
    const release=Viewer.attach(evidence,open=>{d.classList.toggle("has-evidence",open);if(!open)setPane("memo");});d.addEventListener("close",release,{once:true});
    d.addEventListener("cancel",event=>{if(Viewer.isOpen()){event.preventDefault();Viewer.close();}});d.showModal();
  }
  function dialog(title){const d=node("dialog","workspace-dialog"),head=node("div","workspace-dialog-head");head.append(node("h2","",title),button("×","icon-button",()=>d.close()));d.append(head);document.body.append(d);d.addEventListener("close",()=>d.remove(),{once:true});return d;}
  function field(form,label,control){const wrap=node("label","workspace-field");wrap.append(node("span","",label),control);form.append(wrap);return control;}
  function selectField(parent,label,options,value){const select=node("select");for(const [key,name] of Object.entries(options)){const option=node("option","",name);option.value=key;select.append(option);}select.value=value;field(parent,label,select);return select;}
  function itemView(item,onOpen){
    const box=node("article","memo-item");box.dataset.itemKey=item.key;
    const head=node("div","memo-item-summary");head.append(node("strong","",definitions.types[item.type]||item.type),node("span","",item.subject||"대상 미지정"),node("span","",roleNames[item.statement_role]||item.statement_role));
    box.append(head,node("p","memo-item-text",item.text),node("p","muted small",`자료: ${definitions.modalities[item.modality]||item.modality} · 성숙도: ${definitions.data_maturity[item.data_maturity]||item.data_maturity} · 유효성: ${definitions.run_validity[item.run_validity]||item.run_validity} · 검토 전`));
    if(item.annotation_basis)box.append(node("p","muted small","분류·자료 상태: 사용자 입력 · 미검증"));
    if(item.note)box.append(node("p","memo-item-note","추가 의견 · "+item.note));
    for(const e of item.evidence||[])box.append(node("p","memo-source",`${e.title} · p.${e.pdf_page} · ${e.document_id}`));
    if(onOpen&&(item.evidence?.length||item.memo_id))box.append(button(item.evidence?.length?"항목 원문 보기":"원 메모 보기","cite",onOpen));return box;
  }
  async function openBrief(){
    if(!project)return;
    const current=project,d=dialog("업무별 항목 · 내부 검토용"),body=node("div","workspace-dialog-body");
    const view=selectField(body,"업무",definitions.tasks,"result_review");
    const subjects=Object.create(null);subjects[""]="전체 대상";for(const memo of current.memos)if(!memo.archived)for(const item of memo.items||[])if(item.subject)subjects[item.subject]=item.subject;
    const subject=selectField(body,"실험·시험·쟁점",subjects,""),target=node("div");body.append(target);d.append(body);let token=0,data=null;
    function paint(){
      target.replaceChildren(node("p","muted small",data.description));
      if(data.unstructured_memo_count)target.append(node("p","muted small",`항목을 불러오지 않은 기존 메모 ${data.unstructured_memo_count}개는 아직 포함되지 않았습니다.`));
      let count=0;for(const group of data.groups){
        const items=group.items.filter(item=>!subject.value||item.subject===subject.value);if(!items.length)continue;count+=items.length;
        const section=node("section","memo-item-list");section.append(node("h3","",group.label));
        for(const item of items)section.append(itemView(item,()=>{const memo=current.memos.find(m=>m.id===item.memo_id);if(memo){d.close();openReader(memo);}}));target.append(section);
      }
      if(!count)target.append(node("p","muted","표시할 항목이 없습니다. 메모를 저장하거나 기존 메모에서 항목을 불러오세요."));
    }
    async function show(){
      const own=++token;target.replaceChildren(node("p","muted","저장한 항목을 모으고 있습니다."));
      try{const value=await api(`/api/projects/${current.id}/briefs/${view.value}`);if(own!==token||!d.isConnected)return;data=value;paint();}
      catch(e){if(own===token)target.replaceChildren(node("p","error",e.message));}
    }
    view.onchange=show;subject.onchange=()=>{if(data)paint();};d.showModal();show();
  }
  async function createProject(){
    if(!inventory)return;const d=dialog("파이프라인 추가"),form=node("form","workspace-dialog-body"),error=node("p","workspace-form-error");error.setAttribute("role","alert");
    form.append(node("p","muted small","회사에서 개발 중인 제품의 RA 흐름과 메모를 관리합니다."));
    const name=field(form,"파이프라인 이름",node("input"));name.required=true;name.maxLength=120;name.placeholder="예: 사내 개발 코드 또는 개발 제품명";
    const ingredient=field(form,"원성분",node("select"));for(const i of inventory.ingredients){const option=node("option","",i.name);option.value=i.name;ingredient.append(option);}
    form.append(node("p","muted small","비교·참고할 제품과 기관은 왼쪽 ‘자료·검색 범위’에서 선택하세요."));
    const submit=button("파이프라인 추가","primary-button");submit.type="submit";form.append(error,submit);d.append(form);
    form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{const value=await send("/api/projects",{name:name.value,ingredient:ingredient.value});project=value;stage=stages()[0]?.id;tag="";archived=false;$("memo-archived").checked=false;projects.unshift(value);populateProjects();$("workspace-project").value=value.id;Prefs.set("workspaceProject",value.id);d.close();render();msg("파이프라인을 추가했습니다. 비교 제품과 기관은 왼쪽에서 선택하세요.");}catch(e){error.textContent=e.message;}finally{submit.disabled=false;}};
    d.showModal();name.focus();
  }
  function populateProjects(){const select=$("workspace-project");select.replaceChildren();if(!projects.length){const empty=node("option","","파이프라인을 추가하세요");empty.value="";select.append(empty);}for(const p of projects){const option=node("option","",`${p.name} · ${p.ingredient}`);option.value=p.id;select.append(option);}if(project)select.value=project.id;}
  function openEditor({memo=null,source=null,body="",products=[],claims=[]}={}){
    if(!project){msg("먼저 개발 제품의 파이프라인을 추가하세요.",true);$("workspace-create").focus();return;}
    const current=project,d=dialog(memo?"메모 수정 · 태그":"단계 메모 저장"),form=node("form","workspace-dialog-body"),error=node("p","workspace-form-error");error.setAttribute("role","alert");
    const bodyInput=field(form,"메모",node("textarea"));bodyInput.rows=5;bodyInput.maxLength=8000;bodyInput.required=true;bodyInput.value=memo?.body||body;
    const rows=memo?(memo.items||[]):(!source?[{key:"manual",text:"직접 메모",statement_role:"user_note"}]:claims.map(c=>({key:c.id,text:c.text,statement_role:c.kind||c.statement_role}))),controls=[];
    if(rows.length){const list=node("section","memo-item-editor");list.append(node("h3","","저장할 항목과 분류"),node("p","muted small","유형·대상을 정리합니다. 원문·발언 주체와 검토 상태는 보존되며 분류만으로 실험 결과가 인증되지는 않습니다."));for(const item of rows){const box=node("fieldset","memo-item-fields");box.dataset.itemKey=item.key;const legend=node("legend","",item.key);box.append(legend);let choose=null;if(!memo&&source?.kind==="answer"){choose=node("input");choose.type="checkbox";choose.checked=true;const label=node("label","memo-item-select");label.append(choose,"이 항목 저장");box.append(label);choose.onchange=()=>{bodyInput.value=controls.filter(c=>!c.choose||c.choose.checked).map(c=>c.item.text).join("\n\n");};}box.append(node("p","memo-item-text",item.text));const grid=node("div","memo-item-grid"),kind=selectField(grid,"유형",definitions.types,item.type||(item.statement_role==="interpretation"?"interpretation":"unclassified")),subject=field(grid,"실험·시험·쟁점",node("input"));subject.value=item.subject||"";subject.maxLength=120;subject.placeholder="예: SB17 IV FcRn 검토";const modality=selectField(grid,"자료 성격",definitions.modalities,item.modality||"unclassified"),maturity=selectField(grid,"데이터 성숙도",definitions.data_maturity,item.data_maturity||"not_disclosed"),validity=selectField(grid,"시험 유효성",definitions.run_validity,item.run_validity||"not_disclosed"),note=field(grid,"추가 의견 · 원문과 별도",node("textarea"));note.rows=2;note.maxLength=2000;note.value=item.note||"";box.append(grid,node("p","muted small",`${roleNames[item.statement_role]||item.statement_role||"원문 역할 미분류"} · 검토 전`));list.append(box);controls.push({item,choose,kind,subject,modality,maturity,validity,note});}form.append(list);}
    const stageBox=node("fieldset","workspace-choices");stageBox.append(node("legend","","저장할 단계 · 여러 개 선택 가능"));
    const chosen=memo?.stage_ids||[stage];for(const s of stages()){const label=node("label"),input=node("input");input.type="checkbox";input.value=s.id;input.checked=chosen.includes(s.id);label.append(input,s.label);stageBox.append(label);}form.append(stageBox);
    const referenceProducts=memo?.products||products;if(referenceProducts.length)form.append(node("p","muted small","참고 제품 · "+referenceProducts.join(" · ")));
    let currentTags=[...(memo?.tags||[])];const tagArea=node("div","workspace-field"),tagLabel=node("label","","태그 · 협력사나 쟁점"),tagInput=node("input"),chips=node("div","memo-tags");tagInput.id="memo-tag-entry";tagInput.maxLength=60;tagInput.placeholder="예: CRO A, 분석자료 요청";tagLabel.htmlFor=tagInput.id;
    const paintTags=()=>{chips.replaceChildren();for(const t of currentTags){const b=button("#"+t+" ×","memo-tag",()=>{currentTags=currentTags.filter(v=>v!==t);paintTags();});b.setAttribute("aria-label",t+" 태그 제거");chips.append(b);}};
    const addTag=()=>{for(const raw of tagInput.value.split(/[,，\n]/)){const t=raw.trim().replace(/^#+/,"");if(t&&!currentTags.some(v=>v.toLocaleLowerCase()===t.toLocaleLowerCase()))currentTags.push(t);}tagInput.value="";paintTags();};
    tagInput.onkeydown=e=>{if((e.key==="Enter"||e.key===",")&&!e.isComposing){e.preventDefault();addTag();}};
    tagArea.append(tagLabel,tagInput,button("태그 추가","download",addTag),chips,node("span","muted small","Enter 또는 쉼표로 추가합니다. 태그를 누르면 제거할 수 있습니다."));form.append(tagArea);paintTags();
    const original=memo?.source?.original_text;if(original){const originalBox=node("details","scope-details");originalBox.append(node("summary","","저장 당시 RAG 원본"),node("p","pipeline-memo-body",original));form.append(originalBox);}
    if(memo?.history?.length){const history=node("details","scope-details");history.append(node("summary","",`이전 수정 이력 ${memo.history.length}개`));for(const h of [...memo.history].reverse()){history.append(node("strong","",`수정 ${h.revision} · ${date(h.updated_at)}`),node("p","pipeline-memo-body",h.body),node("p","muted small",(h.tags||[]).map(t=>"#"+t).join(" · ")));}form.append(history);}
    const actions=node("div","workspace-dialog-actions"),submit=button("저장","primary-button");submit.type="submit";actions.append(submit,button("취소","download",()=>d.close()));
    if(memo)actions.append(button(memo.archived?"보관 해제":"보관함으로 이동","link-button",()=>save(!memo.archived)));
    form.append(error,actions);d.append(form);const requestId=uuid();
    async function save(archiveValue=memo?.archived||false){addTag();const payload={body:bodyInput.value,stage_ids:[...stageBox.querySelectorAll("input:checked")].map(i=>i.value),products:memo?.products||[],tags:currentTags};
      const selected=controls.filter(c=>!c.choose||c.choose.checked);if(source?.kind==="answer"&&controls.length&&!selected.length){error.textContent="항목을 한 개 이상 선택하세요.";return;}if(controls.length)payload.annotations=selected.map(c=>({key:c.item.key,type:c.kind.value,subject:c.subject.value,modality:c.modality.value,data_maturity:c.maturity.value,run_validity:c.validity.value,note:c.note.value}));
      if(memo){payload.archived=archiveValue;payload.expected_revision=memo.revision;}else{payload.request_id=requestId;if(source)payload.source=source;}
      if(!memo&&source?.kind==="answer"&&controls.length)payload.source={...source,claim_ids:selected.map(c=>c.item.key)};
      form.querySelectorAll("input,textarea,select,button").forEach(e=>e.disabled=true);
      try{const value=await send(`/api/projects/${current.id}/memos`+(memo?`/${memo.id}/revisions`:""),payload);if(project?.id===current.id){project=value;render();}d.close();msg(value.duplicate?"이미 저장한 근거 메모입니다. 기존 메모를 수정하거나 보관함을 확인하세요.":"메모와 태그를 저장했습니다.");}
      catch(e){error.textContent=e.message;form.querySelectorAll("input,textarea,select,button").forEach(el=>el.disabled=false);}}
    form.onsubmit=e=>{e.preventDefault();save();};d.showModal();bodyInput.focus();editor=d;d.addEventListener("close",()=>{editor=null;},{once:true});
  }
  function pin({jobId,resultIndex=0,kind="claim",claimId,evidenceId,body,products=[],claims=[]}){const source={job_id:jobId,result_index:resultIndex,kind};if(claimId)source.claim_id=claimId;if(evidenceId)source.evidence_id=evidenceId;openEditor({source,body,products,claims});}
  async function init(data){inventory=data;$("workspace-create").onclick=createProject;$("workspace-project").onchange=e=>{if(e.target.value)load(e.target.value);};$("memo-add").onclick=()=>openEditor();$("memo-refresh").onclick=()=>project&&load(project.id);$("pipeline-all").onclick=()=>{all=!all;render();};$("memo-tag-filter").onchange=e=>{tag=e.target.value;if(project)Prefs.set("workspaceTag:"+project.id,tag);render();};$("memo-archived").onchange=e=>{archived=e.target.checked;render();};document.querySelectorAll("[data-workspace-mode]").forEach(b=>b.onclick=()=>setMode(b.dataset.workspaceMode));setMode(Prefs.get("workspaceMode","split"));
    $("memo-type-filter").onchange=e=>{itemType=e.target.value;render();};$("memo-brief").onclick=openBrief;$("memo-work").onclick=$("pipeline-work").onclick=()=>{if(project&&typeof WorkAssist!=="undefined")WorkAssist.open(project.id,project.name);};document.addEventListener("workassist:close",e=>{if(e.detail?.projectId===project?.id)paintWork();});for(const [key,label] of Object.entries(definitions.types)){const option=node("option","",label);option.value=key;$("memo-type-filter").append(option);}
    try{const records=await api("/api/projects");if(records.item_definitions)definitions=records.item_definitions;projects=records.projects;populateProjects();const params=new URL(location.href).searchParams,requested=params.get("project"),previous=Prefs.get("workspaceProject","");const id=projects.find(p=>p.id===requested)?.id||projects.find(p=>p.id===previous)?.id||projects[0]?.id;if(id){await load(id);$("workspace-project").value=id;const requestedStage=params.get("stage");if(id===requested&&stages().some(s=>s.id===requestedStage)){stage=requestedStage;all=false;Prefs.set("workspaceStage:"+id,stage);render();}}else render();}
    catch(error){$("workspace-project").replaceChildren(node("option","","파이프라인을 불러오지 못했습니다"));msg(error.message,true);}}
  return Object.freeze({init,pin});
})();
