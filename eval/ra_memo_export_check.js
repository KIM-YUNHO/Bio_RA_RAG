"use strict";
// Isolated DOM contract check; no browser automation or visual assertion.
const fs=require("node:fs"),vm=require("node:vm"),assert=require("node:assert/strict");
const source=fs.readFileSync("rag_service/static/app.js","utf8");
const helper=source.slice(source.indexOf("function node("),source.indexOf("async function api("));
const memoCode=source.slice(source.indexOf("function renderMemo("),source.indexOf("function renderResearch("));
const clicks=[],blobs=[];
class Element{
  constructor(tag){this.tag=tag;this.children=[];this.textContent="";}
  append(...children){this.children.push(...children);}
  click(){clicks.push({download:this.download,href:this.href});}
}
const sandbox={document:{createElement:tag=>new Element(tag)},Blob,
  URL:{createObjectURL:blob=>{blobs.push(blob);return "blob:test";},revokeObjectURL:()=>{}},
  setTimeout:callback=>callback()};
vm.createContext(sandbox);vm.runInContext(helper+memoCode,sandbox);
function allText(element){return element.textContent+"\n"+element.children.map(allText).join("\n");}
(async()=>{
  const rows=[];
  for(const file of process.argv.slice(2)){
    const report=JSON.parse(fs.readFileSync(file,"utf8")),target=new Element("div");
    sandbox.renderMemo(report,target);
    const text=allText(target);
    assert(text.includes("쟁점별 검토 메모"));
    for(const issue of report.review_memo.issues){
      assert(text.includes(issue.request));
      for(const claim of issue.claims){assert(text.includes(claim.text));
        for(const citation of claim.sources)assert(text.includes(citation.title));}
    }
    for(const question of report.review_memo.meeting_questions)assert(text.includes(question.question_ko));
    const panel=target.children[0],button=panel.children.at(-1);
    assert.equal(button.tag,"button");button.onclick();
    assert.equal(clicks.at(-1).download,"ra-review-memo.md");
    assert.equal(await blobs.at(-1).text(),report.review_memo.markdown);
    rows.push({file,rendered_issues:report.review_memo.issues.length,exact_markdown_export:true});
  }
  process.stdout.write(JSON.stringify({rows,isolated_fake_dom:true,browser_visual_verification:false},null,2));
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
