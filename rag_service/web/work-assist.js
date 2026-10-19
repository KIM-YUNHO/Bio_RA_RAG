"use strict";
// 업무 현황: a pipeline's documents, work status and AI drafts, read as of a date.
// Status is computed by the server from the documents' records; the screen never edits it.
const WorkAssist=(()=>{
  const STANDING_CLASS={applicable:"is-applicable",unapproved_revision:"is-unapproved",draft:"is-unapproved",superseded:"is-superseded"};
  // Draft items quoting the documents carry no badge; opinions and the AI's own judgments do.
  const SOURCE_BADGE={attributed_opinion:["의견","is-opinion"],inference:["AI 판단","is-ai"],gap:["AI 판단","is-ai"],draft_text:["AI 제안 문구","is-ai"]};
  const TABS=[["board","진행 현황"],["documents","문서"],["draft","AI 초안"]];
  const send=(path,body)=>api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const when=value=>value?new Date(value).toLocaleString("ko-KR",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}):"";
  const md=iso=>iso.slice(5).replace("-","/");
  let state=null;

  function chip(id,onclick){const b=button(id,"work-chip",onclick);b.title=id+" 원문 보기";return b;}
  function badge(text,cls=""){return node("span","work-badge "+cls,text);}

  async function open(projectId,projectName){
    const d=node("dialog","workspace-dialog work-dialog"),head=node("div","workspace-dialog-head");
    head.append(node("h2","","업무 현황 · "+projectName),button("×","icon-button",()=>d.close()));
    head.querySelector(".icon-button").setAttribute("aria-label","닫기");
    const body=node("div","workspace-dialog-body work-body");d.append(head,body);document.body.append(d);
    d.addEventListener("close",()=>{if(state?.poll)clearTimeout(state.poll);state=null;d.remove();},{once:true});
    state={projectId,dialog:d,body,asOf:"",tab:Prefs.get("workTab","board"),overview:null,draft:null,poll:null,token:0};
    if(!TABS.some(([key])=>key===state.tab))state.tab="board";
    d.showModal();body.append(node("p","muted","문서를 불러오는 중입니다."));
    try{
      const docs=await api(`/api/projects/${projectId}/documents`);
      if(!state||state.dialog!==d)return;
      if(!docs.documents.length){body.replaceChildren(node("p","muted","이 파이프라인에는 등록한 문서가 없습니다. 문서 묶음을 등록하면 날짜별 진행 현황과 AI 초안을 볼 수 있습니다."));return;}
      state.simulation=docs.simulation;
      const dates=[...new Set(docs.documents.map(x=>x.date))].sort(),points=docs.simulation?.checkpoints||[];
      state.asOf=Prefs.get("workAsOf:"+projectId,"");
      if(!points.some(p=>p.as_of===state.asOf)&&!dates.includes(state.asOf))state.asOf=points.length?points[points.length-1].as_of:dates[dates.length-1];
      layout(dates,points);await load();
    }catch(e){if(state?.dialog===d)body.replaceChildren(node("p","error",e.message));}
  }

  function layout(dates,points){
    const {body}=state;body.replaceChildren();
    const disclosure=state.simulation?.disclosure;
    if(disclosure){const box=node("details","work-disclosure"),[first,...rest]=disclosure.split(/(?<=[.다])\s+/);box.append(node("summary","",first));if(rest.length)box.append(node("p","",rest.join(" ")));body.append(box);}
    const bar=node("div","work-controls"),label=node("label","work-asof"),select=node("select");select.id="work-as-of";
    label.append(node("span","","기준일"),select);
    const group=(name,rows)=>{if(!rows.length)return;const g=node("optgroup");g.label=name;for(const [value,text] of rows){const o=node("option","",text);o.value=value;g.append(o);}select.append(g);};
    group("시나리오 시점",points.map(p=>[p.as_of+"|"+p.id,`${p.id} · ${p.as_of} · ${p.label}`]));
    group("문서 날짜",dates.map(x=>[x,x+"까지"]));
    const point=points.find(p=>p.as_of===state.asOf&&p.id===Prefs.get("workPoint:"+state.projectId,""))||points.find(p=>p.as_of===state.asOf);
    select.value=point?state.asOf+"|"+point.id:state.asOf;state.pointId=point?.id||"";
    select.onchange=()=>{const [asOf,pointId]=select.value.split("|");state.asOf=asOf;state.pointId=pointId||"";Prefs.set("workAsOf:"+state.projectId,asOf);Prefs.set("workPoint:"+state.projectId,state.pointId);load();};
    const tabs=node("div","work-tabs");tabs.setAttribute("role","tablist");
    for(const [key,text] of TABS){const b=button(text,"work-tab",()=>{state.tab=key;Prefs.set("workTab",key);paint();});b.dataset.tab=key;b.setAttribute("role","tab");tabs.append(b);}
    bar.append(label,tabs);state.panel=node("div","work-panel");state.status=node("p","work-status muted small");state.status.setAttribute("role","status");
    body.append(bar,state.status,state.panel);
  }

  async function load(){
    const own=++state.token;state.status.textContent="기준일 상태를 계산하는 중입니다.";
    try{const value=await api(`/api/projects/${state.projectId}/workboard/${state.asOf}`);if(!state||own!==state.token)return;state.overview=value;
      state.status.textContent=value.hidden_after_count?`기준일 이후 문서 ${value.hidden_after_count}건은 숨겨지고 AI 초안에도 쓰이지 않습니다.`:"기준일까지의 문서를 모두 사용합니다.";paint();}
    catch(e){if(state&&own===state.token)state.status.textContent=e.message;}
  }

  function paint(){
    if(!state?.overview)return;
    for(const b of state.body.querySelectorAll(".work-tab"))b.setAttribute("aria-selected",String(b.dataset.tab===state.tab));
    state.panel.replaceChildren();
    ({board:paintBoard,documents:paintDocuments,draft:paintDraftTab})[state.tab]();
  }

  function unitBox(unit){
    const box=node("article","work-unit");box.dataset.unitId=unit.unit_id;
    const meta=node("div","work-unit-meta");meta.append(node("strong","",unit.unit_id),node("span","",unit.role_label||""),node("span","",unit.speaker));
    box.append(meta,node("p","work-unit-text",unit.text));return box;
  }
  function unitIndex(){const index={};for(const doc of state.overview.documents)for(const unit of doc.units)index[unit.unit_id]={...unit,document:doc};return index;}
  function showUnit(target,unit){
    target.replaceChildren();target.hidden=false;if(!unit){target.append(node("p","muted small","이 기준일의 문서가 아닙니다."));return;}
    const doc=unit.document,close=button("닫기","link-button",()=>{target.hidden=true;});
    target.append(node("p","work-unit-doc",doc?`${doc.date} · ${doc.title}`:""),unitBox(unit),close);
  }

  function reference(){
    // Changes are counted from the previous scenario point, or from the previous document date when a date is chosen.
    const points=(state.simulation?.checkpoints||[]).filter(p=>p.as_of<state.asOf);
    if(state.pointId)return points.length?{date:points.at(-1).as_of,label:`${points.at(-1).id}(${points.at(-1).as_of.slice(5).replace("-","/")})`}:null;
    const dates=state.overview.document_dates.filter(d=>d<state.asOf);
    return dates.length?{date:dates.at(-1),label:dates.at(-1)}:null;
  }

  function paintBoard(){
    const items=state.overview.items||[],panel=state.panel;
    if(!items.length){panel.append(node("p","muted","기준일까지 기록된 업무가 없습니다."));return;}
    const ref=reference(),since=ref?.date,open=state.openRows||(state.openRows=new Set());
    const fresh=it=>Boolean(since&&it.first_date>since),moved=it=>Boolean(since&&!fresh(it)&&it.last_change>since);
    const top=node("div","work-summary"),counts={};for(const it of items)counts[it.status.label]=(counts[it.status.label]||0)+1;
    top.append(node("strong","",`업무 ${items.length}개`),node("span","",Object.entries(counts).map(([k,v])=>`${k} ${v}`).join(" · ")));
    if(ref)top.append(node("span","work-summary-change",`${ref.label} 이후 새 업무 ${items.filter(fresh).length} · 진행된 업무 ${items.filter(moved).length}`));
    panel.append(top,node("p","work-hint","업무를 누르면 진행 이력과 결과가 펼쳐집니다. 모든 내용은 문서 기록에서 계산합니다. '기록 없음'은 안 했다는 뜻이 아닙니다."));
    for(const [title,kind] of [["시험·작업","work"],["문서·답변","document"]]){
      const members=items.filter(it=>it.kind===kind);if(!members.length)continue;
      const section=node("section","work-card-group"),list=node("div","work-list");section.append(node("h3","",title));
      for(const it of members){
        const row=node("details","work-row"),summary=node("summary"),main=node("div","work-row-main"),name=node("div","work-row-title");
        row.dataset.scopeId=it.scope_id;row.open=open.has(it.scope_id);row.addEventListener("toggle",()=>{row.open?open.add(it.scope_id):open.delete(it.scope_id);});
        name.append(node("strong","",it.label));if(fresh(it))name.append(badge("새 업무","is-new"));else if(moved(it))name.append(badge("진행됨","is-changed"));
        const sub=[it.now,it.waiting?"다음: "+it.waiting:""].filter(Boolean).join(" · ");main.append(name);if(sub)main.append(node("p","work-row-sub",sub));
        const day=node("span","work-row-date",md(it.last_change));day.title="마지막으로 상태가 바뀐 날";
        const toggle=node("span","work-row-toggle","▸");toggle.setAttribute("aria-hidden","true");
        summary.append(node("span","work-pill tone-"+it.status.tone,it.status.label),main,day,toggle);
        const body=node("div","work-row-body"),detail=node("div","work-unit-detail");detail.hidden=true;
        const history=node("ol","work-timeline");
        for(const e of it.events){const li=node("li",[since&&e.date>since&&e.changed?"is-new":"",e.changed?"":"is-restated"].join(" ").trim());li.append(node("span","work-time",md(e.date)),node("span","work-what",e.phrases.join(" · ")+(e.changed?"":" (재확인)")),node("span","work-who",`${e.who} · ${e.source}`),chip(e.unit_id,()=>showUnit(detail,unitIndex()[e.unit_id])));history.append(li);}
        body.append(node("h4","","진행 이력"),history,node("h4","",kind==="document"?"내용":"결과"));
        if(!it.results.length)body.append(node("p","muted small","아직 없음"));
        for(const r of it.results){const box=node("div","work-result-unit");box.append(node("p","work-result-meta",`${md(r.date)} · ${r.title} · ${r.unit_id}`),node("p","",r.text));body.append(box);}
        if(it.opinions.length){body.append(node("h4","","관련 의견"));for(const o of it.opinions){const p=node("p","work-opinion");p.append(node("strong","",o.speaker+" "),document.createTextNode(o.text),node("span","muted"," · "+md(o.date)));body.append(p);}}
        body.append(detail);row.append(summary,body);list.append(row);
      }
      section.append(list);panel.append(section);
    }
  }

  function paintDocuments(){
    const panel=state.panel;
    panel.append(node("p","work-hint","기준일까지 등록된 문서입니다. 펼치면 문단별 원문과 발언자가 보입니다. 계획서는 그날 효력이 있는 버전에 '현재 기준'이 붙습니다."));
    for(const doc of state.overview.documents){
      const box=node("details","work-doc"),summary=node("summary");
      summary.append(node("span","work-doc-date",doc.date.slice(5).replace("-","/")),node("strong","",doc.title),badge(doc.status_label,"is-status-"+doc.status));
      if(doc.standing_label)summary.append(badge(doc.standing_label,STANDING_CLASS[doc.standing]||""));
      if(doc.sharing==="partner")summary.append(badge("협력사 공유","is-sharing-partner"));
      box.append(summary,node("p","muted small",`${doc.document_id} v${doc.version} · ${doc.kind_label} · ${doc.author}`));
      for(const unit of doc.units)box.append(unitBox(unit));panel.append(box);
    }
  }

  function paintDraftTab(){
    const panel=state.panel,overview=state.overview,point=(state.simulation?.checkpoints||[]).find(p=>p.id===state.pointId&&p.as_of===state.asOf);
    const form=node("form","work-form"),task=node("select"),question=node("textarea");
    for(const [key,value] of Object.entries(overview.tasks)){const o=node("option","",value.label);o.value=key;task.append(o);}
    question.rows=3;question.maxLength=2000;question.required=true;
    task.value=point?.task||"result_review";question.value=point?.task===task.value?point.question:overview.tasks[task.value].template;
    task.onchange=()=>{question.value=point&&task.value===point.task?point.question:overview.tasks[task.value].template;};
    const taskField=node("label","workspace-field");taskField.append(node("span","","업무"),task);
    const questionField=node("label","workspace-field");questionField.append(node("span","",`질문 · ${state.asOf}까지의 문서만 씁니다`),question);
    const submit=button("AI 초안 작성","primary-button");submit.type="submit";
    const actions=node("div","work-form-actions");actions.append(submit,node("span","muted small","AI 호출 1회(15초 안팎) · 결과는 사람이 검토할 초안입니다."));
    form.append(taskField,questionField,actions);panel.append(form);
    const result=node("div","work-result"),history=node("section","work-history");panel.append(result,history);
    form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;result.replaceChildren(node("p","muted","작성 요청을 보내는 중입니다."));
      try{const summary=await send(`/api/projects/${state.projectId}/work-drafts`,{task:task.value,question:question.value,as_of:state.asOf});watch(summary.id,result,submit,history);}
      catch(e){result.replaceChildren(node("p","error",e.message));submit.disabled=false;}};
    if(state.draft&&state.draft.as_of===state.asOf)renderDraft(result,state.draft);
    listDrafts(history,result);
  }

  const draftState=d=>({running:"작성 중",failed:"실패",completed:d.review_status==="needs_review"?`확인 필요 ${d.warning_count}`:"검토 전 초안"})[d.state]||d.state;
  async function listDrafts(target,result){
    try{const {drafts}=await api(`/api/projects/${state.projectId}/work-drafts`);if(!state||!target.isConnected)return;
      target.replaceChildren(node("h3","","이전 초안"));if(!drafts.length){target.append(node("p","muted small","아직 작성한 초안이 없습니다."));return;}
      const list=node("div","work-history-list");
      for(const d of drafts){const label=`${d.as_of} · ${d.task_label}${d.audience==="partner"?" · 협력사 공유용":""} · ${draftState(d)} · ${when(d.created_at)}`;
        list.append(button(label,"work-history-item",async()=>{try{const record=await api(`/api/projects/${state.projectId}/work-drafts/${d.id}`);state.draft=record;renderDraft(result,record);result.scrollIntoView?.({block:"start"});}catch(e){result.replaceChildren(node("p","error",e.message));}}));}
      target.append(list);
    }catch(e){target.replaceChildren(node("p","error",e.message));}
  }

  function watch(id,result,submit,history){
    const tick=async()=>{
      if(!state)return;
      try{const record=await api(`/api/projects/${state.projectId}/work-drafts/${id}`);if(!state)return;
        if(record.state==="running"){result.replaceChildren(node("p","muted",`AI가 초안을 쓰는 중입니다 · ${record.as_of}까지의 문서 ${record.selected.length}개 사용`));state.poll=setTimeout(tick,2000);return;}
        state.draft=record;submit.disabled=false;renderDraft(result,record);listDrafts(history,result);}
      catch(e){result.replaceChildren(node("p","error",e.message));submit.disabled=false;}
    };tick();
  }

  function renderDraft(target,record){
    target.replaceChildren();
    if(record.state==="failed"){target.append(node("p","error","초안 작성에 실패했습니다: "+record.error));return;}
    if(record.state!=="completed")return;
    const units=Object.fromEntries(record.selected.map(u=>[u.unit_id,{...u,document:{date:u.date,title:u.title,version:u.version}}]));
    const warnings=record.audit.warnings,flagged=new Set(warnings.map(w=>w.column+":"+w.item));
    const detail=node("div","work-unit-detail");detail.hidden=true;
    const head=node("div","work-draft-head");
    head.append(badge(warnings.length?`확인 필요 ${warnings.length}`:"검토 전 초안",warnings.length?"is-warning":"is-draft"),
      node("strong","",record.task_label),node("span","muted",`기준일 ${record.as_of}${record.audience==="partner"?" · 협력사 공유용":""}`));
    target.append(head,node("p","work-draft-question",record.question));
    if(record.status_summary?.length){const box=node("div","work-status-summary");
      for(const line of record.status_summary){const row=node("p","");row.append(node("strong","","기록상 상태 "),node("span","",line.label+" — "+line.states.map(s=>s.label).join(" · ")+" "));
        for(const id of [...new Set(line.states.flatMap(s=>s.source_unit_ids))])row.append(chip(id,()=>showUnit(detail,units[id])));box.append(row);}
      target.append(box);}
    target.append(node("p","work-position",record.draft.position));
    const grid=node("div","work-columns");
    for(const column of record.draft.columns){
      const box=node("section","work-column");box.append(node("h3","",column.label));
      column.items.forEach((item,index)=>{const row=node("div","work-item"+(flagged.has(column.key+":"+index)?" has-warning":""));
        const text=node("p","");const tag=SOURCE_BADGE[item.basis];if(tag)text.append(badge(tag[0],tag[1]),document.createTextNode(" "));text.append(document.createTextNode(item.text));
        const refs=node("div","work-refs");for(const id of item.source_unit_ids)refs.append(chip(id,()=>showUnit(detail,units[id])));row.append(text,refs);box.append(row);});
      grid.append(box);
    }
    target.append(grid,detail);
    if(warnings.length){const box=node("section","work-warnings"),labels=Object.fromEntries(record.draft.columns.map(c=>[c.key,c.label]));
      box.append(node("h3","",`확인이 필요한 문장 ${warnings.length}`));
      for(const w of warnings){const row=node("div","work-warning");row.append(node("strong","",`${labels[w.column]} ${w.item+1}`),node("span","",w.detail));for(const id of w.source_unit_ids)row.append(chip(id,()=>showUnit(detail,units[id])));box.append(row);}
      target.append(box);}
    target.append(node("p","work-foot",`표시 없음 = 문서에 적힌 내용 · '의견' = 발언자가 있는 의견 · 'AI 판단' = AI가 정리한 판단. 문장 검사는 규칙 기반이라 모든 오류를 찾지 못합니다. · AI 입력: 문서 문단 ${record.selected.length}개(기준일까지 ${record.eligible_unit_ids.length}개) · ${record.model}`));
  }

  return Object.freeze({open});
})();
