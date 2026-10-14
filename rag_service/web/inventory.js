"use strict";
// Scope pane: ingredient -> products -> documents, agencies, common guidelines and a product x agency grid.
const Inventory=(()=>{
  const sel={products:new Set(),agencies:new Set(),documents:new Set(),guidelines:true};
  let data=null,onChange=()=>{},disabled=false;
  const inputs={ingredients:[],products:new Map(),agencies:new Map(),documents:new Map(),guidelines:null};
  const allDocs=()=>data.ingredients.flatMap(i=>i.products.flatMap(p=>p.documents.map(d=>({...d,product_id:p.product_id}))));
  const guidelineDocs=()=>data.guidelines.documents;
  const searchable=()=>data.ingredients.flatMap(i=>i.products.filter(p=>p.searchable));
  const nondefault=()=>$("include-nondefault").checked;
  const docMeta=d=>`${d.pages}쪽 · 검색 ${nondefault()?d.eligible_with_nondefault:d.eligible_chunks}/${d.chunks}청크`;

  function check(label,value,kind,{disabledReason=null,extra=null}={}){
    const row=node("label","scope-check"),input=node("input");input.type="checkbox";input.value=value;input.dataset.kind=kind;
    if(disabledReason){input.disabled=true;input.dataset.locked="true";row.title=disabledReason;row.classList.add("locked");}
    row.append(input,node("span","check-label",label));if(extra)row.append(extra);return {row,input};
  }
  function docRow(doc){
    const {row,input}=check(doc.title,doc.key,"document");row.classList.add("doc-check");
    const meta=node("span","doc-meta",docMeta(doc));meta.dataset.doc=doc.key;row.append(meta);
    input.onchange=()=>{input.checked?sel.documents.add(doc.key):sel.documents.delete(doc.key);sync();};
    inputs.documents.set(doc.key,input);return row;
  }
  function docList(docs){
    const box=node("div","inv-docs");box.hidden=true;
    for(const j of data.jurisdictions){const own=docs.filter(d=>d.jurisdiction===j.id);if(!own.length)continue;
      box.append(node("div","inv-doc-group",`${j.label} · ${own.length}문서`));for(const d of own)box.append(docRow(d));}
    return box;
  }
  function expander(count,box){
    const b=button(`문서 ${count} ▸`,"inv-expand");b.setAttribute("aria-expanded","false");
    b.onclick=()=>{box.hidden=!box.hidden;b.setAttribute("aria-expanded",String(!box.hidden));b.textContent=`문서 ${count} ${box.hidden?"▸":"▾"}`;};return b;
  }
  function productBlock(product){
    const block=node("div","inv-product"),head=node("div","inv-row");
    const label=product.name+(product.product_id!==product.name?` · ${product.product_id}`:"")+(product.other_names.length?` (${product.other_names.join(", ")})`:"");
    const {row,input}=check(label,product.product_id,"product",{disabledReason:product.searchable?null:product.reason});
    head.append(row);block.append(head);
    if(product.searchable){
      input.onchange=()=>{input.checked?sel.products.add(product.product_id):sel.products.delete(product.product_id);sync();};
      inputs.products.set(product.product_id,input);
      const box=docList(product.documents.map(d=>({...d,product_id:product.product_id})));head.append(expander(product.documents.length,box));block.append(box);
    }else block.append(node("p","inv-reason",`${product.reason} · 수집 ${product.collected}문서`));
    return block;
  }
  function grid(){
    const table=node("table","inv-grid"),head=node("tr");head.append(node("th","","제품"));
    for(const j of data.jurisdictions)head.append(node("th","",j.label.replace("Health Canada","HC")));
    table.append(head);
    for(const product of searchable()){
      const tr=node("tr");tr.append(node("th","",product.product_id));
      for(const j of data.jurisdictions){const cell=product.cells[j.id],td=node("td");
        if(cell.indexed){const b=button(`${cell.indexed}/${cell.collected}`,"grid-cell",()=>{sel.products=new Set([product.product_id]);sel.agencies=new Set([j.id]);sel.documents.clear();sync();});
          b.title=`${product.product_id} × ${j.label}: 검색 ${cell.indexed}문서 / 수집 ${cell.collected}문서. 누르면 이 조합만 선택`;td.append(b);}
        else{td.append(node("span","grid-empty","—"));td.title=cell.reason;}
        tr.append(td);}
      table.append(tr);
    }
    const wrap=node("div","scope-group");wrap.append(node("h3","","제품 × 기관 (검색/수집 문서)"),table,node("p","inv-reason","칸을 누르면 그 조합만 선택합니다. 빈칸은 마우스를 올리면 사유가 보입니다."));return wrap;
  }
  function render(target,inventory,change){
    data=inventory;onChange=change;target.replaceChildren();
    for(const ingredient of data.ingredients){
      const box=node("div","scope-group inv-ingredient"),products=ingredient.products.filter(p=>p.searchable);
      const {row,input}=check(ingredient.name,ingredient.name,"ingredient",{extra:node("span","count",`바이오시밀러 ${ingredient.products.length}`)});
      row.classList.add("strong");inputs.ingredients.push({input,products});
      input.onchange=()=>{for(const p of products)input.checked?sel.products.add(p.product_id):sel.products.delete(p.product_id);sync();};
      box.append(node("h3","","성분"),row,node("div","inv-group-title","바이오시밀러"));
      for(const p of ingredient.products)box.append(productBlock(p));
      if(ingredient.reference.length){box.append(node("div","inv-group-title","대조약 자료"));for(const p of ingredient.reference)box.append(productBlock(p));}
      target.append(box);
    }
    const agencies=node("div","scope-group");agencies.append(node("h3","","규제기관"));
    for(const j of data.jurisdictions){const count=allDocs().filter(d=>d.jurisdiction===j.id).length;
      const {row,input}=check(j.label,j.id,"agency",{extra:node("span","count",`${count}문서`)});
      input.onchange=()=>{input.checked?sel.agencies.add(j.id):sel.agencies.delete(j.id);sync();};inputs.agencies.set(j.id,input);agencies.append(row);}
    target.append(agencies);
    if(guidelineDocs().length){
      const box=node("div","scope-group"),counts=data.jurisdictions.map(j=>`${j.label.replace("Health Canada","HC")} ${data.guidelines.by_jurisdiction[j.id]||0}`).join(" · ");
      const {row,input}=check("공통 지침 포함","guidelines","guidelines",{extra:node("span","count",counts)});
      input.checked=true;input.onchange=()=>{sel.guidelines=input.checked;sync();};inputs.guidelines=input;
      const list=docList(guidelineDocs());const head=node("div","inv-row");head.append(row,expander(guidelineDocs().length,list));
      box.append(node("h3","","공통 지침"),head,list);target.append(box);
    }
    target.append(grid());sync(false);
  }
  function scopeDocuments(){
    const guides=guidelineDocs();
    if(sel.documents.size){
      const chosen=[...sel.documents],chosenGuides=guides.filter(d=>sel.documents.has(d.key));
      const productJurisdictions=new Set(allDocs().filter(d=>sel.documents.has(d.key)).map(d=>d.jurisdiction));
      const extra=sel.guidelines&&!chosenGuides.length?guides.filter(d=>productJurisdictions.has(d.jurisdiction)).map(d=>d.key):[];
      return {mode:"documents",keys:[...new Set([...chosen,...extra])]};
    }
    const inScope=d=>(!sel.agencies.size||sel.agencies.has(d.jurisdiction));
    const products=allDocs().filter(d=>(!sel.products.size||sel.products.has(d.product_id))&&inScope(d));
    const guidesIn=guides.filter(inScope);
    if(guides.length&&!sel.guidelines)return {mode:"no-guidelines",keys:products.map(d=>d.key),products:products.length,guides:0};
    return {mode:"filters",keys:null,products:products.length,guides:sel.guidelines?guidesIn.length:0};
  }
  function payload(){
    const scope=scopeDocuments();
    if(scope.mode==="documents")return {products:[],jurisdictions:[],documents:scope.keys};
    const base={products:[...sel.products],jurisdictions:[...sel.agencies]};
    if(scope.mode==="no-guidelines"){if(!scope.keys.length)throw Error("선택한 범위에 검색할 제품 문서가 없습니다.");return {...base,documents:scope.keys};}
    return base;
  }
  function describe(){
    const scope=scopeDocuments();
    if(scope.mode==="documents")return {text:`문서 ${scope.keys.length}개 직접 선택`,note:"문서 선택이 제품·기관 선택보다 우선하며, 질문 해석도 이 문서 범위로 고정됩니다."};
    const products=sel.products.size?[...sel.products].join("·"):"전체 제품",agencies=sel.agencies.size?[...sel.agencies].map(j=>AGENCIES[j]).join("·"):"전체 기관";
    const text=`${products} × ${agencies} · 제품 문서 ${scope.products}개${guidelineDocs().length?(sel.guidelines?` · 지침 ${scope.guides}개`:" · 지침 제외"):""}`;
    return {text,note:scope.mode==="no-guidelines"?"지침을 빼기 위해 제품 문서 목록으로 범위를 고정합니다.":(!sel.products.size&&!sel.agencies.size?"선택하지 않으면 검색 허용 자료 전체를 사용합니다.":"")};
  }
  function sync(notify=true){
    if(!data)return;
    for(const [id,input] of inputs.products)input.checked=sel.products.has(id);
    for(const [id,input] of inputs.agencies)input.checked=sel.agencies.has(id);
    for(const [key,input] of inputs.documents)input.checked=sel.documents.has(key);
    for(const {input,products} of inputs.ingredients){const n=products.filter(p=>sel.products.has(p.product_id)).length;input.checked=!!n&&n===products.length;input.indeterminate=!!n&&n<products.length;}
    if(inputs.guidelines)inputs.guidelines.checked=sel.guidelines;
    const docsMode=sel.documents.size>0;
    for(const input of [...inputs.products.values(),...inputs.agencies.values(),...inputs.ingredients.map(i=>i.input)])input.disabled=disabled||docsMode;
    for(const input of inputs.documents.values())input.disabled=disabled;
    if(inputs.guidelines)inputs.guidelines.disabled=disabled;
    for(const b of document.querySelectorAll(".grid-cell"))b.disabled=disabled;
    const d=describe();$("scope-summary").replaceChildren(node("strong","",d.text));if(d.note)$("scope-summary").append(node("span","",d.note));
    if(docsMode)$("scope-summary").append(button("문서 선택 해제","link-button",()=>{sel.documents.clear();sync();}));
    if(notify)onChange();
  }
  function refreshCounts(){for(const meta of document.querySelectorAll(".doc-meta[data-doc]")){const doc=[...allDocs(),...guidelineDocs()].find(d=>d.key===meta.dataset.doc);if(doc)meta.textContent=docMeta(doc);}}
  function setDisabled(value){disabled=value;sync(false);}
  return Object.freeze({render,payload,describe,refreshCounts,setDisabled,selection:()=>({products:[...sel.products],agencies:[...sel.agencies],documents:[...sel.documents],guidelines:sel.guidelines})});
})();
