"""Finalize approved corpus through fresh, fingerprinted local embeddings."""
import json,sys,os,time,shutil,hashlib
from pathlib import Path
from collections import Counter,defaultdict
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
from preprocess.ingest import sha256,stable_hash,Document
from preprocess.hybrid import read_jsonl
from preprocess.inventory import write_json
from preprocess.workflow import timestamp
from preprocess.grid_refinement import refine
from rag.preprocessed_chunks import write
from rag import build_chunks as bc
from rag.embed_index import Embedder
from rag.release import compose,ReleaseBundle
from rag.search import load_index
import numpy as np

RUN=ROOT/'data/runs/preprocessing_completion_2026-10-10'
WORK=ROOT/'data/preprocessed/preprocessing_completion_2026-10-10/source_grid_v1'
CAND=ROOT/'data/rag/candidates/preprocessing_completion_2026-10-10_v1'
RELEASE=ROOT/'data/rag/releases/preprocessing_completion_2026-10-10_v1'
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
if RELEASE.exists():raise ValueError('Do not overwrite an existing release')
verification=read(RUN/'update_v20_verification/verification.json')
if not verification['passed']:raise ValueError('Current regression checks must pass before corpus execution')
for name,digest in verification['code_sha256'].items():
    if sha256(ROOT/name)!=digest:raise ValueError('Code changed after regression verification: '+name)
baseline=ROOT/'data/rag/releases/corpus_update_2026-10-09_updated_v1'
release=read(baseline/'release_manifest.json');approved=read(ROOT/'data/runs/corpus_update_2026-10-09/corpus_plan.json')
eligible={d['version_id'] for d in approved['documents']};protected={d['version_id'] for d in approved['protected_exclusions']}
targets=defaultdict(list)
for row in read(RUN/'source_grid_prepare_v5/summary.json')['rows']:targets[str(Path(row['source']).resolve())].append(row['asset_id'])
WORK.mkdir(parents=True,exist_ok=True);CAND.mkdir(parents=True,exist_ok=True)
status={'started_at':timestamp(),'lifecycle':'running','stage':'source_grid_and_chunks','documents':92,
    'protected_excluded':len(protected),'components':[],'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0}
write_json(RUN/'completion_progress.json',status)
counter=bc.Counter('BAAI/bge-m3',local_only=True);specs=[];all_chunks=[];lengths=[];coverage=[];plans=[]
begin=time.perf_counter()
for index,item in enumerate(release['components']):
    source=Path(item['run']);old=Path(item['candidate'])
    for name,digest in item['hashes'].items():
        if sha256(old/name)!=digest:raise ValueError('Changed baseline candidate: '+name)
    docs=read(source/'extraction_manifest.json')['documents']
    if any(d['version_id'] not in eligible or d['version_id'] in protected for d in docs):raise ValueError('Protected/out-of-scope document')
    component_key=stable_hash([str(source),str(old)])[:20];candidate=CAND/'components'/component_key
    if candidate.exists():raise ValueError('Existing candidate checkpoint; choose a new output or inspect before resume')
    selected=targets.get(str(source.resolve()),[]);result=None
    if selected:
        destination=WORK/'components'/component_key/'extraction'
        result=refine(source,destination,selected,eligible,protected)
        if result.get('recovered_tables')!=len(selected):raise ValueError('Not all source-fixed table targets recovered')
        source=destination
        chunk_summary=write(source,candidate,counter=counter)
        for a in read_jsonl(source/'extracted_assets.jsonl'):
            if a['asset_id'] in selected:
                plan=a['source_grid_refinement']['plan']
                plans.append({'asset_id':a['asset_id'],'document_id':a['document_id'],'pdf_page':a['pdf_page'],
                    'cells':len(plan['cells']),'unresolved_cells':len(plan['unresolved_cells']),'issues':plan['issues'],
                    'continuation_status':a.get('continuation_status'),'representation_sha256':stable_hash(a['selected']),
                    'plan_sha256':stable_hash(plan),'source':str(source)})
    else:
        # Source/chunk text and all sidecar artifacts are sealed and byte-identical.
        # The new vectors below are calculated once for the complete final corpus.
        shutil.copytree(old,candidate,ignore=shutil.ignore_patterns('emb','search_examples.json'))
        chunk_summary=read(candidate/'chunks_summary.json')
        chunk_summary.update(output_directory=str(candidate),reused_chunk_artifacts_from=str(old),
            reused_chunks_sha256=sha256(old/'chunks.jsonl'))
        write_json(candidate/'chunks_summary.json',chunk_summary)
    chunks=read_jsonl(candidate/'chunks.jsonl');all_chunks.extend(chunks);lengths.append(len(chunks));specs.append((source,candidate))
    own_pages=read_jsonl(source/'extraction_pages.jsonl')
    for value in docs:
        d=Document(**value);own=[p for p in own_pages if p['version_id']==d.version_id]
        import fitz
        with fitz.open(d.pdf_path) as pdf:page_count=len(pdf)
        own_chunks=[c for c in chunks if c['version_id']==d.version_id]
        coverage.append({'document_id':d.document_id,'version_id':d.version_id,'product_id':d.metadata['product_id'],
            'regulator':d.metadata['source'],'source_pages':page_count,'parsed_pages':sum(p['status']=='parsed' for p in own),
            'all_pages_present':{p['pdf_page'] for p in own}==set(range(1,page_count+1)),'chunks':len(own_chunks),
            'source':str(source),'candidate':str(candidate)})
    record={'index':index+1,'source':str(source),'candidate':str(candidate),'chunks':len(chunks),'source_grid':result}
    status['components'].append(record);write_json(RUN/'completion_progress.json',status)
    print(json.dumps({'component':index+1,'total':len(release['components']),'chunks':len(chunks),
        'recovered_tables':len(selected),'total_chunks':len(all_chunks)}),flush=True)
if {d['version_id'] for d in coverage}!=eligible or len(coverage)!=len(eligible):raise ValueError('Corpus document coverage mismatch')
if not all(d['all_pages_present'] and d['parsed_pages']==d['source_pages'] and d['chunks']>0 for d in coverage):raise ValueError('Incomplete document/page/chunk coverage')
if len({c['chunk_id'] for c in all_chunks})!=len(all_chunks):raise ValueError('Duplicate chunk IDs')
if any(c['source'] not in ('EMA','FDA','Health Canada') for c in all_chunks):raise ValueError('Service scope mismatch')
status.update(stage='embedding',chunks=len(all_chunks),chunk_seconds=round(time.perf_counter()-begin,3))
write_json(RUN/'completion_progress.json',status)
print('Embedding final corpus once, with the installed local BGE-M3 encoder',flush=True)
encoder=Embedder('bge-m3',device='cuda',local_only=True)
# Pin the actual loaded tensors, tokenizer and pooling/configuration. Historical
# embeddings lack this fingerprint, so they are not mixed into the new release.
model_hash=hashlib.sha256()
for name,tensor in sorted(encoder.model.state_dict().items()):
    model_hash.update(name.encode());model_hash.update(str(tuple(tensor.shape)).encode());model_hash.update(str(tensor.dtype).encode())
    model_hash.update(tensor.detach().cpu().contiguous().view(__import__('torch').uint8).numpy().tobytes())
identity={'model':'BAAI/bge-m3','revision':encoder.revision,'loaded_state_sha256':model_hash.hexdigest(),
    'tokenizer_sha256':hashlib.sha256(encoder.tok.backend_tokenizer.to_str().encode()).hexdigest(),
    'normalize':True,'max_doc_tokens':8192,'half_precision':encoder.half,
    'modules':str(encoder.model),'pooling':encoder.model[1].get_config_dict()}
write_json(RUN/'embedding_identity.json',identity)
started=time.perf_counter();vectors=encoder.encode_docs(all_chunks)
if vectors.shape!=(len(all_chunks),1024) or not np.isfinite(vectors).all() or encoder.truncated:
    raise ValueError('Incomplete/non-finite/truncated final embeddings')
if not np.allclose(np.linalg.norm(vectors,axis=1),1,atol=.002):raise ValueError('Embedding norms are invalid')
embedding_seconds=round(time.perf_counter()-started,3);offset=0
for (source,candidate),length in zip(specs,lengths):
    chunks=all_chunks[offset:offset+length];destination=candidate/'emb/bge-m3';destination.mkdir(parents=True)
    np.save(destination/'doc.npy',vectors[offset:offset+length]);offset+=length
    write_json(destination/'chunk_ids.json',[c['chunk_id'] for c in chunks])
    write_json(destination/'meta.json',{'model_key':'bge-m3','model':'BAAI/bge-m3','revision':encoder.revision,
        'dim':1024,'n_chunks':length,'similarity':'cosine','chunks_sha256':sha256(candidate/'chunks.jsonl'),
        'truncated_inputs':0,'non_finite_values':0,'device':encoder.device,'local_only':True,
        'embedding_identity':identity,'created_at':timestamp(),'embed_seconds':embedding_seconds,
        'fresh_full_corpus_embedding':True})
    write_json(candidate/'search_examples.json',[])
status.update(stage='release_validation',embedding_seconds=embedding_seconds);write_json(RUN/'completion_progress.json',status)
print('Composing and validating final source-bound release',flush=True)
manifest=compose(specs,RELEASE);bundle=ReleaseBundle(RELEASE)
chunks,stored_vectors,meta=load_index('bge-m3',Path(manifest['candidate']))
if len(chunks)!=len(all_chunks) or stored_vectors.shape!=vectors.shape or not np.array_equal(stored_vectors,vectors):
    raise ValueError('Composed embeddings lost/reordered chunks')
units={k:v for k,v in bundle.units.items()};exclusions=read_jsonl(Path(manifest['candidate'])/'chunk_exclusions.jsonl')
by_product={}
for product in sorted({c['product_id'] for c in chunks}):
    own=[c for c in chunks if c['product_id']==product]
    by_product[product]={'documents':len({c['version_id'] for c in own}),'chunks':len(own),'vectors':len(own)}
quality={'review_required_units':sum(bool(u.get('review_required')) for u in units.values()),
    'description_review_required_units':sum(bool(u.get('description_review_required')) for u in units.values()),
    'excluded_units':len(exclusions),'exclusion_reasons':dict(Counter(r for e in exclusions for r in e['reasons']))}
report={'completed_at':timestamp(),'primary_objective':'Approved corpus preprocessing through embeddings',
    'status':'complete_with_explicit_quality_review','execution_complete':True,'documents':coverage,
    'approved_documents':len(eligible),'protected_excluded_documents':len(protected),'parsed_pages':sum(d['parsed_pages'] for d in coverage),
    'release':str(RELEASE),'release_manifest_sha256':sha256(RELEASE/'release_manifest.json'),
    'candidate':manifest['candidate'],'chunks':len(chunks),'vectors':len(stored_vectors),'dimension':meta['dim'],
    'all_vectors_finite':True,'all_vectors_normalized':True,'truncated_inputs':0,'all_chunk_ids_aligned':True,
    'by_product':by_product,'source_grid_tables':plans,'source_grid_recovered':len(plans),
    'source_grid_cells':sum(p['cells'] for p in plans),'quality_review':quality,
    'tests':{k:verification[k] for k in ('tests_run','passed','failures','errors')},
    'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0,
    'new_embedding_inputs':len(chunks),'embedding_identity':identity,
    'pipeline_contract':'docs/preprocessing/PIPELINE_COMPLETION_2026-10-10.md',
    'code_sha256':verification['code_sha256'],'active_index_promoted':False,'service_started':False,
    'semantic_accuracy_certified':False,'rag_accuracy_evaluated_in_this_run':False,
    'total_seconds':round(time.perf_counter()-begin,3)}
for name,digest in verification['code_sha256'].items():
    if sha256(ROOT/name)!=digest:raise ValueError('Code changed during final corpus execution: '+name)
write_json(RUN/'preprocessing_completion.json',report)
write_json(WORK/'components.json',{'components':status['components'],'release':str(RELEASE)})
status.update(lifecycle='finished',stage='complete',completed_at=report['completed_at']);write_json(RUN/'completion_progress.json',status)
print(json.dumps({k:report[k] for k in ('execution_complete','approved_documents','parsed_pages','chunks','vectors','source_grid_recovered','source_grid_cells','by_product','quality_review','total_seconds')},ensure_ascii=False),flush=True)
