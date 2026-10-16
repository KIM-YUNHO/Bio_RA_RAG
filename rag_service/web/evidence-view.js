"use strict";
// Inline evidence: citation links, simple math, sanitized tables and table/figure cards.
const EvidenceView=(()=>{
  const SYMBOLS={cdot:"·",times:"×","%":"%",mu:"μ",geq:"≥",leq:"≤",ge:"≥",le:"≤",pm:"±",infty:"∞",alpha:"α",beta:"β",gamma:"γ",lambda:"λ",sim:"~"};
  const TABLE_TAGS=new Set(["TABLE","THEAD","TBODY","TFOOT","TR","TH","TD","CAPTION","BR","SUB","SUP"]);
  const DROPPED=new Set(["SCRIPT","STYLE","TEMPLATE","NOSCRIPT","IFRAME","OBJECT","EMBED","SVG","MATH"]);
  function mathNodes(source){
    let text=source.trim().replace(/\\(?:mathrm|text|mathit|mathbf)\{([^}]*)\}/g,"$1").replace(/\\([a-zA-Z]+|%)/g,(m,w)=>SYMBOLS[w]??w);
    const out=document.createDocumentFragment(),pattern=/([_^])(?:\{([^}]*)\}|(\S))/g;let last=0,match;
    while((match=pattern.exec(text))){out.append(text.slice(last,match.index),node(match[1]==="_"?"sub":"sup","",match[2]??match[3]));last=pattern.lastIndex;}
    out.append(text.slice(last));return out;
  }
  // Plain text with $...$ fragments and literal "\n" markers from extracted cells.
  function richText(text){
    const out=document.createDocumentFragment();
    String(text).split(/\\n(?![a-z])/).forEach((line,index)=>{
      if(index)out.append(node("br"));
      let last=0;const pattern=/\$([^$\n]{1,160})\$/g;let match;
      while((match=pattern.exec(line))){out.append(line.slice(last,match.index),mathNodes(match[1]));last=pattern.lastIndex;}
      out.append(line.slice(last));
    });
    return out;
  }
  function copyAllowed(element){
    const out=document.createDocumentFragment();
    for(const child of element.childNodes){
      if(child.nodeType===Node.TEXT_NODE){out.append(richText(child.textContent));continue;}
      if(child.nodeType!==Node.ELEMENT_NODE)continue;
      if(DROPPED.has(child.tagName.toUpperCase()))continue;
      if(!TABLE_TAGS.has(child.tagName)){out.append(copyAllowed(child));continue;}
      const copy=document.createElement(child.tagName.toLowerCase());
      for(const name of ["rowspan","colspan"]){const value=child.getAttribute(name);if(value&&/^[1-9]\d?$/.test(value))copy.setAttribute(name,value);}
      copy.append(copyAllowed(child));out.append(copy);
    }
    return out;
  }
  // Rebuild only table structure elements; scripts, attributes and other markup never reach the page.
  function cleanTable(html){
    // Drop style attributes before parsing so the page CSP never has to block them.
    const parsed=new DOMParser().parseFromString(String(html).replace(/\sstyle\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi,""),"text/html").querySelector("table");
    if(!parsed)return null;const table=document.createElement("table");table.append(copyAllowed(parsed));return table;
  }
  function splitTableText(text){
    const match=/<table[\s\S]*?<\/table>/i.exec(text||"");
    if(!match)return {before:text||"",table:null,after:""};
    return {before:text.slice(0,match.index).trim(),table:match[0],after:text.slice(match.index+match[0].length).trim()};
  }
  function markCitedCells(table,quotes){
    if(!quotes.length)return 0;let count=0;
    for(const cell of table.querySelectorAll("td,th")){
      const value=cell.textContent.replace(/\s+/g," ").trim();
      if(value&&value.length<=40&&/\d/.test(value)&&quotes.some(q=>q.replace(/\s+/g," ").includes(value))){cell.classList.add("cited");count++;}
    }
    return count;
  }
  function linkify(text,validIds,onCite){
    const out=document.createDocumentFragment(),pattern=/\[((?:E\d+)(?:\s*[,·]\s*E\d+)*)\]/g;let last=0,match;
    while((match=pattern.exec(text))){
      out.append(text.slice(last,match.index));
      const group=node("span","cite-group");group.append("[");
      match[1].split(/\s*[,·]\s*/).forEach((id,index)=>{if(index)group.append(", ");
        if(validIds.has(id)){const b=button(id,"cite",()=>onCite(id));b.title="근거 원문 보기";group.append(b);}else group.append(id);});
      group.append("]");out.append(group);last=pattern.lastIndex;
    }
    out.append(text.slice(last));return out;
  }
  function rich(tag,cls,text){const element=node(tag,cls);element.append(richText(text));return element;}
  function isVisual(evidence){return ["table_candidate","figure_candidate"].includes(evidence?.structure?.kind);}
  function visualCard({jobId,resultIndex,eid,evidence,quotes,onOpen,onPin}){
    const isTable=evidence.structure.kind==="table_candidate",card=node("figure","visual-card");
    const head=node("figcaption","visual-head");
    head.append(node("span","visual-tag",isTable?"표":"그림"),node("strong","",`[${eid}] ${evidence.title} · p.${evidence.pdf_page}`),node("span","status pending","자동 추출 · 미검토"));
    const tabs=node("div","visual-tabs"),extracted=node("div","visual-body"),image=node("div","visual-body");
    const parts=splitTableText(evidence.text),crop=`/api/jobs/${jobId}/evidence/${resultIndex}/${encodeURIComponent(eid)}/crop`;
    if(isTable&&parts.table){
      if(parts.before)extracted.append(rich("p","visual-caption",parts.before));
      const table=cleanTable(parts.table),wrap=node("div","table-scroll");
      if(table){const cited=markCitedCells(table,quotes);wrap.append(table);extracted.append(wrap);
        if(cited)extracted.append(node("p","validation-note","강조한 칸은 답변 인용 문장에 같은 값이 있는 칸입니다."));}
      if(parts.after)extracted.append(rich("p","visual-notes",parts.after));
    }else{
      extracted.append(node("p","validation-note",isTable?"구조화된 표가 없어 추출 텍스트를 보여줍니다.":"자동 추출 텍스트·설명입니다. 원문 이미지와 대조하세요."));
      extracted.append(rich("pre","chunk-text",evidence.text||""));
    }
    const img=node("img","crop-image");img.alt=`${evidence.title} p.${evidence.pdf_page} 원문 영역`;img.loading="lazy";
    img.onerror=()=>img.replaceWith(node("p","validation-note","저장된 영역 좌표가 없어 원문 이미지를 만들 수 없습니다. 원문 보기에서 페이지 전체를 확인하세요."));
    image.append(img);
    const showImage=value=>{extracted.hidden=value;image.hidden=!value;if(value&&!img.src)img.src=crop;
      for(const b of tabs.children)b.setAttribute("aria-pressed",String(b.dataset.image===String(value)));};
    const t1=button(isTable?"추출 표":"추출 텍스트","tab",()=>showImage(false)),t2=button("원문 이미지","tab",()=>showImage(true));
    t1.dataset.image="false";t2.dataset.image="true";tabs.append(t1,t2,button("원문 보기 →","download open-source",()=>onOpen(eid)));
    if(onPin)tabs.append(button("+ 단계 메모","download",onPin));
    card.append(head,tabs,extracted,image);showImage(!isTable);
    return card;
  }
  return Object.freeze({richText,cleanTable,splitTableText,markCitedCells,linkify,isVisual,visualCard});
})();
