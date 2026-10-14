"""Versioned context update with exact-input/fingerprinted embedding reuse."""
import json,sys,os,hashlib,time
from pathlib import Path
from copy import deepcopy
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
from preprocess.hybrid import read_jsonl,save_outputs
from preprocess.hybrid_validate import validate_extraction
from preprocess.table_caption_ownership import separate
from preprocess.table_context import assemble
from preprocess.planned_enrichment import snapshot
from preprocess.ingest import sha256,stable_hash,Document
from preprocess.inventory import write_json
from preprocess.workflow import timestamp
from rag.preprocessed_chunks import write
from rag import build_chunks as bc
from rag.embed_index import Embedder
from rag.search import load_index,filter_idx
from rag.release import compose,ReleaseBundle
import numpy as np
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
BASE=ROOT/'data/rag/releases/preprocessing_completion_2026-10-10_v2'
RUN=ROOT/'data/runs/rag_context_update_2026-10-10_v1'
CAND=ROOT/'data/rag/candidates/rag_context_update_2026-10-10_v1'
RELEASE=ROOT/'data/rag/releases/rag_context_update_2026-10-10_v1'
WORK=ROOT/'data/preprocessed/rag_context_update_2026-10-10_v1'
if RELEASE.exists() or CAND.exists():raise ValueError('Versioned outputs already exist')
RUN.mkdir(parents=True,exist_ok=True);CAND.mkdir(parents=True)
start=time.perf_counter();manifest=read(BASE/'release_manifest.json');before_hash=sha256(BASE/'release_manifest.json')
base_chunks,base_vectors,meta=load_index('bge-m3',BASE/'candidate');expected_identity=meta['embedding_identity']
old_inputs={c['header']+'\n'+c['text']:i for i,c in enumerate(base_chunks)}
status={'stage':'rechunking','started_at':timestamp(),'components':[],'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0}
write_json(RUN/'progress.json',status)
counter=bc.Counter('BAAI/bge-m3',local_only=True);specs=[];all_chunks=[];lengths=[];caption_changes=[]
for index,item in enumerate(manifest['components']):
 source=Path(item['run']); key=stable_hash([str(source),str(item['candidate'])])[:20];candidate=CAND/'components'/key
 assets=read_jsonl(source/'extracted_assets.jsonl');groups=read_jsonl(source/'visual_groups.jsonl') if (source/'visual_groups.jsonl').exists() else []
 decisions=separate(assets,groups)
 if decisions:
  prior_texts={a['asset_id']:a['selected'] for a in assets};original=source;source=WORK/'components'/key/'extraction'
  snapshot(original,source)
  m=read(source/'extraction_manifest.json');summary=read(source/'extraction_summary.json')
  assemble(assets,groups,m['grouping_version'])
  summary['caption_ownership_update']={'source':str(original),'decisions':decisions,'new_model_calls':0}
  m.pop('status_run_id',None);m['status']='extraction_complete';write_json(source/'extraction_manifest.json',m)
  save_outputs(source,assets,read_jsonl(source/'extraction_pages.jsonl'),[Document(**d) for d in m['documents']],summary,groups)
  if {a['asset_id']:a['selected'] for a in read_jsonl(source/'extracted_assets.jsonl')}!=prior_texts:raise ValueError('Caption update changed transcription')
  validate_extraction(source);caption_changes.extend(decisions)
 summary=write(source,candidate,counter=counter);chunks=read_jsonl(candidate/'chunks.jsonl')
 all_chunks.extend(chunks);lengths.append(len(chunks));specs.append((source,candidate))
 status['components'].append({'run':str(source),'candidate':str(candidate),'chunks':len(chunks),'caption_changes':len(decisions)})
 write_json(RUN/'progress.json',status)
 print(json.dumps({'component':index+1,'total':len(manifest['components']),'chunks':len(chunks),'caption_changes':len(decisions)}),flush=True)
status['stage']='embedding_identity_check';write_json(RUN/'progress.json',status)
encoder=Embedder('bge-m3',device='cuda',local_only=True);h=hashlib.sha256()
import torch
for name,tensor in sorted(encoder.model.state_dict().items()):
 h.update(name.encode());h.update(str(tuple(tensor.shape)).encode());h.update(str(tensor.dtype).encode())
 h.update(tensor.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
identity={'model':'BAAI/bge-m3','revision':encoder.revision,'loaded_state_sha256':h.hexdigest(),
 'tokenizer_sha256':hashlib.sha256(encoder.tok.backend_tokenizer.to_str().encode()).hexdigest(),
 'normalize':True,'max_doc_tokens':8192,'half_precision':encoder.half,'modules':str(encoder.model),
 'pooling':encoder.model[1].get_config_dict()}
if identity!=expected_identity:raise ValueError('Loaded embedding model differs; refuse vector reuse')
vectors=np.empty((len(all_chunks),1024),dtype=np.float32);fresh=[];reused=0
for i,c in enumerate(all_chunks):
 old=old_inputs.get(c['header']+'\n'+c['text'])
 if old is None:fresh.append(i)
 else:vectors[i]=base_vectors[old];reused+=1
status.update(stage='embedding_changed_inputs',new_embedding_inputs=len(fresh),reused_vectors=reused);write_json(RUN/'progress.json',status)
print(json.dumps({'new_embedding_inputs':len(fresh),'reused_vectors':reused}),flush=True)
embedding_start=time.perf_counter()
if fresh:vectors[fresh]=encoder.encode_docs([all_chunks[i] for i in fresh])
if encoder.truncated or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors,axis=1),1,atol=.002):raise ValueError('Invalid embeddings')
offset=0
for (source,candidate),length in zip(specs,lengths):
 out=candidate/'emb/bge-m3';out.mkdir(parents=True);own=all_chunks[offset:offset+length]
 np.save(out/'doc.npy',vectors[offset:offset+length]);offset+=length
 write_json(out/'chunk_ids.json',[c['chunk_id'] for c in own])
 write_json(out/'meta.json',{**{k:v for k,v in meta.items() if k not in ('created_at','n_chunks','chunks_sha256')},
  'created_at':timestamp(),'n_chunks':length,'chunks_sha256':sha256(candidate/'chunks.jsonl'),
  'embedding_identity':identity,'fresh_full_corpus_embedding':False,'exact_input_vector_reuse':True,
  'reuse_source_manifest_sha256':before_hash,'truncated_inputs':0})
 write_json(candidate/'search_examples.json',[])
status['stage']='release_validation';write_json(RUN/'progress.json',status)
m=compose(specs,RELEASE);bundle=ReleaseBundle(RELEASE);chunks,check,_=load_index('bge-m3',Path(m['candidate']))
if not np.array_equal(check,vectors):raise ValueError('Composed vector order changed')
plan=read(ROOT/'data/runs/corpus_update_2026-10-09/corpus_plan.json')
approved={(d['document_id'],d['version_id']) for d in plan['documents']};held={d['version_id'] for d in plan['protected_exclusions']}
if {(c['document_id'],c['version_id']) for c in chunks}!=approved or any(c['version_id'] in held for c in chunks):raise ValueError('Scope changed')
if sha256(BASE/'release_manifest.json')!=before_hash:raise ValueError('Baseline changed')
names=[str(p.relative_to(ROOT)).replace('\\','/') for f in ('rag','preprocess','eval') for p in (ROOT/f).glob('*.py')]
default=set(map(int,filter_idx(chunks)))
receipt={'execution_complete':True,'release':str(RELEASE),'release_manifest_sha256':sha256(RELEASE/'release_manifest.json'),
 'baseline_manifest_sha256':before_hash,'embedding_identity':identity,'chunks':len(chunks),'vectors':len(check),
 'approved_documents':len(approved),'protected_excluded_documents':len(held),'parsed_pages':len(bundle.pages),
 'new_embedding_inputs':len(fresh),'reused_vectors':reused,'reuse_rule':'exact header + newline + text and identical loaded model/tokenizer/pooling/config',
 'caption_changes':caption_changes,'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0,
 'embedding_seconds':round(time.perf_counter()-embedding_start,3),'completed_at':timestamp(),
 'code_sha256':{n:sha256(ROOT/n) for n in names},'default_chunks':len(default),
 'by_product':{p:{'documents':len({c['version_id'] for c in chunks if c['product_id']==p}),
  'chunks':sum(c['product_id']==p for c in chunks),'default_chunks':sum(chunks[i]['product_id']==p for i in default)} for p in sorted({c['product_id'] for c in chunks})},
 'elapsed_seconds':round(time.perf_counter()-start,3),'active_index_promoted':False,'service_started':False}
write_json(RUN/'completion.json',receipt);status.update(stage='complete',completed_at=timestamp());write_json(RUN/'progress.json',status)
print(json.dumps({k:receipt[k] for k in ('chunks','approved_documents','new_embedding_inputs','reused_vectors','default_chunks','by_product','elapsed_seconds')},ensure_ascii=False),flush=True)
