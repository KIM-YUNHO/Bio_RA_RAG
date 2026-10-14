"use strict";
const RAReviewNotes=(()=>{
  const labels={unreviewed:"미검토",checked:"확인 완료",needs_work:"보완 필요"};
  function key(jobId){if(!/^[0-9a-f]{32}$/.test(jobId))throw Error("질문 ID를 확인하세요.");return "ra-review-notes-v1:"+jobId;}
  function loadHistory(storage,jobId,question){
    const raw=storage.getItem(key(jobId));
    if(raw===null)return {schema_version:1,job_id:jobId,question,revisions:[]};
    const history=JSON.parse(raw);
    if(!history||history.schema_version!==1||history.job_id!==jobId||history.question!==question||!Array.isArray(history.revisions)
      ||history.revisions.some(r=>!r||!Object.hasOwn(labels,r.status)||typeof r.note!=="string"||r.note.length>4000||typeof r.saved_at!=="string"||!Number.isFinite(Date.parse(r.saved_at))))
      throw Error("저장된 검토 이력을 확인할 수 없습니다. 기존 기록은 유지했습니다.");
    return history;
  }
  function appendRevision(storage,jobId,question,status,note,now=new Date().toISOString()){
    if(!Object.hasOwn(labels,status)||typeof note!=="string"||note.length>4000)throw Error("검토 상태와 의견을 확인하세요. 의견은 4,000자까지 저장됩니다.");
    if(typeof now!=="string"||!Number.isFinite(Date.parse(now)))throw Error("저장 시각을 확인하세요.");
    const history=loadHistory(storage,jobId,question),last=history.revisions.at(-1);
    if(last&&last.status===status&&last.note===note)return history;
    const next={...history,revisions:[...history.revisions,{saved_at:now,status,note}]};
    storage.setItem(key(jobId),JSON.stringify(next));return next;
  }
  function historyMarkdown(history){
    const lines=["# RA 검토 의견 이력","",history.question,"","질문 ID: "+history.job_id,"","사용자 검토 의견입니다. 원래 답변·인용과 분리해 기록하며 전문가 인증을 뜻하지 않습니다."];
    history.revisions.forEach((r,i)=>{lines.push("","## 수정 "+(i+1)+" · "+r.saved_at,"","상태: "+labels[r.status],"",r.note);
      for(const q of r.meeting_questions||[])lines.push("","미팅 질문 수정 ["+q.issue_id+"]: "+q.question_ko);});
    return lines.join("\n")+"\n";
  }
  function element(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;}
  async function mountServer(target,{jobId,question,api,meetingQuestions=[],memoMarkdown=""}){
    const box=element("section"),status=element("select"),note=element("textarea"),message=element("p"),save=element("button","검토 의견 저장"),download=element("button","검토 이력 저장"),memoExport=element("button","검토본 메모 저장"),entries=element("div"),details=element("details"),actions=element("div");
    box.className="review-notes";message.className="validation-note";message.setAttribute("role","status");
    box.append(element("h3","내 검토 의견"),element("p","서버에 질문별로 저장됩니다. 원래 답변·인용은 보존하며 의견과 미팅 질문 수정만 별도 기록합니다."));
    status.id="review-status-"+jobId;note.id="review-note-"+jobId;note.maxLength=4000;note.rows=3;
    const sl=element("label","검토 상태"),nl=element("label","검토 의견");sl.htmlFor=status.id;nl.htmlFor=note.id;
    for(const [value,label] of Object.entries(labels)){const option=element("option",label);option.value=value;status.append(option);}status.value="unreviewed";
    box.append(sl,status,nl,note);const questionInputs=[];
    for(const q of meetingQuestions){const label=element("label","미팅 질문 초안 수정 · "+q.issue_id),input=element("textarea");
      input.id="meeting-question-"+jobId+"-"+q.issue_id;input.maxLength=3000;input.value=q.question_ko;label.htmlFor=input.id;box.append(label,input);questionInputs.push({issue_id:q.issue_id,input});}
    for(const b of [save,download,memoExport]){b.type="button";b.className="download";b.disabled=true;}actions.className="review-actions";actions.append(save,download,memoExport);
    details.append(element("summary","저장된 수정 이력"),entries);box.append(actions,message,details);target.append(box);
    let history;
    function refresh(){entries.replaceChildren();
      for(const [i,r] of history.revisions.entries()){const entry=element("div");entry.append(element("p",`수정 ${i+1} · ${new Date(r.saved_at).toLocaleString("ko-KR")} · ${labels[r.status]}`),element("pre",r.note));
        for(const q of r.meeting_questions)entry.append(element("p","미팅 질문 ["+q.issue_id+"]: "+q.question_ko));entries.append(entry);}
      const last=history.revisions.at(-1);if(last){status.value=last.status;note.value=last.note;
        for(const q of questionInputs){const saved=last.meeting_questions.find(item=>item.issue_id===q.issue_id);if(saved)q.input.value=saved.question_ko;}}
      save.disabled=false;download.disabled=!history.revisions.length;memoExport.disabled=false;
    }
    try{history=await api("/api/jobs/"+encodeURIComponent(jobId)+"/review");refresh();}
    catch(error){message.textContent="검토 이력 읽기 실패: "+error.message;return;}
    save.onclick=async()=>{save.disabled=true;try{
      history=await api("/api/jobs/"+encodeURIComponent(jobId)+"/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_revision:history.revisions.length,status:status.value,note:note.value,
        meeting_questions:questionInputs.map(q=>({issue_id:q.issue_id,question_ko:q.input.value}))})});refresh();message.textContent=`서버에 저장했습니다. 수정 이력 ${history.revisions.length}건`;
    }catch(error){message.textContent="저장하지 못했습니다. 입력한 내용은 화면에 남아 있습니다: "+error.message;}finally{save.disabled=false;}};
    function exportText(text,name){const url=URL.createObjectURL(new Blob([text],{type:"text/markdown;charset=utf-8"})),anchor=element("a");anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    download.onclick=()=>exportText(historyMarkdown(history),"ra-review-notes-"+jobId+".md");
    memoExport.onclick=()=>exportText(memoMarkdown+"\n---\n\n"+historyMarkdown(history),"ra-reviewed-memo-"+jobId+".md");
    try{const local=loadHistory(window.localStorage,jobId,question);if(local.revisions.length){const importButton=element("button","이 브라우저 기록 가져오기");importButton.type="button";importButton.className="download";actions.append(importButton);
      importButton.onclick=async()=>{importButton.disabled=save.disabled=true;try{for(const r of local.revisions){history=await api("/api/jobs/"+encodeURIComponent(jobId)+"/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({expected_revision:history.revisions.length,status:r.status,note:r.note,meeting_questions:[]})});}refresh();message.textContent="브라우저 의견을 서버에 복사했습니다. 기존 브라우저 기록도 보존했습니다.";}
        catch(error){message.textContent="가져오기를 중단했습니다. 이미 저장된 기록과 브라우저 원본은 보존했습니다: "+error.message;}finally{save.disabled=false;}};}}
    catch(error){message.textContent="서버 기록은 사용 가능하지만 브라우저 기록을 확인하지 못했습니다: "+error.message;}
  }
  function mount(target,options){
    if(typeof options.api==="function")return mountServer(target,options);
    const {jobId,question}=options;
    const box=element("section"),heading=element("h3","내 검토 의견"),info=element("p","이 브라우저에 저장됩니다. 다른 기기로 옮길 때는 검토 이력을 내려받으세요."),status=element("select"),note=element("textarea"),save=element("button","검토 의견 저장"),download=element("button","검토 이력 저장"),message=element("p"),historyView=element("details"),entries=element("div"),actions=element("div");
    box.className="review-notes";info.className="memo-source";actions.className="review-actions";message.className="validation-note";
    status.id="review-status-"+jobId;note.id="review-note-"+jobId;note.rows=3;note.maxLength=4000;note.placeholder="확인한 근거, 수정할 표현, 추가 조사할 항목을 기록하세요.";
    const statusLabel=element("label","검토 상태"),noteLabel=element("label","검토 의견");statusLabel.htmlFor=status.id;noteLabel.htmlFor=note.id;
    for(const [value,label] of Object.entries(labels)){const option=element("option",label);option.value=value;status.append(option);}status.value="unreviewed";
    save.type=download.type="button";save.className=download.className="download";message.setAttribute("role","status");
    historyView.append(element("summary","저장된 수정 이력"),entries);actions.append(save,download);
    box.append(heading,info,statusLabel,status,noteLabel,note,actions,message,historyView);target.append(box);
    let history;
    function refresh(){
      entries.replaceChildren();
      history.revisions.forEach((r,i)=>{const entry=element("div");entry.append(element("p",`수정 ${i+1} · ${new Date(r.saved_at).toLocaleString("ko-KR")} · ${labels[r.status]}`),element("pre",r.note));entries.append(entry);});
      const last=history.revisions.at(-1);if(last){status.value=last.status;note.value=last.note;}
      download.disabled=!history.revisions.length;
    }
    try{history=loadHistory(window.localStorage,jobId,question);refresh();}
    catch(error){save.disabled=download.disabled=true;message.textContent="검토 이력 읽기 실패: "+error.message;return;}
    save.onclick=()=>{try{history=appendRevision(window.localStorage,jobId,question,status.value,note.value);refresh();message.textContent=`검토 의견을 저장했습니다. 수정 이력 ${history.revisions.length}건`;}
      catch(error){message.textContent="저장하지 못했습니다. 입력한 의견은 화면에 남아 있습니다: "+error.message;}};
    download.onclick=()=>{try{const url=URL.createObjectURL(new Blob([historyMarkdown(history)],{type:"text/markdown;charset=utf-8"})),anchor=element("a");anchor.href=url;anchor.download="ra-review-notes-"+jobId+".md";anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
      catch(error){message.textContent="내려받기에 실패했습니다: "+error.message;}};
  }
  return Object.freeze({mount,loadHistory,appendRevision,historyMarkdown});
})();
