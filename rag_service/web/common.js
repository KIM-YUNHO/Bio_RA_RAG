"use strict";
const $=id=>document.getElementById(id);
const AGENCIES={EU:"EMA",US:"FDA",CA:"Health Canada",JP:"PMDA",sponsor:"시험 등록·의뢰자"};
const STAGES={queued:"처리 준비",understanding:"질문 해석",retrieving:"근거 검색",answering:"답변·근거 검토",completed:"완료",failed:"실패"};
function node(tag,cls,text){const element=document.createElement(tag);if(cls)element.className=cls;if(text!==undefined)element.textContent=text;return element;}
async function api(path,options={}){const response=await fetch(path,{cache:"no-store",...options});const data=await response.json();if(!response.ok)throw Error(data.error||"요청에 실패했습니다.");return data;}
function sourceLink(url,label){try{const parsed=new URL(url,location.href);if(!["http:","https:"].includes(parsed.protocol))return null;const a=node("a","",label);a.href=parsed.href;a.target="_blank";a.rel="noopener";return a;}catch{return null;}}
function downloadText(text,type,name){const url=URL.createObjectURL(new Blob([text],{type}));const anchor=node("a");anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function button(label,cls="download",onclick){const b=node("button",cls,label);b.type="button";if(onclick)b.onclick=onclick;return b;}
const Prefs={get(key,fallback){try{const v=localStorage.getItem("ra-ui:"+key);return v===null?fallback:JSON.parse(v);}catch{return fallback;}},
  set(key,value){try{localStorage.setItem("ra-ui:"+key,JSON.stringify(value));}catch{}}};
