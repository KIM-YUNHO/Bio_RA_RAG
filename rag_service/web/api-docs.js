"use strict";
// API reference of the service: reads /openapi.json (generated from rag_service/app.py) and lists the routes by area.
(async()=>{
  const root=document.getElementById("api-docs"),el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;};
  try{
    const spec=await(await fetch("/openapi.json")).json(),areas=new Map();
    for(const [path,methods] of Object.entries(spec.paths))for(const [method,op] of Object.entries(methods)){
      const area=path.split("/")[2]||"service";if(!areas.has(area))areas.set(area,[]);areas.get(area).push({method:method.toUpperCase(),path,summary:op.summary||""});
    }
    document.getElementById("api-title").textContent=spec.info.title;document.getElementById("api-desc").textContent=spec.info.description||"";
    for(const [area,rows] of areas){
      const section=el("section","api-area");section.append(el("h2","",`/api/${area}`));
      for(const row of rows){const line=el("div","api-row");line.append(el("span","api-method "+row.method.toLowerCase(),row.method),el("code","",row.path),el("span","api-summary",row.summary));section.append(line);}
      root.append(section);
    }
    root.append(el("p","api-note",`경로 ${Object.keys(spec.paths).length}개 · 원본 명세 /openapi.json · 이 서비스는 이 PC(127.0.0.1)에서만 응답합니다.`));
  }catch(error){root.textContent="API 명세를 불러오지 못했습니다: "+error.message;}
})();
