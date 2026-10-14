"use strict";
// Right pane: a simple view of the cited source page. Detailed review stays in the separate review page.
const Viewer=(()=>{
  let ctx=null,token=0;
  const METHOD={region:"저장된 근거 영역을 강조했습니다.",quote:"인용 문장을 페이지 텍스트에서 찾아 강조했습니다."};
  function show(value){$("viewer-pane").hidden=!value;$("split-right").hidden=!value;$("layout").classList.toggle("viewer-open",value);}
  function evidenceUrl(eid,suffix=""){return `/api/jobs/${ctx.jobId}/evidence/${ctx.resultIndex}/${encodeURIComponent(eid)}${suffix}`;}
  function open({jobId,resultIndex,eid,cited}){ctx={jobId,resultIndex,cited:cited||[]};show(true);load(eid,null);}
  function close(){token++;ctx=null;show(false);$("viewer-body").replaceChildren();}
  async function load(eid,page){
    const mine=++token;ctx.eid=eid;$("viewer-body").replaceChildren(node("p","viewer-loading","원문 페이지를 불러오는 중입니다."));
    try{const detail=await api(evidenceUrl(eid,page?`?page=${page}`:""));if(mine!==token)return;ctx.page=detail.page;render(detail);}
    catch(error){if(mine===token)$("viewer-body").replaceChildren(node("p","error","원문을 열지 못했습니다: "+error.message));}
  }
  function render(d){
    const body=$("viewer-body");body.replaceChildren();
    if(ctx.cited.length>1){const tabs=node("div","viewer-tabs");tabs.setAttribute("aria-label","이 답변의 근거");
      for(const item of ctx.cited){const b=button(item.eid,"tab",()=>load(item.eid,null));b.title=`${item.title} · p.${item.page}`;b.setAttribute("aria-pressed",String(item.eid===d.eid));tabs.append(b);}
      body.append(tabs);}
    const head=node("div","viewer-head");
    head.append(node("strong","",d.title||"제목 없음"),node("div","viewer-meta",[d.source||AGENCIES[d.jurisdiction],d.product_id||(d.product_id===null?"공통 지침":""),d.document_status&&`상태 ${d.document_status}`,d.status_checked&&`확인 ${d.status_checked}`].filter(Boolean).join(" · ")));
    const nav=node("div","viewer-nav"),label=`p.${d.page}${d.pdf_page_label&&String(d.pdf_page_label)!==String(d.page)?` (인쇄 ${d.pdf_page_label})`:""} / ${d.page_count}쪽`;
    const prev=button("◀","icon-button",()=>load(d.eid,d.page-1)),next=button("▶","icon-button",()=>load(d.eid,d.page+1));
    prev.disabled=d.page<=1;next.disabled=d.page>=d.page_count;prev.setAttribute("aria-label","이전 페이지");next.setAttribute("aria-label","다음 페이지");
    nav.append(prev,node("span","page-label",label),next);
    if(d.page!==d.cited_page)nav.append(button(`근거 페이지 p.${d.cited_page}로`,"link-button",()=>load(d.eid,null)));
    head.append(nav);body.append(head);
    const note=d.page===d.cited_page?(METHOD[d.highlight_method]||"강조 위치 없음: 이 근거에서 답변이 인용한 문장이 없거나 페이지에서 찾지 못했습니다."):"근거 페이지가 아닌 앞뒤 페이지입니다.";
    body.append(node("p","viewer-note",note));
    const frame=node("div","page-frame"),img=node("img","page-image");img.alt=`${d.title} p.${d.page}`;img.src=evidenceUrl(d.eid,`/page/${d.page}`);
    frame.append(img);
    for(const [x0,y0,x1,y1] of d.boxes){const box=node("div","page-box");Object.assign(box.style,{left:x0*100+"%",top:y0*100+"%",width:(x1-x0)*100+"%",height:(y1-y0)*100+"%"});frame.append(box);}
    img.onload=()=>{const first=frame.querySelector(".page-box");if(first)first.scrollIntoView({block:"center",behavior:"smooth"});};
    img.onerror=()=>frame.replaceWith(node("p","error","페이지 이미지를 만들지 못했습니다."));
    const scroll=node("div","page-scroll");scroll.append(frame);body.append(scroll);
    const links=node("div","source-links viewer-links"),review=d.review_links[0]&&sourceLink(d.review_links[0].url,"검토 페이지에서 상세 보기 ↗");
    if(review)links.append(review);
    const pdf=sourceLink(evidenceUrl(d.eid,"/document.pdf")+`#page=${d.page}`,"원본 PDF ↗");if(pdf)links.append(pdf);
    const official=d.source_url&&sourceLink(d.source_url,"기관 출처 ↗");if(official)links.append(official);
    body.append(links);
  }
  function init(){$("viewer-close").onclick=close;document.addEventListener("keydown",event=>{if(event.key==="Escape"&&ctx)close();});}
  return Object.freeze({init,open,close,isOpen:()=>!!ctx});
})();
