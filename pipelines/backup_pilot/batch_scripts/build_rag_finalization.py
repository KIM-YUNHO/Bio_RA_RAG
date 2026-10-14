"""Rebuild only components with newly recognized study boundaries; preserve prior release."""
import sys,os,json,hashlib,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
from preprocess.hybrid import read_jsonl
from preprocess.ingest import sha256
from preprocess.inventory import write_json
from preprocess.workflow import timestamp
from rag import document_structure as ds,build_chunks as bc
from rag.preprocessed_chunks import write
from rag.search import load_index,filter_idx
from rag.embed_index import Embedder
from rag.release import compose,ReleaseBundle
import numpy as np
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
BASE=ROOT/'data/rag/releases/rag_context_update_2026-10-10_v1'
RUN=ROOT/'data/runs/rag_finalization_2026-10-10_v1'
CAND=ROOT/'data/rag/candidates/rag_finalization_2026-10-10_v1'
RELEASE=ROOT/'data/rag/releases/rag_finalization_2026-10-10_v1'
if CAND.exists() or RELEASE.exists():raise ValueError('Versioned outputs already exist')
RUN.mkdir(parents=True,exist_ok=True);CAND.mkdir(parents=True)
start=time.perf_counter();manifest=read(BASE/'release_manifest.json');before=sha256(BASE/'release_manifest.json')
chunks,base_vectors,meta=load_index('bge-m3',BASE/'candidate');old_inputs={c['header']+'\n'+c['text']:i for i,c in enumerate(chunks)}
status={'stage':'scanning_changed_heading_contract','started_at':timestamp(),'components':[],
 'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0}
write_json(RUN/'progress.json',status);counter=None;specs=[];all_chunks=[];lengths=[];rebuilt=[]
for index,item in enumerate(manifest['components']):
 source=Path(item['run']);candidate=Path(item['candidate']);m=read(source/'extraction_manifest.json')
 products={(d['document_id'],d['version_id']):d['metadata'].get('product_id') for d in m['documents']}
 headings=[]
 for unit in read_jsonl(source/'source_units.jsonl'):
  if unit['kind']!='text' or unit.get('structure',{}).get('table_id'):continue
  for line in unit['text'].splitlines():
   h=ds.identify(line.strip(),products[(unit['document_id'],unit['version_id'])],version=ds.VERSION)
   if h and h['kind']=='study_boundary':headings.append({'unit_id':unit['unit_id'],'title':h['title']})
 if headings:
  if counter is None:counter=bc.Counter('BAAI/bge-m3',local_only=True)
  candidate=CAND/'components'/f'{index+1:03d}';write(source,candidate,counter=counter)
  rebuilt.append({'run':str(source),'candidate':str(candidate),'boundary_headings':headings})
 own=read_jsonl(candidate/'chunks.jsonl');all_chunks.extend(own);lengths.append(len(own));specs.append((source,candidate))
 status['components'].append({'component':index+1,'rebuilt':bool(headings),'chunks':len(own),'boundary_headings':headings})
 write_json(RUN/'progress.json',status)
 if headings:print(json.dumps({'component':index+1,'rebuilt':True,'chunks':len(own),'new_boundaries':len(headings)}),flush=True)
encoder=Embedder('bge-m3',device='cuda',local_only=True);h=hashlib.sha256()
import torch
for name,tensor in sorted(encoder.model.state_dict().items()):
 h.update(name.encode());h.update(str(tuple(tensor.shape)).encode());h.update(str(tensor.dtype).encode())
 h.update(tensor.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
identity={'model':'BAAI/bge-m3','revision':encoder.revision,'loaded_state_sha256':h.hexdigest(),
 'tokenizer_sha256':hashlib.sha256(encoder.tok.backend_tokenizer.to_str().encode()).hexdigest(),
 'normalize':True,'max_doc_tokens':8192,'half_precision':encoder.half,'modules':str(encoder.model),'pooling':encoder.model[1].get_config_dict()}
if identity!=meta['embedding_identity']:raise ValueError('Loaded model fingerprint differs')
fresh=[];vectors=np.empty((len(all_chunks),1024),dtype=np.float32)
for i,c in enumerate(all_chunks):
 old=old_inputs.get(c['header']+'\n'+c['text'])
 if old is None:fresh.append(i)
 else:vectors[i]=base_vectors[old]
status.update(stage='embedding_changed_inputs',new_embedding_inputs=len(fresh),reused_vectors=len(all_chunks)-len(fresh));write_json(RUN/'progress.json',status)
print(json.dumps({'rebuilt_components':len(rebuilt),'new_embedding_inputs':len(fresh),'reused_vectors':len(all_chunks)-len(fresh)}),flush=True)
if fresh:vectors[fresh]=encoder.encode_docs([all_chunks[i] for i in fresh])
if encoder.truncated or not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors,axis=1),1,atol=.002):raise ValueError('Embedding validation failed')
rebuilt_paths={Path(r['candidate']) for r in rebuilt};offset=0
for (_,candidate),length in zip(specs,lengths):
 own=all_chunks[offset:offset+length]
 if candidate in rebuilt_paths:
  folder=candidate/'emb/bge-m3';folder.mkdir(parents=True);np.save(folder/'doc.npy',vectors[offset:offset+length])
  write_json(folder/'chunk_ids.json',[c['chunk_id'] for c in own])
  write_json(folder/'meta.json',{**meta,'created_at':timestamp(),'n_chunks':length,'chunks_sha256':sha256(candidate/'chunks.jsonl'),
   'embedding_identity':identity,'truncated_inputs':0,'composition':None,'components':None,
   'exact_input_vector_reuse':True,'reuse_source_manifest_sha256':before,'fresh_full_corpus_embedding':False})
  write_json(candidate/'search_examples.json',[])
 offset+=length
status['stage']='release_validation';write_json(RUN/'progress.json',status)
result=compose(specs,RELEASE);bundle=ReleaseBundle(RELEASE);check_chunks,check_vectors,_=load_index('bge-m3',RELEASE/'candidate')
if not np.array_equal(check_vectors,vectors):raise ValueError('Vector order differs')
plan=read(ROOT/'data/runs/corpus_update_2026-10-09/corpus_plan.json');approved={(d['document_id'],d['version_id']) for d in plan['documents']}
held={d['version_id'] for d in plan['protected_exclusions']}
if {(c['document_id'],c['version_id']) for c in check_chunks}!=approved or any(c['version_id'] in held for c in check_chunks):raise ValueError('Scope changed')
if sha256(BASE/'release_manifest.json')!=before:raise ValueError('Prior release changed')
default=set(map(int,filter_idx(check_chunks)));names=[str(p.relative_to(ROOT)).replace('\\','/') for f in ('rag','preprocess','eval','rag_service') for p in (ROOT/f).glob('*.py')]
receipt={'execution_complete':True,'completed_at':timestamp(),'release':str(RELEASE),'release_manifest_sha256':sha256(RELEASE/'release_manifest.json'),
 'baseline_manifest_sha256':before,'chunks':len(check_chunks),'vectors':len(check_vectors),'approved_documents':len(approved),'protected_excluded_documents':len(held),
 'parsed_pages':len(bundle.pages),'new_embedding_inputs':len(fresh),'reused_vectors':len(all_chunks)-len(fresh),'rebuilt_components':rebuilt,
 'default_chunks':len(default),'new_ocr_calls':0,'new_qwen_calls':0,'elapsed_seconds':round(time.perf_counter()-start,3),
 'by_product':{p:{'documents':len({c['version_id'] for c in check_chunks if c['product_id']==p}),'chunks':sum(c['product_id']==p for c in check_chunks),
  'default_chunks':sum(check_chunks[i]['product_id']==p for i in default)} for p in sorted({c['product_id'] for c in check_chunks})},
 'code_sha256':{n:sha256(ROOT/n) for n in names},'embedding_identity':identity,'active_index_promoted':False,'service_started':False}
write_json(RUN/'completion.json',receipt);status.update(stage='complete',completed_at=timestamp());write_json(RUN/'progress.json',status)
print(json.dumps({k:receipt[k] for k in ('chunks','new_embedding_inputs','reused_vectors','default_chunks','elapsed_seconds')},ensure_ascii=False),flush=True)
