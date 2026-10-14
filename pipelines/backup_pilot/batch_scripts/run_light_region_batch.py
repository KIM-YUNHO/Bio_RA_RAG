"""Finish ordinary region OCR, then freeze complex-only VLM and review queues."""
import json,sys,os
from pathlib import Path
from collections import Counter,defaultdict
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from preprocess.hybrid import read_jsonl
from preprocess.hybrid_config import load_config
from preprocess.ingest import sha256,stable_hash
from preprocess.inventory import write_json
from preprocess.paddle_adapter import PaddleAdapter
from preprocess.light_refinement import refine
from preprocess.workflow import CODE,timestamp,exclusive
from preprocess.hybrid_validate import validate_extraction
from collect_visual_queue import extraction_folder
RUN=ROOT/'data/runs/corpus_update_2026-10-09'
OUT=ROOT/'data/preprocessed/corpus_update_2026-10-09/light_region_completion'
OLD=ROOT/'data/preprocessed/corpus_update_2026-10-09/visual_completion'
STATUS=RUN/'light_region_completion_progress.json'

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))

def main():
    config=load_config(ROOT/'configs/preprocess.local.json')
    if config.paddle.parser_backend!='cascade':raise ValueError('Ordinary OCR backend required')
    source_state=ROOT/'data/preprocessed/corpus_update_2026-10-09/selective_corpus/corpus_progress.json'
    corpus=read(source_state);plan=read(corpus['plan_path']);protected={d['version_id'] for d in plan['protected_exclusions']}
    eligible={d['version_id'] for d in plan['documents']}
    originals=read_jsonl(RUN/'selective_visual_queue/visual_tasks.jsonl');by_source=defaultdict(list)
    for t in originals:by_source[t['extraction']].append(t['asset_id'])
    sources={}
    for src in by_source:
        earlier=OLD/'components'/stable_hash(src)[:20]/'extraction'
        source=earlier if (earlier/'extraction_integrity.json').exists() else Path(src)
        manifest=read(source/'extraction_manifest.json')
        if any(d['version_id'] in protected or d['version_id'] not in eligible for d in manifest['documents']):raise ValueError('Out of scope source')
        sources[src]={'path':str(source),'integrity_sha256':sha256(source/'extraction_integrity.json')}
    frozen={'sources':sources,'queue_sha256':sha256(RUN/'selective_visual_queue/visual_tasks.jsonl'),
        'corpus_sha256':sha256(source_state),'config':config.to_dict(),'code':{n:sha256(ROOT/n) for n in CODE},
        'runner_sha256':sha256(Path(__file__)),'api_calls':0,'new_qwen_calls':0,'automatic_promotion':False}
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'frozen.json').exists() and read(OUT/'frozen.json')!=frozen:raise ValueError('Frozen light batch changed')
    write_json(OUT/'frozen.json',frozen)
    state=read(STATUS) if STATUS.exists() else {'started_at':timestamp(),'completed_components':[], 'statistics':[]}
    def save(stage,**extra):
        state.update(stage=stage,updated_at=timestamp(),pid=os.getpid(),api_calls=0,new_qwen_calls=0,**extra);write_json(STATUS,state)
    def check():
        if any(sha256(ROOT/n)!=h for n,h in frozen['code'].items()):raise ValueError('Light OCR code changed')
    class Shared(PaddleAdapter):
        def fingerprint(self):
            if not hasattr(self,'cached'):self.cached=super().fingerprint()
            return deepcopy(self.cached)
        def ocr(self,image):check();return super().ocr(image)
    with exclusive(OUT):
        backend=Shared(config.paddle,OUT/'worker')
        try:
            for src,ids in by_source.items():
                check();dest=OUT/'components'/stable_hash(src)[:20]/'extraction'
                if src in state['completed_components']:validate_extraction(dest);continue
                if sha256(Path(sources[src]['path'])/'extraction_integrity.json')!=sources[src]['integrity_sha256']:raise ValueError('Source changed')
                save('light_region_ocr',current={'source':sources[src]['path']})
                result=refine(sources[src]['path'],dest,ids,config,backend,
                    notify=lambda **e:save('light_region_ocr',current=e))
                state['statistics'].append(result);state['completed_components'].append(src)
                save('light_region_ocr',current=None)
        finally:backend.close()
        complex_tasks=[];reviews=[];counts=Counter();components=[]
        for source,candidate in corpus['components']:
            src=str(extraction_folder(source));dest=OUT/'components'/stable_hash(src)[:20]/'extraction'
            final=dest if src in by_source else Path(src)
            components.append({'source':str(final),'previous_candidate':candidate,'candidate_needs_refresh':src in by_source})
            for a in read_jsonl(final/'extracted_assets.jsonl'):
                stage=a['route'].get('next_stage');counts[stage or 'preserved']+=1
                if a['route']['task'] and stage=='qwen' and (a.get('enrichment') or {}).get('status') not in {'heuristic_checks_passed','needs_review','complete'}:
                    complex_tasks.append({k:a[k] for k in ('asset_id','document_id','version_id','pdf_page','bbox_pdf','bbox_input_px','source_refs','route')}
                        |{'extraction':str(final),'input_state_sha256':stable_hash([a['base'],a['native_reference'],a.get('light_region_ocr')]),
                          'numeric_evidence_eligible':False})
                if stage in {'review','light_ocr'} or (a.get('enrichment') or {}).get('status')=='needs_review':
                    reviews.append({'extraction':str(final),'asset_id':a['asset_id'],'document_id':a['document_id'],
                        'page':a['pdf_page'],'route':a['route'],'enrichment_status':(a.get('enrichment') or {}).get('status')})
        write_json(OUT/'components.json',components);write_json(OUT/'complex_visual_tasks.json',complex_tasks)
        write_json(OUT/'review_tasks.json',reviews)
        save('light_ocr_complete_complex_queue_pending',current=None,routes=dict(counts),
            complex_regions=len(complex_tasks),review_regions=len(reviews),
            next_step='Inspect complex-only queue and source quality before VLM execution; regenerate changed chunks',
            rag_evaluation_is_independent=True,protected_exclusions=len(protected))

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        prior=read(STATUS) if STATUS.exists() else {}
        write_json(STATUS,{**prior,'stage':'requires_attention','error':f'{type(exc).__name__}: {exc}','updated_at':timestamp()})
        raise
