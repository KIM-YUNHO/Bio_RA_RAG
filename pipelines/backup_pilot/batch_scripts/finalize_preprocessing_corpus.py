"""Finish all remaining approved table decisions and reuse verified embeddings."""
import sys,json,os,time,shutil,hashlib
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
from preprocess.hybrid import read_jsonl
from preprocess.ingest import sha256,stable_hash,Document
from preprocess.queue_finalize import finalize
from preprocess.inventory import write_json
from preprocess.workflow import timestamp
from rag.preprocessed_chunks import write
from rag import build_chunks as bc
from rag.embed_index import Embedder
from rag.release import compose,ReleaseBundle
from rag.search import load_index
import numpy as np
RUN=ROOT/'data/runs/preprocessing_completion_2026-10-10'
WORK=ROOT/'data/preprocessed/preprocessing_completion_2026-10-10/queue_finalization_v2'
CAND=ROOT/'data/rag/candidates/preprocessing_completion_2026-10-10_v2'
RELEASE=ROOT/'data/rag/releases/preprocessing_completion_2026-10-10_v2'
BASE=ROOT/'data/rag/releases/preprocessing_completion_2026-10-10_v1'
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
verification=read(RUN/'update_v21_verification/verification.json')
if not verification['passed']:raise ValueError('Current pipeline checks must pass')
for name,digest in verification['code_sha256'].items():
    if sha256(ROOT/name)!=digest:raise ValueError('Changed code after validation: '+name)
preliminary=read(RUN/'completion_progress.json')
if len(preliminary['components'])!=89:raise ValueError('Preliminary source/chunk preparation has not completed')
base={'components':[{'run':c['source'],'candidate':c['candidate']} for c in preliminary['components']]}
approved=read(ROOT/'data/runs/corpus_update_2026-10-09/corpus_plan.json')
eligible={d['version_id'] for d in approved['documents']};protected={d['version_id'] for d in approved['protected_exclusions']}
pending={r['asset_id'] for r in read(RUN/'remaining_source_grid_probe.json')['rows']}
if RELEASE.exists() or CAND.exists():raise ValueError('Do not overwrite finalized corpus outputs')
WORK.mkdir(parents=True,exist_ok=True);CAND.mkdir(parents=True)
begin=time.perf_counter();counter=bc.Counter('BAAI/bge-m3',local_only=True)
status={'started_at':timestamp(),'lifecycle':'running','stage':'finalize_remaining_tables','components':[],
    'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0}
write_json(RUN/'finalization_progress.json',status)
specs=[];all_chunks=[];lengths=[];decisions=[];coverage=[]
for index,item in enumerate(base['components']):
    source,old=Path(item['run']),Path(item['candidate'])
    old_summary=read(old/'chunks_summary.json')
    for name,digest in old_summary['artifacts'].items():
        if sha256(old/name)!=digest:raise ValueError('Changed preliminary chunk artifact: '+name)
    docs=read(source/'extraction_manifest.json')['documents']
    if any(d['version_id'] not in eligible or d['version_id'] in protected for d in docs):raise ValueError('Scope mismatch')
    key=stable_hash([str(source),str(old)])[:20];candidate=CAND/'components'/key
    ids=[a['asset_id'] for a in read_jsonl(source/'extracted_assets.jsonl') if a['asset_id'] in pending]
    result=None
    status['current_component']={'index':index+1,'documents':[d['document_id'] for d in docs],'selected_tables':len(ids)}
    write_json(RUN/'finalization_progress.json',status)
    if ids:
        destination=WORK/'components'/key/'extraction';result=finalize(source,destination,ids,eligible,protected);source=destination
        summary=write(source,candidate,counter=counter)
        decisions.extend({'source':str(source),'asset_id':a['asset_id'],'document_id':a['document_id'],'pdf_page':a['pdf_page'],
            'stage':a['route'].get('next_stage'),'source_grid_cells':len((a.get('source_grid_refinement') or {}).get('plan',{}).get('cells',[]))}
            for a in read_jsonl(source/'extracted_assets.jsonl') if a['asset_id'] in ids)
    else:
        shutil.copytree(old,candidate,ignore=shutil.ignore_patterns('emb','search_examples.json'))
        summary=read(candidate/'chunks_summary.json');summary.update(output_directory=str(candidate),reused_chunk_artifacts_from=str(old))
        write_json(candidate/'chunks_summary.json',summary)
    chunks=read_jsonl(candidate/'chunks.jsonl');all_chunks.extend(chunks);lengths.append(len(chunks));specs.append((source,candidate))
    pages=read_jsonl(source/'extraction_pages.jsonl')
    import fitz
    for d in docs:
        obj=Document(**d);own=[p for p in pages if p['version_id']==obj.version_id]
        with fitz.open(obj.pdf_path) as pdf:total=len(pdf)
        coverage.append({'document_id':obj.document_id,'version_id':obj.version_id,'product_id':obj.metadata['product_id'],
            'regulator':obj.metadata['source'],'source_pages':total,'parsed_pages':sum(p['status']=='parsed' for p in own),
            'all_pages_present':{p['pdf_page'] for p in own}==set(range(1,total+1)),
            'chunks':sum(c['version_id']==obj.version_id for c in chunks),'source':str(source),'candidate':str(candidate)})
    status['components'].append({'source':str(source),'candidate':str(candidate),'chunks':len(chunks),'finalization':result})
    write_json(RUN/'finalization_progress.json',status)
    print(json.dumps({'component':index+1,'total':len(base['components']),'chunks':len(chunks),
        'remaining_tables':len(ids),'recovered':(result or {}).get('recovered_tables',0),'review':(result or {}).get('explicit_review_tables',0)}),flush=True)
if {d['version_id'] for d in coverage}!=eligible or len(coverage)!=len(eligible):raise ValueError('Document version mismatch')
if {d['asset_id'] for d in decisions}!=pending:raise ValueError('Unfinished remaining table decision')
if not all(d['all_pages_present'] and d['parsed_pages']==d['source_pages'] and d['chunks'] for d in coverage):raise ValueError('Incomplete coverage')
from rag.service_scope import SOURCES
if any(c['source'] not in SOURCES for c in all_chunks):raise ValueError('Final corpus contains an excluded regulator')
status.update(stage='embedding',chunks=len(all_chunks));write_json(RUN/'finalization_progress.json',status)
encoder=Embedder('bge-m3',device='cuda',local_only=True);model_hash=hashlib.sha256()
import torch
for name,tensor in sorted(encoder.model.state_dict().items()):
    model_hash.update(name.encode());model_hash.update(str(tuple(tensor.shape)).encode());model_hash.update(str(tensor.dtype).encode())
    model_hash.update(tensor.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
identity={'model':'BAAI/bge-m3','revision':encoder.revision,'loaded_state_sha256':model_hash.hexdigest(),
    'tokenizer_sha256':hashlib.sha256(encoder.tok.backend_tokenizer.to_str().encode()).hexdigest(),
    'normalize':True,'max_doc_tokens':8192,'half_precision':encoder.half,
    'modules':str(encoder.model),'pooling':encoder.model[1].get_config_dict()}
write_json(RUN/'embedding_identity_v2.json',identity)
print('Embedding the complete finalized corpus once',flush=True)
embedding_start=time.perf_counter();vectors=encoder.encode_docs(all_chunks)
embedding_seconds=round(time.perf_counter()-embedding_start,3)
if vectors.shape!=(len(all_chunks),1024) or encoder.truncated or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors,axis=1),1,atol=.002):
    raise ValueError('Invalid final embeddings')
offset=0
for (source,candidate),length in zip(specs,lengths):
    destination=candidate/'emb/bge-m3';destination.mkdir(parents=True);chunks=all_chunks[offset:offset+length]
    np.save(destination/'doc.npy',vectors[offset:offset+length]);offset+=length
    write_json(destination/'chunk_ids.json',[c['chunk_id'] for c in chunks])
    write_json(destination/'meta.json',{'model_key':'bge-m3','model':'BAAI/bge-m3','revision':encoder.revision,
        'dim':1024,'n_chunks':length,'similarity':'cosine','chunks_sha256':sha256(candidate/'chunks.jsonl'),
        'truncated_inputs':0,'non_finite_values':0,'device':encoder.device,'local_only':True,'created_at':timestamp(),
        'embedding_identity':identity,'fresh_full_corpus_embedding':True,'embed_seconds':embedding_seconds})
    write_json(candidate/'search_examples.json',[])
status['stage']='release_validation';write_json(RUN/'finalization_progress.json',status)
manifest=compose(specs,RELEASE);bundle=ReleaseBundle(RELEASE)
chunks,final_vectors,meta=load_index('bge-m3',Path(manifest['candidate']))
if not np.array_equal(final_vectors,vectors):raise ValueError('Final composed vectors changed order or values')
routes=Counter(a['route'].get('next_stage','legacy') for a in bundle.assets.values())
nonterminal=[a['asset_id'] for a in bundle.assets.values() if a['route'].get('task')
    and (a.get('enrichment') or {}).get('status') not in ('heuristic_checks_passed','needs_review','complete')]
if nonterminal or routes['light_ocr']:raise ValueError('Unfinished OCR/model work remains')
tables=[{'asset_id':a['asset_id'],'document_id':a['document_id'],'pdf_page':a['pdf_page'],
    'cells':len(a['source_grid_refinement']['plan']['cells']),'retrieval_eligible':bundle.units[a['asset_id']]['retrieval_eligible'],
    'review_required':bundle.units[a['asset_id']]['review_required'],'continuation_status':a.get('continuation_status')}
    for a in bundle.assets.values() if a['selected']['origin']=='source_fixed_grid_table_candidate']
exclusions=read_jsonl(Path(manifest['candidate'])/'chunk_exclusions.jsonl')
by_product={p:{'documents':len({c['version_id'] for c in chunks if c['product_id']==p}),
    'chunks':sum(c['product_id']==p for c in chunks),'vectors':sum(c['product_id']==p for c in chunks)} for p in sorted({c['product_id'] for c in chunks})}
quality={'review_required_units':sum(bool(u.get('review_required')) for u in bundle.units.values()),
    'description_review_required_units':sum(bool(u.get('description_review_required')) for u in bundle.units.values()),
    'excluded_units':len(exclusions),'exclusion_reasons':dict(Counter(r for e in exclusions for r in e['reasons'])),
    'finalized_structure_review_tables':sum(d['stage']=='review' for d in decisions),'model_stage_nonterminal':0,'ordinary_ocr_pending':0}
report={'execution_complete':True,'status':'complete_with_explicit_quality_review',
    'primary_objective':'Approved corpus preprocessing through embeddings',
    'approved_documents':len(eligible),'protected_excluded_documents':len(protected),
    'parsed_pages':sum(d['parsed_pages'] for d in coverage),'all_vectors_finite':True,'all_vectors_normalized':True,
    'truncated_inputs':0,'all_chunk_ids_aligned':True,'dimension':1024,
    'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0,'service_started':False,'active_index_promoted':False,
    'semantic_accuracy_certified':False,'rag_accuracy_evaluated_in_this_run':False,
    'pipeline_contract':'docs/preprocessing/PIPELINE_COMPLETION_2026-10-10.md',
    'completed_at':timestamp(),'release':str(RELEASE),'release_manifest_sha256':sha256(RELEASE/'release_manifest.json'),
    'candidate':manifest['candidate'],'documents':coverage,'chunks':len(chunks),'vectors':len(final_vectors),'by_product':by_product,
    'source_grid_tables':tables,'source_grid_recovered':len(tables),'source_grid_cells':sum(t['cells'] for t in tables),
    'source_grid_indexed_tables':sum(t['retrieval_eligible'] for t in tables),'remaining_table_decisions':decisions,
    'quality_review':quality,'routes':dict(routes),'new_embedding_inputs':len(all_chunks),'embedding_identity':identity,
    'embedding_seconds':embedding_seconds,'reused_historical_vectors':0,
    'code_sha256':verification['code_sha256'],'tests':{k:verification[k] for k in ('tests_run','passed','failures','errors')},
    'finalization_seconds':round(time.perf_counter()-begin,3)}
if any(sha256(ROOT/n)!=h for n,h in verification['code_sha256'].items()):raise ValueError('Code changed during finalization')
write_json(RUN/'preprocessing_completion_v2.json',report)
write_json(WORK/'components.json',{'components':status['components'],'release':str(RELEASE)})
status.update(lifecycle='finished',stage='complete',completed_at=report['completed_at']);write_json(RUN/'finalization_progress.json',status)
print(json.dumps({k:report[k] for k in ('execution_complete','approved_documents','parsed_pages','chunks','vectors','source_grid_recovered','source_grid_cells','source_grid_indexed_tables','by_product','quality_review','embedding_seconds','finalization_seconds')},ensure_ascii=False),flush=True)
