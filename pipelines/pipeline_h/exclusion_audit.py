"""List skipped source blocks and check literal coverage in existing chunks."""
import argparse,json,re,unicodedata
from collections import defaultdict,Counter
from html.parser import HTMLParser
from pathlib import Path
from pipelines.pipeline_h.prepare import read,write

def normalized(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text or '')).casefold()

class PlainHTML(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
    def handle_data(self,data):self.parts.append(data)

def audit(run):
    run=Path(run).resolve();folder=run/'geometry_review';folder.mkdir(exist_ok=True)
    docs={d['document_id']:d for d in read(run/'intake_manifest.json')['documents']}
    pages=[json.loads(x) for x in (run/'extraction/extraction_pages.jsonl').read_text(encoding='utf-8').splitlines()]
    chunks=[json.loads(x) for x in (run/'candidate/chunks.jsonl').read_text(encoding='utf-8').splitlines()]
    joined=defaultdict(list)
    for c in chunks:joined[c['document_id']].append(c['text'])
    corpus={k:normalized('\n'.join(v)) for k,v in joined.items()};html_source={};groups=[]
    for p in pages:
        if p['status']=='parsed':continue
        doc=docs[p['document_id']];items=[]
        if doc['kind']=='html' and doc['document_id'] not in html_source:
            parser=PlainHTML();parser.feed(Path(doc['raw_path']).read_text(encoding='utf-8-sig'))
            html_source[doc['document_id']]=normalized(''.join(parser.parts))
        for e in p['invalid_blocks']:
            b=e['block'];text=b.get('text','');needle=normalized(text)
            items.append({'ref':b.get('ref'),'text':text,'raw_bbox_bottom_left':b.get('bbox'),
                'section_path':b.get('section_path'),'reason':e['reason'],'skipped_by_export':True,
                'raw_result_preserved':True,'long_text':len(needle)>=20,
                'same_document_chunk_exact_normalized_match':bool(needle and needle in corpus[doc['document_id']]),
                'original_html_exact_normalized_match':bool(needle and needle in html_source[doc['document_id']]) if doc['kind']=='html' else None})
        long_missing=[e for e in items if e['long_text'] and not e['same_document_chunk_exact_normalized_match']]
        groups.append({'document_id':doc['document_id'],'product':doc['metadata']['product_id'],'title':doc['metadata']['title'],
            'kind':doc['kind'],'pdf_page':p['pdf_page'],'original_path':doc['raw_path'],'raw_result_path':p['raw_result_path'],
            'excluded_blocks':len(items),'long_text_missing_exact_match':len(long_missing),
            'long_text_missing_characters':sum(len(e['text']) for e in long_missing),'items':items})
    summary={'mode':'read-only coverage audit; no extraction/chunk/index change',
        'normalization':'NFKC + remove whitespace + casefold; source text unchanged',
        'limitation':'Exact phrase matching is not semantic coverage; overlap, glyph differences and boundaries can affect matches. A phrase found elsewhere does not prove complete coverage of its paragraph.',
        'pages':len(groups),'excluded_blocks':sum(g['excluded_blocks'] for g in groups),
        'hc_html_excluded_blocks':sum(g['excluded_blocks'] for g in groups if g['kind']=='html'),
        'hc_long_text_missing_exact_match':sum(g['long_text_missing_exact_match'] for g in groups if g['kind']=='html'),
        'hc_long_text_missing_characters':sum(g['long_text_missing_characters'] for g in groups if g['kind']=='html'),
        'groups':groups}
    write(folder/'excluded_content_audit.json',summary)
    lines=['# 좌표 오류로 제외된 내용 전체 목록','',
        '원본 문서와 Docling 원시 추출 결과는 삭제되지 않았습니다. 아래 항목은 잘못된 영역 좌표로 인해 현재 검색용 asset 내보내기에서 제외된 내용입니다.','',
        '청크 대조는 해당 문서의 chunk.text를 대상으로 NFKC·공백 제거·대소문자 통일 후 수행했습니다. 문구 미확인은 의미 수준의 완전 누락 확정과 다릅니다. 줄 경계·중복·다른 문자 표기로 인한 차이도 가능합니다.','',
        f"오류 페이지 {len(groups)}쪽, 제외 블록 {summary['excluded_blocks']}개. HC HTML 3문서의 제외 블록 {summary['hc_html_excluded_blocks']}개 중, 20자 이상의 문구 {summary['hc_long_text_missing_exact_match']}개가 같은 문서 청크에서 그대로 확인되지 않았습니다.",'',
        '| 제품 | 문서 | 파생 PDF 쪽 | 제외 블록 | 긴 문구 청크 미확인 |',
        '| :--- | :--- | ---: | ---: | ---: |']
    for g in groups:
        if g['kind']=='html':lines.append(f"| {g['product']} | {g['title']} | {g['pdf_page']} | {g['excluded_blocks']} | {g['long_text_missing_exact_match']} |")
    for g in groups:
        lines+=['',f"## {g['product']} · {g['title']} · {'파생 PDF' if g['kind']=='html' else 'PDF'} {g['pdf_page']}쪽",'',
            f"문서 ID: `{g['document_id']}`",'',f"원본: [{Path(g['original_path']).name}]({Path(g['original_path']).as_posix()})",'']
        for number,e in enumerate(g['items'],1):
            found='같은 문서 청크에서 문구 확인' if e['same_document_chunk_exact_normalized_match'] else '같은 문서 청크에서 동일 문구 미확인'
            source=(' · 원본 HTML 문구 확인' if e['original_html_exact_normalized_match'] else ' · 원본 HTML 문자 표기 차이 등으로 정확 일치 미확인') if g['kind']=='html' else ''
            lines += [f"### {number}. {e['ref']} — {found}{source}",'',f"원시 bottom-left 좌표: `{e['raw_bbox_bottom_left']}`",'',
                '```text',e['text'],'```','']
    (folder/'EXCLUDED_CONTENT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='groups'},ensure_ascii=False))
    for g in groups:
        if g['kind']=='html':print(json.dumps({'document_id':g['document_id'],'excluded':g['excluded_blocks'],
            'original_html_exact_matches':sum(e['original_html_exact_normalized_match'] for e in g['items']),
            'long_missing':g['long_text_missing_exact_match']},ensure_ascii=False))
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();audit(a.run)
