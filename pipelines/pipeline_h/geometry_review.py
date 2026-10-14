"""Read-only gallery of pages rejected by the final geometry audit."""
import argparse,base64,html,json
from pathlib import Path
from pipelines.pipeline_h.prepare import read,write
from preprocess.ingest import sha256

def build(run):
    run=Path(run).resolve();out=run/'geometry_review';out.mkdir(exist_ok=True)
    docs={d['document_id']:d for d in read(run/'intake_manifest.json')['documents']}
    pages=[json.loads(line) for line in (run/'extraction/extraction_pages.jsonl').read_text(encoding='utf-8').splitlines()]
    failed=[p for p in pages if p['status']!='parsed'];data=[]
    for p in failed:
        d=docs[p['document_id']];layout=read(p['layout_result_path']);w,h=layout['size']
        items=[];overlays=[];outside=0
        for i,e in enumerate(p['invalid_blocks'],1):
            b=e.get('block',{});box=b.get('bbox');item={'number':i,'reason':e['reason'],
                'text':b.get('text',''),'raw_bbox_bottom_left':box,'ref':b.get('ref')}
            if box:
                l,bottom,r,top=box;t=h-top;bt=h-bottom
                item['display_bbox_top_left']=[l,t,r,bt]
                if r>0 and l<w and max(t,bt)>=0 and min(t,bt)<=h:
                    y=max(0,min(h,(t+bt)/2));x=max(0,min(w,(l+r)/2))
                    overlays.append(f'<line x1="{l}" y1="{y}" x2="{r}" y2="{y}" stroke="#ef4444" stroke-width="2"/>')
                    overlays.append(f'<circle cx="{x}" cy="{y}" r="8" stroke="#ef4444" stroke-width="1.8" fill="none"/>')
                    overlays.append(f'<text x="{min(w-25,x+10)}" y="{max(14,y-5)}" font-size="12" fill="#dc2626" font-weight="700">{i}</text>')
                    item['visibility']='on_page_zero_height_or_invalid_region'
                else:
                    outside+=1;item['visibility']='outside_page_no_on_page_location'
            items.append(item)
        source=d.get('rendered_path') or d['raw_path']
        image=Path(p['image_path']);uri='data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode('ascii')
        data.append({'document_id':p['document_id'],'product':d['metadata']['product_id'],'title':d['metadata']['title'],
            'page':p['pdf_page'],'kind':d['kind'],'source_path':source,'source_url':Path(source).as_uri()+f"#page={p['pdf_page']}",
            'original_url':Path(d['raw_path']).as_uri(),'original_path':d['raw_path'],
            'page_label':p.get('pdf_page_label'),'image':uri,'image_sha256':sha256(image),
            'width':w,'height':h,'overlay':''.join(overlays),'outside':outside,'errors':items})
    clean=[{k:v for k,v in d.items() if k not in ('image','overlay')} for d in data]
    write(out/'review_manifest.json',{'source_run':str(run),'pages':len(data),'mode':'read-only review; extraction artifacts unchanged','items':clean})
    payload=json.dumps(data,ensure_ascii=False).replace('<','\\u003c')
    title='최종 좌표 검증 오류 · 19쪽 원문 검토'
    template='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f8;color:#172437;font:15px/1.65 "Malgun Gothic",sans-serif}
header{padding:22px 28px;background:#132b45;color:white}h1{font-size:24px;margin:0 0 6px}header p{margin:0;color:#d6e3ef}
.layout{display:grid;grid-template-columns:310px minmax(0,1fr);gap:18px;padding:20px;max-width:1750px;margin:auto}
nav{align-self:start;position:sticky;top:16px;max-height:94vh;overflow:auto;background:white;padding:12px;border-radius:10px}
nav button{display:block;width:100%;text-align:left;border:1px solid #e2e8ef;border-radius:7px;background:white;color:#172437;margin:7px 0;padding:10px;cursor:pointer;font:inherit}
nav button.active{border-color:#2463c3;background:#eaf2ff}nav small{display:block;color:#64748b}main{min-width:0}.card{background:white;border-radius:10px;padding:22px;margin-bottom:18px}
h2{font-size:20px;margin:0 0 10px}.badge{display:inline-block;padding:3px 9px;background:#eaf2ff;border-radius:6px;font-size:13px;margin-right:6px}
.note{border-left:4px solid #e59f23;background:#fff7e5;padding:12px;margin:14px 0}.tools{display:flex;gap:16px;flex-wrap:wrap;margin-top:10px}a{color:#235bb3}
.sheet{position:relative;border:1px solid #ccd4de;background:white}.sheet img{display:block;width:100%;height:auto}.sheet svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.original{display:none}.pair{display:grid;grid-template-columns:1fr;gap:16px}.pair.compare{grid-template-columns:1fr 1fr}.pair.compare .original{display:block}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #dbe1e9;padding:10px;vertical-align:top;text-align:left}th{background:#f1f5f9}td:nth-child(2){word-break:break-word;white-space:pre-wrap}
.coords{font:12px/1.6 Consolas,monospace;color:#64748b;word-break:break-all}.details{max-height:600px;overflow:auto}.muted{color:#64748b}label{cursor:pointer}
@media(max-width:1000px){.layout{grid-template-columns:1fr}nav{position:static;max-height:290px}.pair.compare{grid-template-columns:1fr}}@media print{nav,.tools{display:none}.layout{display:block}.sheet{max-width:600px}}
</style><header><h1>__TITLE__</h1><p>92문서 중 최종 검증에서 걸린 19쪽 · 페이지 전체 누락을 뜻하지 않습니다 · 추출 결과를 변경하지 않는 검토 화면</p></header>
<div class="layout"><nav id="nav"></nav><main><section class="card"><h2 id="title"></h2><div id="meta"></div><div id="notice" class="note"></div>
<div class="tools"><a id="source" target="_blank">원문 PDF 열기</a><a id="original" target="_blank">저장된 원본 열기</a><label><input id="overlay" type="checkbox" checked> 오류 좌표 표시</label><label><input id="compare" type="checkbox"> 원문·표시본 나란히</label></div>
<p class="muted" id="path"></p><p class="muted">빨간 선·원은 추출기가 기록한 오류 좌표입니다. 실제 문자 영역을 확정하거나 수정한 표시가 아닙니다.</p>
<div class="pair" id="pair"><div class="original"><p>원문 페이지</p><div class="sheet"><img id="plain" alt="원문 페이지"></div></div><div><p>오류 좌표 표시본</p><div class="sheet"><img id="page" alt="오류가 검출된 원문 페이지"><svg id="svg"></svg></div></div></div></section>
<section class="card"><h2>해당 페이지에서 걸린 항목</h2><div class="details"><table><thead><tr><th>번호</th><th>추출 문자·문구</th><th>오류와 원시 좌표</th></tr></thead><tbody id="errors"></tbody></table></div></section></main></div>
<script>const data=__DATA__;const $=id=>document.getElementById(id);let selected=0;
function show(index){selected=index;const d=data[index];$('title').textContent=d.title;$('meta').textContent=`${d.product} · ${d.document_id} · ${d.kind==='html'?'HTML 파생 PDF':'PDF'} ${d.page}쪽 · 오류 ${d.errors.length}항목`;
$('notice').textContent=d.outside?`페이지 밖에 기록된 오류 ${d.outside}항목이 있습니다. 해당 좌표는 이 페이지 이미지 안에 표시할 수 없어 아래 목록에서 확인해야 합니다. HTML 원본과 파생 PDF를 함께 확인하세요.`:'빨간 표시 위치에서 일부 문자의 영역 높이가 0 등으로 기록됐습니다. 페이지의 다른 본문·그림은 추출되어 있으며, 이 화면은 좌표 오류를 확인하기 위한 것입니다.';
$('source').href=location.protocol.startsWith('http')?`/source/${index}/pdf#page=${d.page}`:d.source_url;$('original').href=location.protocol.startsWith('http')?`/source/${index}/original`:d.original_url;$('path').textContent=d.source_path;$('page').src=d.image;$('plain').src=d.image;
$('svg').setAttribute('viewBox',`0 0 ${d.width} ${d.height}`);$('svg').innerHTML=d.overlay;$('svg').style.display=$('overlay').checked?'block':'none';$('errors').replaceChildren();
d.errors.forEach(e=>{const tr=document.createElement('tr');const n=document.createElement('td');n.textContent=e.number;const t=document.createElement('td');t.textContent=e.text||'(빈 문자)';const v=document.createElement('td');v.textContent=e.visibility==='outside_page_no_on_page_location'?'페이지 밖 좌표':'높이 0 등 잘못된 문자 좌표';const c=document.createElement('div');c.className='coords';c.textContent=`${e.reason}\nref: ${e.ref}\n원시 bottom-left: ${JSON.stringify(e.raw_bbox_bottom_left)}\n화면 top-left: ${JSON.stringify(e.display_bbox_top_left)}`;v.append(c);tr.append(n,t,v);$('errors').append(tr);});
document.querySelectorAll('nav button').forEach((b,i)=>b.classList.toggle('active',i===index));}
data.forEach((d,i)=>{const b=document.createElement('button');b.textContent=`${i+1}. ${d.product} · ${d.kind==='html'?'파생 PDF':'PDF'} ${d.page}쪽`;const s=document.createElement('small');s.textContent=`${d.title} · ${d.errors.length}항목`;b.append(s);b.onclick=()=>show(i);$('nav').append(b);});
$('overlay').onchange=()=>{$('svg').style.display=$('overlay').checked?'block':'none';};$('compare').onchange=()=>{$('pair').classList.toggle('compare',$('compare').checked);};show(0);
</script></html>'''
    target=out/'index.html';target.write_text(template.replace('__TITLE__',html.escape(title)).replace('__DATA__',payload),encoding='utf-8')
    print(json.dumps({'review':str(target),'pages':len(data),'error_items':sum(len(d['errors']) for d in data),'bytes':target.stat().st_size},ensure_ascii=False))
    return target

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();build(a.run)
