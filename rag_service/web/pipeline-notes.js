"use strict";
// Development stages organize notes; they never change the RAG search filters.
const PipelineNotes=(()=>{
  let inventory=null,projects=[],project=null,stage="bridging",all=false,tag="",archived=false,loadToken=0,editor=null;
  const stages=()=>project?.stages||[];
  const msg=(text,error=false)=>{const box=$("pipeline-message");box.textContent=text;box.classList.toggle("is-error",error);};
  const send=(path,body)=>api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const uuid=()=>crypto.randomUUID().replaceAll("-","");
  const date=value=>new Date(value).toLocaleString("ko-KR",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"});
  const memoList=id=>(project?.memos||[]).filter(m=>m.stage_ids.includes(id)&&Boolean(m.archived)===archived&&(!tag||m.tags.some(t=>t.toLocaleLowerCase()===tag.toLocaleLowerCase())));
  function emptyMemo(s){
    const message=tag?"이 태그의 메모가 없습니다.":archived?"보관된 메모가 없습니다.":"이 단계에 메모를 남겨보세요.",canAdd=!tag&&!archived;
    const box=canAdd?button("","pipeline-empty is-action",()=>{stage=s.id;Prefs.set("workspaceStage:"+project.id,stage);all=false;render();openEditor();}):node("div","pipeline-empty");
    const text=node("span","pipeline-empty-text");if(canAdd)text.append(node("span","pipeline-empty-phrase","이 단계에 메모를"),document.createTextNode(" "),node("span","pipeline-empty-phrase","남겨보세요."));else text.textContent=message;
    box.append(text);if(canAdd){box.append(node("span","pipeline-empty-icon","+"));box.setAttribute("aria-label",s.label+" 메모 추가");}return box;
  }
  function setMode(mode){if(!["split","pipeline","chat"].includes(mode))mode="split";$("chat-pane").dataset.workspaceMode=mode;document.querySelectorAll("[data-workspace-mode]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.workspaceMode===mode)));Prefs.set("workspaceMode",mode);if(mode==="pipeline")all=true;render();}
  async function load(id){const token=++loadToken;project=null;render();msg("파이프라인을 불러오는 중입니다.");try{const value=await api("/api/projects/"+encodeURIComponent(id));if(token!==loadToken)return;project=value;Prefs.set("workspaceProject",id);stage=Prefs.get("workspaceStage:"+id,stages()[0]?.id);tag=Prefs.get("workspaceTag:"+id,"");if(!project.memos.some(m=>m.tags.includes(tag)))tag="";if(!stages().some(s=>s.id===stage))stage=stages()[0]?.id;render();msg("");}catch(error){if(token===loadToken)msg(error.message,true);}}
  function render(){
    const steps=$("pipeline-steps"),list=$("pipeline-memos");steps.replaceChildren();list.replaceChildren();
    $("memo-add").disabled=!project;$("pipeline-all").disabled=!project;$("memo-refresh").disabled=!project;
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
    const box=node("article","pipeline-memo "+(memo.origin==="rag"?"from-rag":"from-user"));
    const meta=node("div","pipeline-memo-meta");meta.append(node("span","memo-origin",memo.origin==="rag"?"RAG 근거":"직접 메모"));if(memo.products.length)meta.append(node("span","","참고 제품 · "+memo.products.join(" · ")));meta.append(button("수정","link-button",()=>openEditor({memo})));box.append(meta,node("p","pipeline-memo-body",memo.body));
    if(memo.tags.length){const chips=node("div","memo-tags");for(const t of memo.tags)chips.append(button("#"+t,"memo-tag",()=>{tag=t;Prefs.set("workspaceTag:"+project.id,tag);render();}));box.append(chips);}
    if(memo.source){const refs=node("div","memo-source-links"),source=memo.source,cited=source.evidence.map(e=>({eid:e.id,title:e.title,page:e.pdf_page}));
      for(const e of source.evidence)refs.append(button(`[${e.id}] 원문 · p.${e.pdf_page}`,"cite",()=>Viewer.open({jobId:source.job_id,resultIndex:source.result_index,eid:e.id,cited})));box.append(refs);}
    box.append(node("div","memo-time",`${date(memo.updated_at)} · 수정 ${memo.revision} · ${memo.stage_ids.map(id=>stages().find(s=>s.id===id)?.short||id).join(" / ")}`));return box;
  }
  function dialog(title){const d=node("dialog","workspace-dialog"),head=node("div","workspace-dialog-head");head.append(node("h2","",title),button("×","icon-button",()=>d.close()));d.append(head);document.body.append(d);d.addEventListener("close",()=>d.remove(),{once:true});return d;}
  function field(form,label,control){const wrap=node("label","workspace-field");wrap.append(node("span","",label),control);form.append(wrap);return control;}
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
  function openEditor({memo=null,source=null,body="",products=[]}={}){
    if(!project){msg("먼저 개발 제품의 파이프라인을 추가하세요.",true);$("workspace-create").focus();return;}
    const current=project,d=dialog(memo?"메모 수정 · 태그":"단계 메모 저장"),form=node("form","workspace-dialog-body"),error=node("p","workspace-form-error");error.setAttribute("role","alert");
    const bodyInput=field(form,"메모",node("textarea"));bodyInput.rows=5;bodyInput.maxLength=8000;bodyInput.required=true;bodyInput.value=memo?.body||body;
    const stageBox=node("fieldset","workspace-choices");stageBox.append(node("legend","","저장할 단계 · 여러 개 선택 가능"));
    const chosen=memo?.stage_ids||[stage];for(const s of stages()){const label=node("label"),input=node("input");input.type="checkbox";input.value=s.id;input.checked=chosen.includes(s.id);label.append(input,s.label);stageBox.append(label);}form.append(stageBox);
    const referenceProducts=memo?.products||products;if(referenceProducts.length)form.append(node("p","muted small","참고 제품 · "+referenceProducts.join(" · ")));
    let currentTags=[...(memo?.tags||[])];const tagArea=node("div","workspace-field"),tagLabel=node("label","","태그 · 협력사나 쟁점"),tagInput=node("input"),chips=node("div","memo-tags");tagInput.id="memo-tag-entry";tagInput.maxLength=60;tagInput.placeholder="예: CRO A, 분석자료 요청";tagLabel.htmlFor=tagInput.id;
    const paintTags=()=>{chips.replaceChildren();for(const t of currentTags){const b=button("#"+t+" ×","memo-tag",()=>{currentTags=currentTags.filter(v=>v!==t);paintTags();});b.setAttribute("aria-label",t+" 태그 제거");chips.append(b);}};
    const addTag=()=>{for(const raw of tagInput.value.split(/[,，\n]/)){const t=raw.trim().replace(/^#+/,"");if(t&&!currentTags.some(v=>v.toLocaleLowerCase()===t.toLocaleLowerCase()))currentTags.push(t);}tagInput.value="";paintTags();};
    tagInput.onkeydown=e=>{if((e.key==="Enter"||e.key===",")&&!e.isComposing){e.preventDefault();addTag();}};
    tagArea.append(tagLabel,tagInput,button("태그 추가","download",addTag),chips,node("span","muted small","Enter 또는 쉼표로 추가합니다. 태그를 누르면 제거할 수 있습니다."));form.append(tagArea);paintTags();
    const original=memo?.source?.original_text;if(original){const originalBox=node("details","scope-details");originalBox.append(node("summary","","저장 당시 RAG 원본"),node("p","pipeline-memo-body",original));form.append(originalBox);}
    if(memo?.history?.length){const history=node("details","scope-details");history.append(node("summary","",`이전 수정 이력 ${memo.history.length}개`));for(const h of [...memo.history].reverse()){history.append(node("strong","",`수정 ${h.revision} · ${date(h.updated_at)}`),node("p","pipeline-memo-body",h.body),node("p","muted small",h.tags.map(t=>"#"+t).join(" · ")));}form.append(history);}
    const actions=node("div","workspace-dialog-actions"),submit=button("저장","primary-button");submit.type="submit";actions.append(submit,button("취소","download",()=>d.close()));
    if(memo)actions.append(button(memo.archived?"보관 해제":"보관함으로 이동","link-button",()=>save(!memo.archived)));
    form.append(error,actions);d.append(form);const requestId=uuid();
    async function save(archiveValue=memo?.archived||false){addTag();const payload={body:bodyInput.value,stage_ids:[...stageBox.querySelectorAll("input:checked")].map(i=>i.value),products:memo?.products||[],tags:currentTags};
      if(memo){payload.archived=archiveValue;payload.expected_revision=memo.revision;}else{payload.request_id=requestId;if(source)payload.source=source;}
      form.querySelectorAll("input,textarea,button").forEach(e=>e.disabled=true);
      try{const value=await send(`/api/projects/${current.id}/memos`+(memo?`/${memo.id}/revisions`:""),payload);if(project?.id===current.id){project=value;render();}d.close();msg(value.duplicate?"이미 저장한 근거 메모입니다. 기존 메모를 수정하거나 보관함을 확인하세요.":"메모와 태그를 저장했습니다.");}
      catch(e){error.textContent=e.message;form.querySelectorAll("input,textarea,button").forEach(el=>el.disabled=false);}}
    form.onsubmit=e=>{e.preventDefault();save();};d.showModal();bodyInput.focus();editor=d;d.addEventListener("close",()=>{editor=null;},{once:true});
  }
  function pin({jobId,resultIndex=0,kind="claim",claimId,evidenceId,body,products=[]}){const source={job_id:jobId,result_index:resultIndex,kind};if(claimId)source.claim_id=claimId;if(evidenceId)source.evidence_id=evidenceId;openEditor({source,body,products});}
  async function init(data){inventory=data;$("workspace-create").onclick=createProject;$("workspace-project").onchange=e=>{if(e.target.value)load(e.target.value);};$("memo-add").onclick=()=>openEditor();$("memo-refresh").onclick=()=>project&&load(project.id);$("pipeline-all").onclick=()=>{all=!all;render();};$("memo-tag-filter").onchange=e=>{tag=e.target.value;if(project)Prefs.set("workspaceTag:"+project.id,tag);render();};$("memo-archived").onchange=e=>{archived=e.target.checked;render();};document.querySelectorAll("[data-workspace-mode]").forEach(b=>b.onclick=()=>setMode(b.dataset.workspaceMode));setMode(Prefs.get("workspaceMode","split"));
    try{const records=await api("/api/projects");projects=records.projects;populateProjects();const previous=Prefs.get("workspaceProject","");const id=projects.find(p=>p.id===previous)?.id||projects[0]?.id;if(id)await load(id);else render();}
    catch(error){$("workspace-project").replaceChildren(node("option","","파이프라인을 불러오지 못했습니다"));msg(error.message,true);}}
  return Object.freeze({init,pin});
})();
