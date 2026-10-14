"""Frozen development check: exact query receipts, fresh source-ID API answers.

Independent transfer questions are a separate frozen phase, never selected by
this runner's outcomes. Protected documents and answer gold never enter calls.
"""
import argparse,json,shutil,sys,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
import numpy as np
from rag.eval_contract_stage2 import read,write,sha,BASE,RELEASE
from rag.openai_chat import EvaluationAPIChat
from rag.pipeline import RAGPipeline
from rag import selection_candidate
from rag.retrieval_candidate import retrieve
from preprocess.hybrid_config import load_config

RUN=ROOT/'data/runs/rag_bounded_candidate_2026-10-11_v1'
OUT=RUN/'api_eval'
IDS=['Q001','Q003','Q009','Q012','D08','T05','Q038','Q022','Q035','Q049','Q028','Q030','Q031','Q056','Q057','Q058']
CAP=24


class SavedQueryVectors:
    def __init__(self):
        src=ROOT/'data/runs/rag_retrieval_cycle_2026-10-11_v1'
        self.values=dict(zip(read(src/'queries.json'),np.load(src/'query_vectors.npy')))
    def encode_queries(self,queries):return np.stack([self.values[q] for q in queries])


def frozen(prepared):
    assert all(sha(ROOT/n)==h for n,h in prepared['code_sha256'].items()),'code changed after freeze'
    assert sha(RELEASE/'candidate/chunks.jsonl')==prepared['chunks_sha256']
    assert sha(RELEASE/'release_manifest.json')==prepared['release_manifest_sha256']
    assert sha(RUN/'cases.json')==prepared['cases_sha256']


def prepare():
    if (RUN/'prepared.json').exists():frozen(read(RUN/'prepared.json'));return
    RUN.mkdir(parents=True,exist_ok=True);OUT.mkdir(exist_ok=True)
    cases=[c for c in read(BASE/'cases.json') if c['id'] in IDS]
    write(RUN/'cases.json',cases)
    query_cache=OUT/'api_calls';query_cache.mkdir(exist_ok=True)
    receipts=[]
    for response in (BASE/'openai_eval/api_calls').glob('*.response.json'):
        saved=read(response)
        if saved.get('case') not in IDS or saved.get('schema')!='query_understanding' or saved.get('status')!='completed':continue
        request=response.with_name(response.name.replace('.response.','.request.'))
        if not request.exists():continue
        for path in (response,request):
            dest=query_cache/path.name;shutil.copyfile(path,dest)
            receipts.append({'source':str(path.relative_to(ROOT)),'sha256':sha(path)})
    hashes={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for folder in ('rag','tests') for p in (ROOT/folder).glob('*.py')}
    tests=ROOT/'data/runs/rag_retrieval_cycle_2026-10-11_v1/full_tests.txt'
    assert tests.read_text(encoding='utf-8').strip().endswith('OK')
    prior=read(BASE/'prepared.json')
    prepared={'version':'bounded-candidate-eval-1','cases':IDS,'code_sha256':hashes,
        'chunks_sha256':sha(RELEASE/'candidate/chunks.jsonl'),'release_manifest_sha256':sha(RELEASE/'release_manifest.json'),
        'cases_sha256':sha(RUN/'cases.json'),'protected_excluded':prior['protected_excluded'],
        'fresh_api_call_cap':CAP,'context_tokens':24576,'answer_tokens':3000,
        'query_cache_receipts':receipts,'document_embedding_reused':True,'query_vectors_reused':True,
        'gold_sent':False,'automatic_http_retries':0,'independent_holdout':False,
        'evaluation_scope':'Selected development failures, regression sentinels and three missing-field controls; not full 69-case accuracy',
        'tests_path':str(tests.relative_to(ROOT)),'tests_sha256':sha(tests),'test_count':int(re.search(r'Ran (\d+) tests',tests.read_text(encoding='utf-8'))[1]),
        'live_service_switched':False,'plain_reranker_not_adopted':True}
    for name in hashes:
        dest=RUN/'code_snapshot'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/name).read_bytes())
    write(RUN/'prepared.json',prepared);frozen(prepared)
    print(json.dumps({'prepared':True,'cases':len(cases),'cap':CAP,'cached_query_receipts':len(receipts)}),flush=True)


def run(limit=None):
    prepared=read(RUN/'prepared.json');frozen(prepared)
    client=EvaluationAPIChat(OUT,max_new_calls=CAP);client.config.num_ctx=prepared['context_tokens']
    pipeline=RAGPipeline(RELEASE,RELEASE/'candidate',load_config(ROOT/'configs/preprocess.local.json'),
        chat_client=client,encoder_factory=SavedQueryVectors,retrieval_pages=10,retrieval_fn=retrieve,selection_strategy=selection_candidate)
    cases=read(RUN/'cases.json');new=0
    try:
        for case in cases:
            frozen(prepared);path=OUT/'responses'/(case['id']+'.json')
            if path.exists():continue
            if limit is not None and new>=limit:break
            client.case=case['id'];payload=case['payload']
            write(RUN/'progress.json',{'stage':'evaluating','case':case['id'],'new_api_attempts':client.attempts,
                'completed':len(list((OUT/'responses').glob('*.json'))) if (OUT/'responses').exists() else 0})
            print('start',case['id'],flush=True)
            result=pipeline.ask(payload['question'],payload.get('products'),payload.get('jurisdictions'),payload.get('documents'),
                include_nondefault=payload.get('include_nondefault',False))
            result['candidate_versions']={'retrieval':retrieve.__module__,'selection':selection_candidate.VERSION,'page_budget':10}
            assert all(b['version_id'] not in prepared['protected_excluded'] for u in result['results'] for b in u.get('evidence',{}).values())
            write(path,result);new+=1
            print(json.dumps({'case':case['id'],'statuses':[u['status'] for u in result['results']],
                'items':sum(len(u['items']) for u in result['results']),'new_api_attempts':client.attempts,
                'seconds':result.get('seconds'),'errors':[u.get('error') for u in result['results'] if u.get('error')]}),flush=True)
            if client.halted or any(u['status']=='error' for u in result['results']):
                raise RuntimeError('Evaluation error preserved; no automatic repetition')
        completed=len(list((OUT/'responses').glob('*.json')))
        write(RUN/'progress.json',{'stage':'completed' if completed==len(cases) else 'batch_completed','completed':completed,'total':len(cases),'new_api_attempts':client.attempts,'semantic_review_pending':True})
    finally:pipeline.close();client.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',action='store_true');parser.add_argument('--limit',type=int);args=parser.parse_args()
    prepare() if args.prepare else run(args.limit)
