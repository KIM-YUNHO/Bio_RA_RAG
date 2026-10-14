"""Checkpointed RA3 corpus processing; no API calls and no active-index writes."""
import argparse,json,os,time
import shutil
from pathlib import Path
from copy import deepcopy
from .ingest import sha256
from .inventory import write_json
from .hybrid_config import load_config
from .paddle_adapter import PaddleAdapter
from .workflow import run,build_index,timestamp,CODE,ROOT,exclusive
from .subset import create


class SharedParser(PaddleAdapter):
    """Keep the serial Paddle worker loaded across document boundaries."""
    def fingerprint(self):
        if not hasattr(self,'_fingerprint'):self._fingerprint=super().fingerprint()
        return deepcopy(self._fingerprint)
    def close(self):pass
    def finish(self):super().close()
    def _receive(self):
        try:return super()._receive()
        except (TimeoutError,RuntimeError):
            super().close();raise


def process(plan_path,out,config,reuse_corpus=None):
    plan_path,out=Path(plan_path).resolve(),Path(out).resolve()
    plan=json.loads(plan_path.read_text(encoding='utf-8'))
    if plan['scope']!=['EMA','FDA','Health Canada']:raise ValueError('Unexpected corpus scope')
    protected={d['version_id'] for d in plan['protected_exclusions']}
    if any(d.get('protected') or d['version_id'] in protected for d in plan['documents']):raise ValueError('Protected source entered the corpus plan')
    out.mkdir(parents=True,exist_ok=True)
    code={name:sha256(ROOT/name) for name in CODE}
    state_path=out/'corpus_progress.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {
        'started_at':timestamp(),'plan_path':str(plan_path),'plan_sha256':sha256(plan_path),'code_sha256':code,
        'total_documents':len(plan['documents']),'documents':{},'components':[],'stage':'preparing',
        'active_index_updated':False,'protected_exclusions':len(plan['protected_exclusions']),
        'reuse_corpus':str(Path(reuse_corpus).resolve()) if reuse_corpus else None}
    if state['plan_sha256']!=sha256(plan_path) or state['code_sha256']!=code:raise ValueError('Corpus plan or code changed; use a new run')
    if state.get('reuse_corpus')!=(str(Path(reuse_corpus).resolve()) if reuse_corpus else None):raise ValueError('Reuse source changed')
    def save():
        state['updated_at']=timestamp();write_json(state_path,state)
    def frozen():
        if any(sha256(ROOT/n)!=h for n,h in code.items()):raise ValueError('Code changed during corpus processing')
    with exclusive(out):
        frozen()
        for name in code:
            dest=out/'code'/name;dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists():shutil.copyfile(ROOT/name,dest)
        base=out/'sb17_base'
        ids=['EMA:63563','FDA:761373Orig1s000,761425Orig1s000:MultidisciplineR','HC:PM:00082519']
        if not (base/'extraction').exists():create(ROOT/'data/preprocessed/pipeline_review_2026-10-08',base/'extraction',ids)
        from rag.preprocessed_chunks import write
        if not (base/'candidate').exists():write(base/'extraction',base/'candidate')
        delta=ROOT/'data/preprocessed/corpus_update_2026-10-09/current_delta'
        if not (delta/'finished.json').is_file():raise ValueError('Finish current remaining OCR before the corpus batch')
        reused={d['version_id']:folder for folder in (base,delta) for d in json.loads((folder/('extraction' if folder==base else 'labels')/'extraction_manifest.json').read_text(encoding='utf-8'))['documents']}
        components=[(str(base/'extraction'),str(base/'candidate')),(str(delta/'labels'),str(delta/'candidate'))]
        from .selective_reuse import collect
        prior_complete=collect(reuse_corpus,plan,out) if reuse_corpus else {}
        parser=SharedParser(config.paddle,out/'shared_paddle_worker')
        state.update(stage='extracting',lifecycle='running');save()
        try:
            for index,doc in enumerate(plan['documents'],1):
                frozen();key=doc['version_id'];prior=state['documents'].get(key,{})
                if key in reused:
                    state['documents'][key]={'document_id':doc['document_id'],'product_id':doc['product_id'],
                        'status':'reused_complete_page_extraction','pages':doc.get('page_count'),'run':str(reused[key])};save();continue
                if key in prior_complete:
                    item=prior_complete[key];state['documents'][key]=item
                    components.append((item['extraction'],item['candidate']));save();continue
                folder=out/'documents'/key[:20]
                state['current_document']={'index':index,'document_id':doc['document_id'],'product_id':doc['product_id'],'run':str(folder)};save()
                if prior.get('status')=='extracted':
                    components.append((str(folder),str(folder/'candidate')));continue
                print(f'Corpus {index}/{len(plan["documents"])}: {doc["document_id"]}',flush=True)
                try:
                    result=run(folder,config,catalog=None if (folder/'workflow_manifest.json').is_file() else ROOT/'data/catalog/documents.jsonl',
                        document_ids=None if (folder/'workflow_manifest.json').is_file() else [doc['document_id']],
                        resume=(folder/'workflow_manifest.json').is_file(),use_qwen=False,label_ocr=False,embedding=False,paddle=parser)
                    state['documents'][key]={'document_id':doc['document_id'],'product_id':doc['product_id'],'status':'extracted',
                        'pages':result['documents'],'chunks':result['chunks'],'content_state':result['content_state'],'run':str(folder)}
                    components.append((str(folder),str(folder/'candidate')))
                except Exception as exc:
                    state['documents'][key]={'document_id':doc['document_id'],'product_id':doc['product_id'],'status':'failed',
                        'error':type(exc).__name__+': '+str(exc),'run':str(folder)}
                save()
        finally:parser.finish()
        state.update(stage='embedding',components=components);save();frozen()
        from rag.embed_index import Embedder
        encoder=Embedder('bge-m3',device='cuda',local_only=True)
        try:
            for source,candidate in components:
                candidate=Path(candidate);meta=candidate/'emb/bge-m3/meta.json'
                if meta.exists() and json.loads(meta.read_text(encoding='utf-8')).get('chunks_sha256')==sha256(candidate/'chunks.jsonl'):continue
                print('Corpus embedding: '+str(candidate),flush=True)
                build_index(candidate,[],device='cuda',include_nondefault=False,factory=lambda *args,**kwargs:encoder)
        finally:
            del encoder
            import gc,torch
            gc.collect()
            if torch.cuda.is_available():torch.cuda.empty_cache()
        frozen();state.update(stage='ready_to_compose',lifecycle='finished',finished_at=timestamp(),components=components);save()
        return state


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--config',type=Path,default=Path('configs/preprocess.local.json'))
    p.add_argument('--reuse-corpus',type=Path);args=p.parse_args()
    process(args.plan,args.out,load_config(args.config),reuse_corpus=args.reuse_corpus)


if __name__=='__main__':main()
