"""Local BGE build, original-metadata audit and isolated release validation."""
import argparse,json,time
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
from pipelines.pipeline_h.prepare import ROOT,read,write
from preprocess.ingest import sha256,INTAKE_METADATA_FIELDS
from preprocess.hybrid import read_jsonl,save_rows

PREVIOUS=ROOT/'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2/candidate'

def embed(run):
    from rag.incremental_embeddings import build
    from pipelines.pipeline_h.chunks import correct_provenance
    correct_provenance(run/'candidate')
    intake=read(run/'intake_manifest.json')
    source=Path(intake['parent_run'])/'candidate' if intake.get('parent_run') else PREVIOUS
    return build(run/'candidate',source,device='cuda')

def finish(run):
    from rag.search import load_index,filter_idx
    from rag.release import compose,ReleaseBundle
    from preprocess.hybrid_validate import validate_extraction
    from rag import preprocessed_chunks
    from pipelines.pipeline_h.chunk_runtime import enable_dom_schema
    enable_dom_schema()
    start=time.perf_counter();run=Path(run).resolve();out=run/'extraction';candidate=run/'candidate'
    intake=read(run/'intake_manifest.json');manifest=read(out/'extraction_manifest.json')
    expected={(d['document_id'],d['version_id']):d for d in intake['documents']}
    actual={(d['document_id'],d['version_id']):d for d in manifest['documents']}
    # A supplement run covers exactly its own frozen intake; the operational run stays 92.
    supplement=intake.get('version')=='production-h-supplement-intake-1'
    if set(expected)!=set(actual) or (not supplement and len(expected)!=92):raise ValueError('Intake document scope mismatch')
    if manifest.get('validation_only_page_plan'):raise ValueError('Test page subset cannot be promoted as an operational release')
    for name,key in (('catalog_snapshot.json','catalog_snapshot_sha256'),('products_snapshot.json','products_snapshot_sha256')):
        if sha256(run/name)!=intake[key]:raise ValueError('Original catalog snapshot changed')
    for key,doc in actual.items():
        if doc!=expected[key]:raise ValueError('Original intake metadata/source contract changed')
    validation=validate_extraction(out);chunks,vectors,meta=load_index('bge-m3',candidate)
    if intake.get('original_document_contract'):
        from pipelines.pipeline_h.source_contract import validate as validate_source_contract
        validate_source_contract(run,with_chunks=True)
    loaded=preprocessed_chunks.load_input(out)
    preprocessed_chunks.verify_ranges(chunks,loaded[3],loaded[4],loaded[5],require_full_coverage=False)
    issues=[]
    for chunk in chunks:
        doc=actual[chunk['document_id'],chunk['version_id']];m=doc['metadata']
        for field in ('product_id','source','role','document_type','language','title'):
            if chunk[field]!=(m.get(field) or ('unknown' if field!='title' else Path(doc['raw_path']).name)):
                issues.append({'chunk_id':chunk['chunk_id'],'field':field})
        for field in INTAKE_METADATA_FIELDS:
            if field in m and chunk.get('document_metadata',{}).get(field)!=m[field]:issues.append({'chunk_id':chunk['chunk_id'],'field':field})
        if 'Qwen' in chunk['provenance']['parser']:raise ValueError('Incorrect inherited Qwen parser label')
    if issues:raise ValueError('Chunk metadata mismatch: '+str(issues[:10]))
    pages=read_jsonl(out/'extraction_pages.jsonl');assets=read_jsonl(out/'extracted_assets.jsonl');units=read_jsonl(out/'source_units.jsonl')
    indexed={(c['document_id'],c['version_id']) for c in chunks};missing=sorted(set(actual)-indexed)
    if missing:raise ValueError('Documents without indexed chunks: '+str(missing))
    failed_pages=[p for p in pages if p['status']!='parsed']
    if failed_pages:raise ValueError('Unresolved incomplete extraction pages: '+str([(p['document_id'],p['pdf_page']) for p in failed_pages]))
    preserved={p:sha256(p)==value for p,value in intake.get('protected_original_pipeline',{}).items()}
    changed=[p for p,ok in preserved.items() if not ok]
    if changed:raise ValueError('Protected original artifact changed: '+str(changed))
    if vectors.shape!=(len(chunks),1024) or not np.isfinite(vectors).all():raise ValueError('Invalid vector matrix')
    if not np.allclose(np.linalg.norm(vectors,axis=1),1,atol=.002):raise ValueError('Vector normalization mismatch')
    if meta['truncated_inputs'] or meta['model']!='BAAI/bge-m3':raise ValueError('Embedding strategy mismatch')
    # This checks row/ID/vector alignment, not retrieval relevance or semantic accuracy.
    probes=np.linspace(0,len(chunks)-1,min(20,len(chunks)),dtype=int)
    self_recall=sum(float(vectors[i]@vectors[i])>=float((vectors@vectors[i]).max())-.002 for i in probes)/len(probes)
    if self_recall!=1:raise ValueError('Vector row identity self-recall failed')
    audit={'status':'passed','documents':len(expected),'pages':len(pages),'indexed_documents':len(indexed),
           'metadata_contract':'all intake metadata unchanged; chunk identity/policy fields checked; raw structural blocks and DOM locators retained',
           'metadata_field_mismatches':issues,'protected_original_artifacts':len(preserved),'protected_originals_unchanged':all(preserved.values()),
           'vectors':list(vectors.shape),'truncated_inputs':meta['truncated_inputs'],'vector_self_recall':self_recall,
           'self_recall_semantic_accuracy':False,'numeric_evidence_approved':False,'source_validation':validation}
    write(run/'metadata_and_integrity_audit.json',audit)
    release=run/'release'
    if not release.exists():compose([(out,candidate)],release)
    bundle=ReleaseBundle(release)
    if len(bundle.docs)!=len(expected) or bundle.chunk_count!=len(chunks):raise ValueError('Release coverage mismatch')
    jobs=read(run/'selection_manifest.json')['jobs'];decisions=read(run/'candidate_decisions.json');default=set(int(i) for i in filter_idx(chunks))
    summary={'status':'complete','processing_scope':(intake['scope'] if supplement else 'user-confirmed existing approved 92 documents'),
             'products':intake['products'],'pages':len(pages),'chunks':len(chunks),'default_search_chunks':len(default),
             'assets_by_type':dict(Counter(a['asset_type'] for a in assets)),
             'logical_figures':sum(a['asset_type']=='figure_group' or a['asset_type']=='figure' and not a.get('logical_group_id') for a in assets),
             'caption_anchored_figure_groups':sum(a['asset_type']=='figure_group' for a in assets),
             'ungrouped_visual_regions':sum(a['asset_type']=='figure' and not a.get('logical_group_id') for a in assets),
             'decorative_visual_candidates':sum(a.get('visual_classification',{}).get('decorative_candidate',False) for a in assets if a['asset_type']=='figure'),
             'figure_count_status':'source/layout candidates including ungrouped visuals; no full-corpus manual figure adjudication',
             'review_required_units':sum(u['review_required'] for u in units),'retrieval_eligible_units':sum(u['retrieval_eligible'] for u in units),
             'vlm_calls':len(jobs),'vlm_calls_by_task':dict(Counter(j['task'] for j in jobs)),
             'accepted_candidates':sum(d['gate']['accepted'] for d in decisions),'rejected_candidates':sum(not d['gate']['accepted'] for d in decisions),
             'stage_timings':read(run/'stage_timings.json') if (run/'stage_timings.json').exists() else {},
             'docling_seconds':sum(r['seconds'] for r in read(run/'docling_progress.json')['documents'] if 'seconds' in r),
             'embedding':{k:meta.get(k) for k in ('model','dim','similarity','n_chunks','embed_seconds','truncated_inputs')},
             'embedding_reuse':read(candidate/'embedding_reuse.json'), 'release':str(release),'audit':audit,
             'old_service_pointer_changed':False,'figure_meaning_generation':False,
             'full_corpus_semantic_accuracy':'not measured: no independently reviewed gold for all 4,160 pages',
             'validation_seconds':time.perf_counter()-start}
    if (run/'repair_summary.json').exists():
        summary['source_repair']=read(run/'repair_summary.json')
        summary['source_content_audit']=read(run/'source_content_audit.json')
        html_ids={d['document_id'] for d in intake['documents'] if d['kind']=='html'}
        summary['source_page_counts']={'original_pdf_pages':sum(p['document_id'] not in html_ids for p in pages),
            'html_dom_preview_pages':sum(p['document_id'] in html_ids for p in pages),
            'html_preview_pages_are_not_original_pages':True}
    write(run/'final_summary.json',summary)
    productrows=[]
    for product,count in intake['products'].items():
        pcs=[c for c in chunks if c['product_id']==product];pids={d['document_id'] for d in actual.values() if d['metadata']['product_id']==product}
        productrows.append(f'| {product} | {count} | {sum(p["document_id"] in pids for p in pages)} | {len(pcs)} |')
    report=f'''# 신규 H 전처리·청킹·임베딩 완료 보고서

기존 승인 92문서, 총 {len(pages):,}쪽을 새 폴더에서 처리했습니다. 이전 코드·원문·DB·운영 인덱스와 서비스 포인터는 보존했습니다.

| 제품 | 문서 | 페이지 | 청크 |
| :-- | --: | --: | --: |
{chr(10).join(productrows)}

## 기능별 구성

1. 원문/메타데이터: 기존 catalog 및 승인 목록, 기존 HTML 렌더·검증된 CMap PDF 파생본. 원본 문서 ID/버전/SHA, 제품·기관·유형·날짜·검색/평가 정책을 보존합니다.
2. 기본 추출: Heron 레이아웃, PDF 텍스트층 또는 RapidOCR/PP-OCRv6, TableFormer accurate. `ra_prep`은 별도 운영 보정 코드로, OCR 재판독·표 복원·머리말·절·발화자·연구 ID 규칙을 적용합니다.
3. 독립 누락 점검: PP-DocLayoutV3 + 닫힌 테두리 규칙. 원래 picture와 chart 증거를 보존하며 잘못 승격된 표는 후보로 분리합니다.
4. 선별 전사: PaddleOCR-VL-1.6-0.9B, 표 `table` 또는 의심 기호 `ocr` 작업. 총 {len(jobs):,}회, 채택 {summary['accepted_candidates']:,}회, 거절 {summary['rejected_candidates']:,}회. Qwen·그림 의미 설명·곡선 수치 생성은 없습니다.
5. 청킹: 기존 `preprocessed-chunks-13-closed-row-blocks`. 목표 768 / 최대 1024 / 표·그림 최대 2048, overlap 96. 절·연구 경계와 표 행 묶음, 캡션·각주, 원문 범위를 유지합니다.
6. 임베딩: 기존 BGE-M3 1024차원 정규화 dense cosine. 총 {len(chunks):,}벡터, 잘린 입력 {meta['truncated_inputs']}건. 완전히 같은 임베딩 입력만 기존 벡터를 재사용합니다.

## 오류 수정과 확인

- 각주 `$^{{a}}$`를 라벨 단어에 붙여 표 후보를 거절하던 검증 오류를 수정했습니다. 추출된 원문 표기를 바꾸거나 90% 라벨 보존 기준을 낮추지 않았습니다.
- 의심 특수기호는 원문 셀 crop을 다시 읽습니다. 기호 밖 문구와 V158 등의 표기를 보존하는 후보만 채택합니다. 자동 치환으로 정답을 주입하지 않습니다.
- 회전 PDF의 화면 좌표와 원문 좌표를 변환하며, 모든 bbox·원본/파생본 SHA·청크 표현 범위를 검사했습니다.
- 원본 메타데이터 불일치 0건, 92문서 모두 인덱스 연결, 보호한 기존 파일 {len(preserved)}개 변경 없음, 벡터 정규화 및 ID 연결 검사 통과.
- 검토 필요 unit {summary['review_required_units']:,}개는 원문/후보/거절 이유와 함께 보존하고 검색 적격성과 구분했습니다.

## 성능과 한계

기본 Docling 추출 누적 {summary['docling_seconds']/60:.1f}분, BGE 임베딩 {meta.get('embed_seconds',0)/60:.1f}분입니다. 단계별 실제 시간은 `stage_timings.json`, 모델 로딩/전사 시간은 각 `*_runtime.json`에 있습니다. 중단 후 재개 시간은 벽시계 총시간과 구분합니다.

전체 4,160쪽의 의미 정확도를 수치로 주장하지 않습니다. 기존 15쪽 개발 표본의 H 실험은 88/90 셀, 32/34 구절, 표 18/18·그림 4/4였고 독립 검토 전입니다. 이번 전체 처리는 출처·구조·메타데이터·범위·벡터 무결성을 확인한 결과입니다. `metadata_and_integrity_audit.json`과 `candidate_decisions.json`에서 근거를 확인할 수 있습니다.

## 사용 위치

- 새 전처리: `extraction/`
- 새 청크·벡터: `candidate/`
- 기존 읽기/검색 도구 호환 릴리스: `release/`
- 원문 대조 및 메타데이터: `intake_manifest.json`, `extraction/document_structures.jsonl`, `extraction/html_dom_inventory.jsonl`, `candidate/chunk_structure_links.jsonl`

서비스 전환은 수행하지 않았습니다. 새 릴리스 경로를 지정하면 기존 검색·원문 검토 도구로 사용할 수 있습니다.
'''
    if summary.get('source_repair'):
        counts=summary['source_page_counts'];repair=summary['source_repair']
        report=report.replace('기존 HTML 렌더·검증된 CMap PDF 파생본','원본 HTML DOM·검증된 CMap PDF 파생본')
        report=report.replace(f'총 {len(pages):,}쪽',f"PDF 원문 {counts['original_pdf_pages']:,}쪽 및 HTML DOM 미리보기 {counts['html_dom_preview_pages']:,}쪽")
        report+='\n## DOM·좌표 보완 적용\n\n'
        report+=f"HTML10문서는 원본 DOM의 {repair['html_dom_units']:,}개 단위를 직접 보존했습니다. HTML의 PDF는 새 DOM 기반 미리보기이며 원본 페이지 번호가 아닙니다. DOM node path/start/end와 원본 SHA를 참조에 기록했습니다. 원래 catalog metadata는 변경하지 않았습니다.\n\n"
        report+=f"독립 문자 좌표로 복구한 항목 {repair['native_geometry_recovered']}개, 미해결 작은 문자 좌표 경고 {repair['geometry_warning_blocks']}개입니다. 경고에는 원문·원시 block·페이지 미리보기를 남겼으며 이 경고 때문에 같은 페이지의 정상 본문을 제외하지 않습니다. 전체 의미 정확도가 검증됐다는 뜻은 아닙니다.\n\n"
        report+=f"임베딩 재사용 원본은 이전 v1의 완료 candidate입니다. 재사용 {summary['embedding_reuse']['reused_vectors']:,}개, 새 계산 {summary['embedding_reuse']['new_vectors']:,}개이며 동일한 header+text만 재사용했습니다. 새 run/candidate/release를 만들고 기존 운영 서비스 포인터는 유지했습니다.\n"
    if supplement:
        report=report.replace('기존 승인 92문서',f"보충 범위 {len(expected)}문서({intake['scope']})").replace('92문서 모두',f'{len(expected)}문서 모두')
        report+=f"\n## 보충 실행 결정\n\n{intake['decision']}\n\n기존 92문서 릴리스와의 병합은 별도 단계에서 수행합니다.\n"
    (run/'FINAL_REPORT.md').write_text(report,encoding='utf-8');return audit

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--stage',choices=['embed','release'],required=True);a=p.parse_args()
    run=Path(a.run).resolve();print(json.dumps(embed(run) if a.stage=='embed' else finish(run),ensure_ascii=False))
