"use strict";
// 업무 현황: a pipeline's documents, work status and issue guides (RAG answers organized by AI), read as of a date.
// Status is computed by the server from the documents' records; the screen never edits it.
const WorkAssist=(()=>{
  const STANDING_CLASS={applicable:"is-applicable",unapproved_revision:"is-unapproved",draft:"is-unapproved",superseded:"is-superseded"};
  // Draft items quoting the documents carry no badge; opinions and the AI's own judgments do.
  const GUIDE_BADGE={public:["공개 근거","is-public"],internal:["사내 자료","is-internal"],suggestion:["AI 제안","is-ai"]};
  const TABS=[["board","진행 현황"],["documents","문서"],["issue","이슈 대응"]];
  const send=(path,body)=>api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const when=value=>value?new Date(value).toLocaleString("ko-KR",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}):"";
  const md=iso=>iso.slice(5).replace("-","/");
  let state=null;

  function chip(id,onclick){const b=button(id,"work-chip",onclick);b.title=id+" 원문 보기";return b;}
  function badge(text,cls=""){return node("span","work-badge "+cls,text);}
  const tally=items=>{const counts={};for(const it of items)counts[it.status.label]=(counts[it.status.label]||0)+1;return Object.entries(counts).map(([k,v])=>`${k} ${v}`).join(" · ");};
  // The date the window opens on: the last one chosen for this pipeline, else the last scenario point or document date.
  function pickDate(projectId,docs){
    const dates=[...new Set(docs.documents.map(x=>x.date))].sort(),points=docs.simulation?.checkpoints||[];
    let asOf=Prefs.get("workAsOf:"+projectId,"");
    if(!points.some(p=>p.as_of===asOf)&&!dates.includes(asOf))asOf=points.length?points[points.length-1].as_of:dates[dates.length-1];
    const point=points.find(p=>p.as_of===asOf&&p.id===Prefs.get("workPoint:"+projectId,""))||points.find(p=>p.as_of===asOf);
    return {dates,points,asOf,point};
  }

  // One line for the pipeline board, on the same date the window opens on; null when the pipeline has no documents.
  async function summary(projectId){
    const docs=await api(`/api/projects/${projectId}/documents`);
    if(!docs.documents.length)return null;
    const {asOf,point}=pickDate(projectId,docs),board=await api(`/api/projects/${projectId}/workboard/${asOf}`);
    return {asOf,point:point?.id||"",count:board.items.length,text:tally(board.items),issues:(board.issues||[]).length};
  }

  async function open(projectId,projectName){
    const d=node("dialog","workspace-dialog work-dialog"),head=node("div","workspace-dialog-head");
    head.append(node("h2","","업무 현황 · "+projectName),button("×","icon-button",()=>d.close()));
    head.querySelector(".icon-button").setAttribute("aria-label","닫기");
    const body=node("div","workspace-dialog-body work-body");d.append(head,body);document.body.append(d);
    d.addEventListener("close",()=>{if(state?.poll)clearTimeout(state.poll);if(state?.viewerRelease){state.viewerRelease();Viewer.close();}state=null;d.remove();document.dispatchEvent(new CustomEvent("workassist:close",{detail:{projectId}}));},{once:true});
    state={projectId,dialog:d,body,asOf:"",tab:Prefs.get("workTab","board"),overview:null,draft:null,poll:null,token:0};
    if(!TABS.some(([key])=>key===state.tab))state.tab="board";
    d.showModal();body.append(node("p","muted","문서를 불러오는 중입니다."));
    try{
      const docs=await api(`/api/projects/${projectId}/documents`);
      if(!state||state.dialog!==d)return;
      if(!docs.documents.length){body.replaceChildren(node("p","muted","이 파이프라인에는 등록한 문서가 없습니다. 문서 묶음을 등록하면 날짜별 진행 현황과 AI 초안을 볼 수 있습니다."));return;}
      state.simulation=docs.simulation;
      const {dates,points,asOf,point}=pickDate(projectId,docs);state.asOf=asOf;
      layout(dates,points,point);await load();
    }catch(e){if(state?.dialog===d)body.replaceChildren(node("p","error",e.message));}
  }

  function layout(dates,points,point){
    const {body}=state;body.replaceChildren();
    const disclosure=state.simulation?.disclosure;
    if(disclosure){const box=node("details","work-disclosure"),[first,...rest]=disclosure.split(/(?<=[.다])\s+/);box.append(node("summary","",first));if(rest.length)box.append(node("p","",rest.join(" ")));body.append(box);}
    const bar=node("div","work-controls"),label=node("label","work-asof"),select=node("select");select.id="work-as-of";
    label.append(node("span","","기준일"),select);
    const group=(name,rows)=>{if(!rows.length)return;const g=node("optgroup");g.label=name;for(const [value,text] of rows){const o=node("option","",text);o.value=value;g.append(o);}select.append(g);};
    group("시나리오 시점",points.map(p=>[p.as_of+"|"+p.id,`${p.id} · ${p.as_of} · ${p.label}`]));
    group("문서 날짜",dates.map(x=>[x,x+"까지"]));
    select.value=point?state.asOf+"|"+point.id:state.asOf;state.pointId=point?.id||"";
    select.onchange=()=>{const [asOf,pointId]=select.value.split("|");state.asOf=asOf;state.pointId=pointId||"";Prefs.set("workAsOf:"+state.projectId,asOf);Prefs.set("workPoint:"+state.projectId,state.pointId);load();};
    const tabs=node("div","work-tabs");tabs.setAttribute("role","tablist");
    for(const [key,text] of TABS){const b=button(text,"work-tab",()=>{state.tab=key;Prefs.set("workTab",key);paint();});b.dataset.tab=key;b.setAttribute("role","tab");tabs.append(b);}
    bar.append(label,tabs);state.panel=node("div","work-panel");state.status=node("p","work-status muted small");state.status.setAttribute("role","status");
    state.viewerHost=node("section","work-viewer");state.viewerHost.hidden=true;
    body.append(bar,state.status,state.panel,state.viewerHost);
  }

  async function load(){
    const own=++state.token;state.status.textContent="기준일 상태를 계산하는 중입니다.";
    try{const value=await api(`/api/projects/${state.projectId}/workboard/${state.asOf}`);if(!state||own!==state.token)return;state.overview=value;
      state.status.textContent=value.hidden_after_count?`기준일 이후 문서 ${value.hidden_after_count}건은 숨겨집니다.`:"기준일까지의 문서를 모두 보여 줍니다.";paint();}
    catch(e){if(state&&own===state.token)state.status.textContent=e.message;}
  }

  function paint(){
    if(!state?.overview)return;
    for(const b of state.body.querySelectorAll(".work-tab"))b.setAttribute("aria-selected",String(b.dataset.tab===state.tab));
    state.panel.replaceChildren();
    ({board:paintBoard,documents:paintDocuments,issue:paintIssue})[state.tab]();
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
    const top=node("div","work-summary");
    top.append(node("strong","",`업무 ${items.length}개`),node("span","",tally(items)));
    if(ref)top.append(node("span","work-summary-change",`${ref.label} 이후 새 업무 ${items.filter(fresh).length} · 진행된 업무 ${items.filter(moved).length}`));
    for(const issue of state.overview.issues||[]){const bar=button("","work-issue-banner",()=>{state.tab="issue";Prefs.set("workTab","issue");paint();});
      bar.append(badge("이슈","is-issue"),node("strong","",issue.title),node("span","muted",md(issue.date)),node("span","work-issue-open","이슈 대응 보기 ›"));panel.append(bar);}
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

  // 이슈 대응: the issue's public-source questions run through the RA service, then one AI call organizes the cited answers.
  function paintIssue(){
    const panel=state.panel,issues=state.overview.issues||[];
    if(!issues.length){panel.append(node("p","muted","기준일까지 등록된 이슈가 없습니다. 이슈는 문서 묶음과 함께 등록합니다."));return;}
    const detail=node("div","work-unit-detail");detail.hidden=true;
    const result=node("div","issue-result"),history=node("section","work-history"),runs=[],setRuns=busy=>{for(const b of runs)b.disabled=busy;};
    for(const issue of issues){
      const card=node("section","issue-card"),head=node("div","issue-head");
      head.append(badge("이슈","is-issue"),node("strong","",issue.title),node("span","muted",md(issue.date)));
      const refs=node("div","work-refs");refs.append(node("span","muted small","사내 근거 "));for(const id of issue.unit_ids)refs.append(chip(id,()=>showUnit(detail,unitIndex()[id])));
      const asks=node("details","issue-questions"),list=node("ol");asks.append(node("summary","",`공개 자료에 물어볼 질문 ${issue.questions.length}개`));
      for(const q of issue.questions)list.append(node("li","",q.text));asks.append(list);
      const scope=node("p","issue-scope","검색 범위: 같은 성분 바이오시밀러의 공개 심사 자료와 지침"+(issue.search.note?" · "+issue.search.note:""));
      const run=button("RAG 대응 가이드 만들기","primary-button",async()=>{setRuns(true);result.replaceChildren(node("p","muted","요청을 보내는 중입니다."));
        try{const s=await send(`/api/projects/${state.projectId}/issue-guides`,{issue_id:issue.id});watchGuide(s.id,result,setRuns,history);}
        catch(e){result.replaceChildren(node("p","error",e.message));setRuns(false);}});runs.push(run);
      const actions=node("div","work-form-actions");actions.append(run,node("span","muted small",`공개 자료 검색 ${issue.questions.length}회 + AI 정리 1회 · 5분 안팎 · 결과는 사람이 검토할 가이드입니다.`));
      card.append(head,node("p","issue-summary",issue.summary),refs,detail,asks,scope,actions);panel.append(card);
    }
    panel.append(result,history);
    if(state.guide&&issues.some(i=>i.id===state.guide.issue_id))renderGuide(result,state.guide);
    // A guide still being made (e.g. after switching tabs) is followed again here.
    listGuides(history,result,running=>{setRuns(true);watchGuide(running.id,result,setRuns,history);});
  }

  const guideState=g=>(({running:"만드는 중",failed:"실패",completed:g.points?`정답 대조 ${g.covered}/${g.points}`:"완료"})[g.state]||g.state)+(g.failed_questions?` · 검색 실패 ${g.failed_questions}`:"");
  async function listGuides(target,result,onRunning){
    try{const {guides}=await api(`/api/projects/${state.projectId}/issue-guides`);if(!state||!target.isConnected)return;
      const running=guides.find(g=>g.state==="running");if(running&&onRunning)onRunning(running);
      target.replaceChildren(node("h3","","이전 가이드"));if(!guides.length){target.append(node("p","muted small","아직 만든 가이드가 없습니다."));return;}
      const list=node("div","work-history-list");
      for(const g of guides)list.append(button(`${g.issue_title} · ${guideState(g)} · ${when(g.created_at)}`,"work-history-item",async()=>{
        try{const record=await api(`/api/projects/${state.projectId}/issue-guides/${g.id}`);state.guide=record;renderGuide(result,record);result.scrollIntoView?.({block:"start"});}
        catch(e){result.replaceChildren(node("p","error",e.message));}}));
      target.append(list);
    }catch(e){target.replaceChildren(node("p","error",e.message));}
  }

  const STEP={waiting:"대기",running:"검색 중",completed:"완료",failed:"실패"};
  function watchGuide(id,result,setRuns,history){
    if(state.poll)clearTimeout(state.poll);
    const token=state.watch=(state.watch||0)+1;
    const tick=async()=>{
      if(!state||state.watch!==token||!result.isConnected)return;
      try{const record=await api(`/api/projects/${state.projectId}/issue-guides/${id}`);if(!state||state.watch!==token)return;
        if(record.state==="running"){
          const box=node("div","issue-progress");box.append(node("p","",record.stage==="writing"?"검색이 끝났습니다. AI가 가이드를 정리하는 중입니다.":"공개 자료를 검색하는 중입니다."));
          const steps=node("ol","issue-steps");for(const q of record.questions)steps.append(node("li","is-"+q.state,`${STEP[q.state]||q.state} · ${q.text}`));
          box.append(steps);result.replaceChildren(box);state.poll=setTimeout(tick,3000);return;}
        state.guide=record;setRuns(false);renderGuide(result,record);listGuides(history,result);}
      catch(e){result.replaceChildren(node("p","error",e.message));setRuns(false);}
    };tick();
  }

  // Public sources open in the source viewer, moved into this window on first use.
  function openSource(record,source){
    if(!state.viewerRelease){const host=state.viewerHost;state.viewerRelease=Viewer.attach(host,open=>{host.hidden=!open;});}
    const cited=record.sources.filter(s=>s.job_id===source.job_id).map(s=>({eid:s.evidence_id,title:s.title,page:s.page}));
    Viewer.open({jobId:source.job_id,resultIndex:0,eid:source.evidence_id,cited});state.viewerHost.scrollIntoView?.({block:"start"});
  }
  const sourceLabel=s=>`${s.id} · ${s.product||"지침"} ${s.agency||""} p.${s.page}`.replace(/\s+/g," ");

  function renderGuide(target,record){
    target.replaceChildren();
    if(record.state==="failed"){target.append(node("p","error","가이드를 만들지 못했습니다: "+record.error));return;}
    if(record.state!=="completed")return;
    const sources=Object.fromEntries(record.sources.map(s=>[s.id,s])),units=Object.fromEntries(record.facts.map(f=>[f.id,{unit_id:f.id,text:f.text,speaker:f.speaker,document:{date:f.date,title:f.title}}]));
    const detail=node("div","work-unit-detail");detail.hidden=true;
    const refs=ids=>{const box=node("div","work-refs");for(const id of ids){if(sources[id]){const b=button(sourceLabel(sources[id]),"work-chip is-public",()=>openSource(record,sources[id]));b.title=sources[id].title;box.append(b);}else if(units[id])box.append(chip(id,()=>showUnit(detail,units[id])));}return box;};
    const head=node("div","work-draft-head");
    head.append(badge(record.warnings.length?`확인 필요 ${record.warnings.length}`:"검토 전 가이드",record.warnings.length?"is-warning":"is-draft"),node("strong","",record.issue_title),node("span","muted",when(record.created_at)));
    const scope=record.scope.excluded_products.length?` · 검색에서 뺀 제품: ${record.scope.excluded_products.join(", ")}`:"";
    const failed=record.answers.filter(a=>a.state!=="completed").length;
    target.append(head,node("p","issue-scope",`검색한 제품: ${record.scope.products.join(", ")} 및 공통 지침${scope} · 인용된 공개 자료 ${record.sources.length}쪽`));
    if(failed)target.append(node("p","error",`공개 자료 검색 ${record.answers.length}건 중 ${failed}건이 실패해, 그 질문의 근거 없이 정리했습니다.`));
    target.append(node("p","work-position",record.guide.summary));
    const grid=node("div","issue-sections"),flagged=new Set(record.warnings.map(w=>w.section+":"+w.item));
    for(const section of record.guide.sections){
      const box=node("section","work-column issue-section is-"+section.key);box.append(node("h3","",section.label));
      section.items.forEach((item,index)=>{const row=node("div","work-item"+(flagged.has(section.key+":"+index)?" has-warning":""));
        if(section.key==="mock_questions"){row.append(node("p","issue-q","Q. "+item.question),node("p","issue-why","왜 묻나: "+item.why),node("p","issue-prepare","준비할 자료: "+item.prepare));}
        else{const text=node("p","");const tag=GUIDE_BADGE[item.basis];if(tag)text.append(badge(tag[0],tag[1]),document.createTextNode(" "));text.append(document.createTextNode(item.text));row.append(text);}
        row.append(refs(item.source_ids));box.append(row);});
      grid.append(box);
    }
    target.append(grid,detail);
    if(record.warnings.length){const box=node("section","work-warnings"),labels=Object.fromEntries(record.guide.sections.map(s=>[s.key,s.label]));
      box.append(node("h3","",`확인이 필요한 문장 ${record.warnings.length}`));
      for(const w of record.warnings){const row=node("div","work-warning");row.append(node("strong","",`${labels[w.section]} ${w.item+1}`),node("span","",w.detail));box.append(row);}
      target.append(box);}
    const answers=node("details","issue-answers");answers.append(node("summary","",`공개 자료 검색 답변 ${record.answers.length}건`));
    for(const a of record.answers){const box=node("div","issue-answer");box.append(node("p","issue-q",`${a.id} · ${a.text}`),node("p","muted small",`답변 상태 ${a.status||a.state} · 인용 ${Object.keys(a.evidence).length}건`),node("p","issue-answer-text",a.answer||"(답변 없음)"));answers.append(box);}
    target.append(answers);
    if(record.review){const r=record.review,box=node("details","issue-review"),covered=r.points.filter(p=>p.covered).length;
      box.append(node("summary","",`정답 대조(평가용) · ${r.label} · ${r.points.length}개 중 ${covered}개 언급`),node("p","issue-review-summary",r.summary));
      const list=node("ul","issue-review-points");
      for(const p of r.points){const li=node("li",p.covered?"is-covered":"is-missed");li.append(node("strong","",(p.covered?"✓ ":"✗ ")+p.text),node("span","muted small",` · 원문 ${p.page}`+(p.where?` · 가이드 '${p.where.section}'에서 "${p.where.match}"`:"")));list.append(li);}
      box.append(list,node("p","muted small",r.method));target.append(box);}
    target.append(node("p","work-foot",`'공개 근거' = 검색된 공개 자료 인용 · '사내 자료' = 내부 문서 · 'AI 제안' = AI가 근거를 바탕으로 낸 제안. 칩을 누르면 원문이 열립니다. · ${record.model} · 검색 ${record.answers.length}회`));
  }

  return Object.freeze({open,summary});
})();
