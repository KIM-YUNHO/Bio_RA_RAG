"""Create a new native snapshot; preserve the old catalog, raw files and index."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import requests
import fitz
from rag.ra_sources import build, rows

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT/'data/guidelines/ra_guidelines_native_2026-10-11_v1'
NEW = ROOT/'data/guidelines/ra_guidelines_native_2026-10-12_v2'
OUT = ROOT/'data/runs/ra_guideline_refresh_2026-10-12_v1'
EMA = 'https://www.ema.europa.eu/en/scientific-guidelines/'
FDA = 'https://www.fda.gov/regulatory-information/search-fda-guidance-documents/'
HC = 'https://www.canada.ca/en/health-canada/services/drugs-health-products/biologics-radiopharmaceuticals-genetic-therapies/applications-submissions/guidance-documents/information-submission-requirements-biosimilar-biologic-drugs'
STATUS = {
 'GL:EMA-SBMP-REV1': ('current_effective', EMA+'similar-biological-medicinal-products', '2015-04-30', '2026 개정 concept paper는 의견 수렴 중이며 아직 대체본이 아님.'),
 'GL:EMA-SBMP-NONCLIN-CLIN-REV1': ('current_effective_conditional', EMA+'similar-biological-medicinal-products-containing-biotechnology-derived-proteins-active-substance-non-clinical-clinical-issues', '2015-07-01', '공식 페이지는 tailored CES 접근이 불가능한 경우에만 기존 지침을 전면 적용하도록 명시. 2026 reflection paper 및 scientific advice와 함께 확인.'),
 'GL:EMA-SBMP-QUALITY-REV1': ('current_effective', EMA+'similar-biological-medicinal-products-containing-biotechnology-derived-proteins-active-substance-quality-issues', '2014-12-01', ''),
 'GL:EMA-SBMP-MAB': ('current_effective', EMA+'similar-biological-medicinal-products-containing-monoclonal-antibodies-non-clinical-clinical-issues', '2012-12-01', '2026 tailored clinical approach와 함께 적용 범위를 검토.'),
 'GL:EMA-TAILORED-CLINICAL-RP': ('adopted', EMA+'reflection-paper-tailored-clinical-approach-biosimilar-development', '2026-03-27', '저장된 2025 초안과 Adopted 메타데이터의 불일치 확인. 현재 등록 페이지의 최종본 PDF 링크(.pdf-0)로 교체.'),
 'GL:EMA-HMA-INTERCHANGEABILITY-2022': ('published_statement', 'https://www.ema.europa.eu/en/human-regulatory-overview/biosimilar-medicines-overview', '2023-04-26', '과학적 상호교환성 입장. 처방·자동대체 실행은 회원국 관할.'),
 'GL:FDA-CES-UPDATED-2025': ('draft', FDA+'scientific-considerations-demonstrating-biosimilarity-reference-product-updated-recommendations', '2025-10', 'Draft, not for implementation. 시행 중인 최종 지침으로 표현하지 않음.'),
 'GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015': ('withdrawn', 'https://www.fda.gov/news-events/press-announcements/fda-takes-further-steps-streamline-biosimilar-development-and-make-medicines-more-affordable', '2026-03-09', 'FDA가 철회 공지. 현재 지침 검색 및 모델 입력에서 제외; 원본과 과거 답변 보존.'),
 'GL:FDA-CLINPHARM-2016': ('final', FDA+'clinical-pharmacology-data-support-demonstration-biosimilarity-reference-product', '2016-12', '2026 Rev4 draft의 PK 브리징 제안과 최종 지침을 구분.'),
 'GL:FDA-QUALITY-CAA': ('final', FDA+'development-therapeutic-protein-biosimilars-comparative-analytical-assessment-and-other-quality', '2025-09', '2019 초안과 2015 Quality Considerations를 대체.'),
 'GL:FDA-QA-REV4': ('draft', FDA+'new-and-revised-draft-qas-biosimilar-development-and-bpci-act-revision-3', '2026-03', 'URL slug는 revision-3이나 공식 제목·본문은 Revision 4. Draft, not for implementation.'),
 'GL:FDA-INTERCHANGEABILITY-UPDATE-2024': ('draft', FDA+'considerations-demonstrating-interchangeability-reference-product-update-0', '2024-06', 'Draft, not for implementation. 2019 최종본을 이미 대체한 것으로 판단하지 않음.'),
 'GL:FDA-INTERCHANGEABILITY-2019': ('final', FDA+'considerations-demonstrating-interchangeability-reference-product-guidance-industry', '2019-05', '2024 개정 제안은 별도의 초안.'),
}

def main():
    if OUT.exists() or NEW.exists(): raise ValueError('Do not overwrite or automatically re-download')
    old_hashes = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [OLD/'manifest.json',OLD/'chunks.jsonl']}
    manifest = json.loads((OLD/'manifest.json').read_text(encoding='utf-8'))
    ids = {s['document_id'] for s in manifest['sources']}
    docs = [deepcopy(d) for d in rows(ROOT/'data/catalog/documents.jsonl') if d['document_id'] in ids]
    assert len(docs)==21 and {d['document_id'] for d in docs}==ids
    OUT.mkdir(parents=True)
    replacement = next(d for d in docs if d['document_id']=='GL:EMA-TAILORED-CLINICAL-RP')
    old_version = replacement['version_id']
    url = 'https://www.ema.europa.eu/en/documents/other/reflection-paper-tailored-clinical-approach-biosimilar-development_en.pdf-0'
    response = requests.get(url,timeout=60); response.raise_for_status()
    payload = response.content
    assert payload.startswith(b'%PDF')
    digest = hashlib.sha256(payload).hexdigest()
    raw = OUT/(digest+'.pdf'); raw.write_bytes(payload)
    with fitz.open(raw) as pdf:
        assert len(pdf)==15
        text = pdf[0].get_text()
        assert '16 March 2026' in text and 'Adopted by CHMP' in text
        parsed = OUT/(digest+'.pages.jsonl')
        pages = [{'document_id':replacement['document_id'],'version_id':digest,'pdf_page':i+1,
            'text':p.get_text(sort=True),'text_layer':{'needs_ocr':False},'parser':'pymupdf_native',
            'parser_version':fitz.VersionBind} for i,p in enumerate(pdf)]
    parsed.write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in pages),encoding='utf-8')
    replacement.update(version_id=digest,sha256=digest,raw_path=str(raw.relative_to(ROOT)),
        parsed_path=str(parsed.relative_to(ROOT)),page_count=15,url=url,final_url=response.url,
        retrieved_at=datetime.now(timezone.utc).isoformat())
    replacement['source_metadata']={**replacement.get('source_metadata',{}),'status':'Adopted',
        'first_published':'27/03/2026','checked':'2026-10-12'}
    catalog=OUT/'catalog.jsonl'
    catalog.write_text(''.join(json.dumps(d,ensure_ascii=False)+'\n' for d in docs),encoding='utf-8')
    new_manifest=build(catalog,NEW)
    status_rows=[]
    for doc in docs:
        identity=doc['document_id']
        if identity.startswith('GL:HC-BIOSIMILAR-2026'):
            suffix=identity.partition(':2026')[2].lstrip(':') if ':2026' in identity else ''
            # Actual section URLs are preserved in the catalog, rather than invented.
            info=('final_guidance_family', doc.get('final_url') or doc['url'], '2026-05-19',
                  '2026 최종 지침의 목차 및 변경 요약 확인. 개별 절은 동일 지침의 일부이며 별도 독립 지침 8개가 아님.')
        else: info=STATUS[identity]
        state,official,date,note=info
        status_rows.append({'document_id':identity,'version_id':doc['version_id'],'title':doc['title'],
            'source':doc['source'],'registered_status':state,'official_url':official,'status_date':date,
            'checked_on':'2026-10-12','status_check_scope':'official_register_or_guidance_family',
            'live_binary_sha_matched':identity=='GL:EMA-TAILORED-CLINICAL-RP',
            'model_execution_allowed':state!='withdrawn','note_ko':note})
    status={'schema_version':1,'checked_on':'2026-10-12','current_validity_certified':False,
        'rows':status_rows,'replacement':{'document_id':replacement['document_id'],
        'old_version_id':old_version,'new_version_id':digest,'url':url}}
    target=ROOT/'docs/rag/GUIDELINE_STATUS_2026-10-12.json'
    target.write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 지침 상태 검토 · 2026-10-12','',
      '공식 등록 상태를 확인한 AI 검토 기록이다. 전체 원문의 최신 바이트까지 재검증한 것은 아니며, 제품 허가정보의 최신성 인증도 아니다. EMA reflection paper만 현재 최종본을 새로 다운로드하여 해시·15쪽 원문을 확인했다. 기존 스냅샷은 보존했다.','',
      '| 문서 ID | 공식 상태 / 기준 날짜 | 조치·범위 | 공식 근거 |','|---|---|---|---|']
    for row in status_rows:
        lines.append(f"| {row['document_id']} | {row['registered_status']} / {row['status_date']} | {row['note_ko'] or '등록 상태 확인; 기존 저장 원문 해시 유지.'} | [공식 페이지]({row['official_url']}) |")
    (target.with_suffix('.md')).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    assert all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in old_hashes.items())
    (OUT/'verification.json').write_text(json.dumps({'old_snapshot_unchanged':True,'old_sha256':old_hashes,
        'new_snapshot':str(NEW),'manifest':new_manifest,'replacement':status['replacement'],
        'new_provider_calls':0},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'new_snapshot':str(NEW),'documents':len(docs),'chunks':new_manifest['chunks'],
        'withdrawn_documents':1,'replacement_version':digest},ensure_ascii=False))

if __name__=='__main__': main()
