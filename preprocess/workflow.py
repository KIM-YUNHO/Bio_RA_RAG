"""One local PDF workflow, using the existing extraction and retrieval contracts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time

from .ingest import Document, assert_model_execution_allowed, from_catalog, from_inputs, sha256, stable_hash
from .inventory import write_json

VERSION = 'local-ra-workflow-1'
ROOT = Path(__file__).resolve().parents[1]
CODE = tuple(sorted(p.relative_to(ROOT).as_posix() for p in
    [*ROOT.glob('preprocess/*.py'), *ROOT.glob('rag/*.py'), ROOT/'collection/text_layer.py',
     ROOT/'data/reviewed/studies.json',ROOT/'rag/doc_groups.json']))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def exclusive(out):
    """OS releases this lock on a crash; a leftover file does not block resume."""
    path = out/'workflow.lock'
    with path.open('a+b') as lock:
        if path.stat().st_size == 0:
            lock.write(b'0');lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:raise ValueError('This workflow is already running') from exc
        else:
            import fcntl
            try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:raise ValueError('This workflow is already running') from exc
        try:yield
        finally:
            lock.seek(0)
            if os.name == 'nt':msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:fcntl.flock(lock, fcntl.LOCK_UN)


def file_hashes(folder, names):
    return {name:sha256(folder/name) for name in names if (folder/name).is_file()}


def seed_parser(source, out, documents):
    """Copy source-validated parser caches; cache keys still decide reuse."""
    from .hybrid import assert_sources, cache_read
    from .hybrid_validate import validate_extraction
    source=Path(source).resolve()
    manifest=read(source/'extraction_manifest.json')
    registered=[Document(**d) for d in manifest['documents']]
    assert_model_execution_allowed(registered);assert_sources(registered)
    if not {d.version_id for d in documents} <= {d.version_id for d in registered}:
        raise ValueError('Parser cache does not cover the selected source documents')
    if (source/'extraction_integrity.json').is_file():validate_extraction(source)
    copied=0
    # A page cache is independent of the final extraction bundle. Its exact
    # source/model/render key is checked again by run_hybrid before any reuse.
    for raw in sorted((source/'paddle_raw').glob('*.json')):
        key=raw.stem
        if cache_read(raw,key) is None:continue
        destination=out/'paddle_raw'/raw.name;destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists() and sha256(destination)!=sha256(raw):raise ValueError('Conflicting parser cache')
        shutil.copyfile(raw,destination);copied+=1
    return copied


def build_index(folder, queries, device, include_nondefault, factory=None):
    import numpy as np
    from rag.embed_index import Embedder, MODELS
    from rag.search import Bm25, dense_hits, filter_idx, rrf, to_pages, load_index
    from rag.preprocessed_chunks import related_visual_contexts
    from .hybrid import read_jsonl
    chunks=read_jsonl(folder/'chunks.jsonl')
    embedder=(factory or Embedder)('bge-m3',device=device,local_only=True)
    started=time.perf_counter()
    try:
        vectors=embedder.encode_docs(chunks)
        if len(vectors)!=len(chunks) or vectors.ndim!=2 or not np.isfinite(vectors).all():
            raise ValueError('Invalid document embeddings')
        if embedder.truncated:raise ValueError('Embedding truncated a chunk')
        dest=folder/'emb/bge-m3';dest.mkdir(parents=True,exist_ok=True)
        np.save(dest/'doc.npy',vectors)
        write_json(dest/'chunk_ids.json',[c['chunk_id'] for c in chunks])
        meta={'model_key':'bge-m3','model':MODELS['bge-m3']['doc'],'revision':embedder.revision,
            'dim':int(vectors.shape[1]),'n_chunks':len(chunks),'similarity':'cosine',
            'chunks_sha256':sha256(folder/'chunks.jsonl'),'truncated_inputs':0,'non_finite_values':0,
            'device':embedder.device,'local_only':True,'created_at':timestamp(),
            'embed_seconds':round(time.perf_counter()-started,3)}
        write_json(dest/'meta.json',meta)
        load_index('bge-m3',folder)
        results=[]
        if queries:
            query_vectors=embedder.encode_queries(queries)
            if len(query_vectors)!=len(queries) or not np.isfinite(query_vectors).all():
                raise ValueError('Invalid query embeddings')
            idx=filter_idx(chunks,include_nondefault=include_nondefault)
            bm=Bm25(chunks)
            for query,vector in zip(queries,query_vectors):
                hits=rrf([bm.hits(query,idx),dense_hits(vector,vectors,idx)],top_n=50)
                pages=to_pages(hits,chunks,top_k=5)
                for page in pages:
                    page['visual_contexts']=related_visual_contexts(folder,page['best_chunk'],
                        include_nondefault=include_nondefault)
                results.append({'query':query,'mode':'hybrid','include_nondefault':include_nondefault,
                    'include_evaluation':False,'results':pages})
        write_json(folder/'search_examples.json',results)
        return meta
    finally:
        del embedder
        import gc
        gc.collect()
        if factory is None:
            import torch
            if torch.cuda.is_available():torch.cuda.empty_cache()


def run(out, config, *, inputs=None, catalog=None, document_ids=None, root=ROOT, resume=False,
        use_qwen=True, max_qwen_calls=None, label_ocr=True, max_ocr_calls=None,
        embedding=True, device='cuda', queries=(), include_nondefault=False, seed_run=None,
        paddle=None, qwen=None, counter=None, index_factory=None):
    """All PDF pages. Resume reuses completed stages; no active-index mutation."""
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    config.validate()
    for value in (max_qwen_calls,max_ocr_calls):
        if value is not None and (type(value) is not int or value<0):raise ValueError('Invalid call budget')
    out=Path(out).resolve()
    if out in {ROOT,ROOT/'data',ROOT/'data/rag'}:raise ValueError('Choose a separate workflow directory')
    manifest_path=out/'workflow_manifest.json'
    if not resume and out.exists() and any(out.iterdir()):raise ValueError('Workflow output must be empty; use --resume')
    if resume and not manifest_path.is_file():raise ValueError('No workflow to resume')
    if resume and (inputs or catalog or document_ids):raise ValueError('Resume uses the saved input documents')
    if not resume and (bool(inputs)==bool(catalog)):raise ValueError('Choose PDF inputs or a catalog')
    out.mkdir(parents=True,exist_ok=True)
    with exclusive(out):
        extraction=out/'extraction';labels=out/'labels';candidate=out/'candidate'
        options={'use_qwen':use_qwen,'max_qwen_calls':max_qwen_calls,'label_ocr':label_ocr,
            'max_ocr_calls':max_ocr_calls,'embedding':embedding,'device':device,
            'queries':list(queries),'include_nondefault':include_nondefault}
        code={name:sha256(ROOT/name) for name in CODE}
        if resume:
            manifest=read(manifest_path)
            if manifest['version']!=VERSION or manifest['config']!=config.to_dict() or manifest['options']!=options:
                raise ValueError('Resume requires the original configuration and options')
            if manifest['code_sha256']!=code:raise ValueError('Workflow code changed; use a new output directory')
            documents=[Document(**d) for d in manifest['documents']]
        else:
            documents=from_catalog(catalog,root,document_ids) if catalog else from_inputs(inputs,extraction)
            assert_model_execution_allowed(documents)
            from .html_render import prepare
            prepare(documents,extraction)
            from .native_fonts import prepare as prepare_fonts
            prepare_fonts(documents,extraction)
            manifest={'version':VERSION,'started_at':timestamp(),'lifecycle':'running','stage':'intake',
                'config':config.to_dict(),'options':options,'documents':[d.to_dict() for d in documents],
                'code_sha256':code,'stages':{},'active_index_updated':False}
        assert_model_execution_allowed(documents)
        from .hybrid import assert_sources, run_hybrid, resume_enrichment, read_jsonl
        from .hybrid_validate import validate_extraction
        assert_sources(documents)
        manifest.update(lifecycle='running',error=None)
        write_json(manifest_path,manifest)

        def stage(name, action, folder, files):
            prior=manifest['stages'].get(name)
            if prior and prior['status']=='complete':
                saved_folder=Path(prior['folder'])
                if any(sha256(saved_folder/n)!=digest for n,digest in prior['files'].items()):
                    raise ValueError(f'Completed workflow stage changed: {name}')
                print(f'Workflow {name}: reused',flush=True);return prior['result']
            manifest.update(stage=name,updated_at=timestamp());write_json(manifest_path,manifest)
            print(f'Workflow {name}: starting',flush=True);started=time.perf_counter()
            result=action()
            if name=='label_ocr':folder=Path(result['run'])
            if name=='chunk':files=tuple(dict.fromkeys([*files,*result['artifacts']]))
            manifest['stages'][name]={'status':'complete','finished_at':timestamp(),
                'elapsed_seconds':round(time.perf_counter()-started,3),'folder':str(folder),
                'files':file_hashes(folder,files),
                'result':result}
            write_json(manifest_path,manifest);return result

        try:
            if seed_run and 'extract' not in manifest['stages']:
                manifest['seed_parser_cache_files']=seed_parser(seed_run,extraction,documents)
            extracted=stage('extract',lambda:run_hybrid(documents,extraction,config,use_qwen=False,paddle=paddle),
                extraction,('extraction_pages.jsonl',))
            final=extraction
            if use_qwen:
                stage('enrich',lambda:resume_enrichment(extraction,config,max_qwen_calls,adapter=qwen),extraction,
                    ('extraction_manifest.json','extraction_summary.json','extraction_integrity.json',
                     'extracted_assets.jsonl','source_units.jsonl','visual_groups.jsonl'))
            if label_ocr and use_qwen:
                from .figure_ocr import complete
                def recover_labels():
                    source=extraction;target=labels
                    attempts=manifest.setdefault('label_attempts',[])
                    for previous in attempts:
                        try:validate_extraction(Path(previous));source=Path(previous)
                        except (OSError,ValueError,KeyError):pass
                    if target.exists() and any(target.iterdir()):target=out/f'labels_retry_{len(attempts)+1}'
                    attempts.append(str(target));write_json(manifest_path,manifest)
                    return {'run':str(target),'statistics':complete(source,target,config,max_calls=max_ocr_calls)}
                label_result=stage('label_ocr',recover_labels,labels,
                    ('extraction_manifest.json','extraction_summary.json','extraction_integrity.json',
                     'extracted_assets.jsonl','source_units.jsonl','visual_groups.jsonl'))
                final=Path(label_result['run'])
            validate_extraction(final)
            from rag.preprocessed_chunks import write
            chunked=stage('chunk',lambda:write(final,candidate,counter=counter),candidate,('chunks_summary.json',))
            if embedding:
                stage('index',lambda:build_index(candidate,list(queries),device,include_nondefault,index_factory),
                    candidate,('emb/bge-m3/doc.npy','emb/bge-m3/chunk_ids.json','emb/bge-m3/meta.json','search_examples.json'))
            from .reporting import build_report, save_report
            report=build_report(final);save_report(report,final)
            pages=read_jsonl(final/'extraction_pages.jsonl');coverage=[]
            import fitz
            for doc in documents:
                with fitz.open(doc.pdf_path) as pdf:total=len(pdf)
                own=[p for p in pages if (p['document_id'],p['version_id'])==(doc.document_id,doc.version_id)]
                coverage.append({'document_id':doc.document_id,'source_pages':total,'attempted_pages':len(own),
                    'parsed_pages':sum(p['status']=='parsed' for p in own),
                    'all_pages_attempted':{p['pdf_page'] for p in own}==set(range(1,total+1))})
            if not all(c['all_pages_attempted'] for c in coverage):raise ValueError('Workflow omitted input pages')
            summary={'version':VERSION,'finished_at':timestamp(),'execution_complete':True,
                'content_state':report['state'],'documents':coverage,'counts':report['counts'],
                'chunks':chunked['chunks'],'embedding_built':embedding,'candidate_directory':str(candidate),
                'final_extraction_directory':str(final),'review_items':report['review_items'],
                'stage_execution':manifest['stages'],'active_index_updated':False,'accuracy_certified':False}
            replies=[read(path).get('reply',{}) for path in (extraction/'qwen_attempts').glob('*.json')]
            summary['execution_cost']={'paddle_page_calls':extracted['parser_calls'],
                'paddle_page_cache_reused':extracted['parser_cache_reused'],
                'qwen_calls':manifest['stages'].get('enrich',{}).get('result',{}).get('attempted_calls',0),
                'qwen_input_tokens':sum(r.get('prompt_eval_count',0) or 0 for r in replies),
                'qwen_output_tokens':sum(r.get('eval_count',0) or 0 for r in replies),
                'label_ocr_calls':manifest['stages'].get('label_ocr',{}).get('result',{}).get('statistics',{}).get('attempted_calls',0)}
            write_json(out/'workflow_report.json',summary)
            lines=['# PDF 전처리 실행 결과','',f"문서 {len(documents)}개 · 전체 {sum(c['source_pages'] for c in coverage)}페이지 · 후보 청크 {chunked['chunks']}개",'',
                f"내용 상태: {report['state_label']}. 전체 실행은 종료됐으며 검토·보류 항목은 별도로 남습니다.",'',
                '| 문서 | 전체 페이지 | 시도 | 추출 성공 |','|---|---:|---:|---:|']
            lines += [f"| {c['document_id']} | {c['source_pages']} | {c['attempted_pages']} | {c['parsed_pages']} |" for c in coverage]
            lines += ['',f"- 후보 검색 폴더: `{candidate}`",f"- 문서별 결과·검토 사유: `{final/'processing_report.md'}`",
                f"- 검색 실행 예시 결과: `{candidate/'search_examples.json'}`",'',
                '```powershell',f'.\\.venv\\Scripts\\python.exe -B rag/search.py --rag-dir "{candidate}" --mode hybrid --local-only --visual-context --query "your question"','```','',
                '원본·페이지·영역 출처를 보존합니다. 활성 인덱스는 변경하지 않았습니다.']
            (out/'workflow_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
            manifest.update(lifecycle='finished',stage='finished',finished_at=timestamp(),
                report=str(out/'workflow_report.md'),candidate_directory=str(candidate))
            write_json(manifest_path,manifest)
            return summary
        except BaseException as exc:
            manifest.update(lifecycle='failed',error=f'{type(exc).__name__}: {exc}',updated_at=timestamp())
            write_json(manifest_path,manifest);raise
