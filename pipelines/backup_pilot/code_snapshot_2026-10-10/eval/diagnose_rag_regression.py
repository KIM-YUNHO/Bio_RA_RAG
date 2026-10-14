"""Read-only historical comparison and paired retrieval audit of the SB17 regression."""
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
import numpy as np
from eval.current_server_eval import read, write, digest, request
from eval.review_pipeline import cases_for
from rag.search import Bm25, load_index, filter_idx, dense_hits, rrf, to_pages
from rag import study_search
from rag.embed_index import Embedder
from rag.answer import retrieve
from types import SimpleNamespace


def rows(path):
    return [json.loads(line) for line in path.open(encoding='utf-8') if line.strip()]


def main():
    out=ROOT/'data/runs/rag_regression_diagnosis_2026-10-09'
    out.mkdir(parents=True,exist_ok=True)
    live=request('http://127.0.0.1:8765','/api/summary')
    products=rows(ROOT/'data/catalog/products.jsonl')
    docs=rows(ROOT/'data/catalog/documents.jsonl')
    with sqlite3.connect('file:'+str(ROOT/'data/collection.sqlite').replace('\\','/')+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        counts=[{'product_id':p,'documents':n,'pages':pages} for p,n,pages in db.execute(
            'select product_id,count(*),sum(page_count) from document_versions group by product_id')]
        db_products=[p for p, in db.execute('select product_id from products')]
    original,ov,om=load_index('bge-m3',ROOT/'data/rag')
    current,cv,cm=load_index('bge-m3',ROOT/'data/rag/candidates/pipeline_review_2026-10-08')
    assert cm['chunks_sha256']==live['chunks_sha256']
    manifest=read(ROOT/'data/preprocessed/inventory_v2_2026-10-08/inventory_summary.json')
    inventory={'checked_at':datetime.now(timezone.utc).isoformat(),
        'products':[{k:p.get(k) for k in ('product_id','kind','ema_name','us_brand','reference_product_id')} for p in products],
        'db_products':db_products,'database_by_product':counts,
        'catalog_by_product':dict(Counter(d.get('product_id') or 'unassigned' for d in docs)),
        'catalog_document_versions':len(docs),'catalog_sha256':digest(ROOT/'data/catalog/documents.jsonl'),
        'legacy_index':{'chunks':len(original),'documents':len({(c['document_id'],c['version_id']) for c in original}),
            'products':dict(Counter(c['product_id'] for c in original)),'sha256':om['chunks_sha256']},
        'live_server':live,'earlier_inventory_snapshot':manifest,
        'notice':'Catalog/DB, old RAG index, earlier inventory and live preprocessing candidate are distinct stages and snapshots.'}
    registry=read(ROOT/'data/reviewed/studies.json')
    inventory['query_registry']={'products':list(registry['products']),'study_count':len(registry['studies']),
        'source':'data/reviewed/studies.json','used_as_understanding_schema_enum':True}
    inventory['ema_study_metadata']={name:{'chunks':len(group),'with_study_candidates':sum(bool(c.get('studies')) for c in group),
        'basis':dict(Counter(c.get('study_basis') for c in group))} for name,group in
        [('legacy',[c for c in original if c['document_id']=='EMA:63563']),
         ('current',[c for c in current if c['document_id']=='EMA:63563'])]}
    write(out/'inventory.json',inventory)
    old=read(ROOT/'data/rag/eval/answers_v0.2.json')['rows']
    judgments={(r['question'],r['agency']):r for r in csv.DictReader(
        (ROOT/'eval/answer_judgments_v0.2.csv').open(encoding='utf-8-sig'))}
    new={r['id']:r for r in read(ROOT/'data/runs/rag_evaluation_2026-10-09/analysis.json')['rows']}
    paired=[]
    for row in old:
        if row['agency']!='EU' or row['product']!='SB17':continue
        key=row['component']; now=new[key]
        response=read(ROOT/f'data/runs/rag_evaluation_2026-10-09/responses/{key}.json')
        assert response['question']==row['ko']
        paired.append({'id':key,'question':row['ko'],'old_judgment':judgments[key,'EU'],
            'old_retrieval_hit':row['retrieval_hit'],'old_answer':row['answer_ko'],'old_evidence_pages':row['evidence'],
            'current_judgment':now['judgment'],'current_qualified_page_rank':now['qualified_page_rank'],
            'current_retrieval_observed':now['retrieval_observed'],
            'current_answer':[u['answer_ko'] for u in response['results']]})
    write(out/'paired_answers.json',{'n':len(paired),'old_model':'gpt-4o-mini (historical record)',
        'new_model':live['answer_model'],'old_verdict_counts':dict(Counter(p['old_judgment']['judgment'] for p in paired)),
        'new_verdict_counts':dict(Counter(p['current_judgment']['verdict'] for p in paired)),
        'rows':paired,'limitation':'Same14 questions and EMA source; historical judgments are preserved, not rescored under the new fact-completeness rubric. Multiple factors changed.'})
    cases=cases_for(current)
    forms=[(c,l,c[l]) for c in cases for l in ('en','ko','ko_b','ja') if l in c]
    cache=ROOT/'data/runs/pipeline_review_2026-10-08/query_vectors.npz'
    saved=np.load(cache,allow_pickle=False)
    questions=[q for _,_,q in forms]
    assert str(saved['fingerprint'])==hashlib.sha256(json.dumps(questions,ensure_ascii=False).encode()).hexdigest()
    assert str(saved['model'])==om.get('model',om.get('doc'))==cm['model']
    labels=list(csv.DictReader((ROOT/'eval/labels_manual_v0.1.csv').open(encoding='utf-8-sig')))
    banks=[('legacy_all',original,ov),('current_all',current,cv)]
    for name,chunks,vec in list(banks):
        keep=[i for i,c in enumerate(chunks) if c['document_id']=='EMA:63563']
        assert {chunks[i]['version_id'] for i in keep}=={live['documents'][0]['version_id']}
        banks.append((name.replace('_all','_same_ema'),[chunks[i] for i in keep],vec[keep]))
    results=[]
    for name,chunks,vec in banks:
        print('Paired retrieval',name,flush=True)
        bm=Bm25(chunks)
        idx=filter_idx(chunks,'SB17','assessment','EU')
        for (case,lang,question),qv in zip(forms,saved['vectors']):
            if case['suite']!='historical_qa':continue
            gold=set(case['pages'])
            qualified=gold|{int(l['pdf_page']) for l in labels if l['query_id']==case['id']
                and l['document_id']==case['document_id'] and l['grade']=='2' and l['partial']=='0'}
            b=bm.hits(question,idx); d=dense_hits(qv,vec,idx)
            methods={'bm25':to_pages(b,chunks,10,collapse=False),
                'dense':to_pages(d,chunks,10,collapse=False),
                'hybrid':to_pages(rrf([b,d]),chunks,10,collapse=False),
                'hybrid_B':study_search.search(qv,question,idx,chunks,vec,bm,['ST_SB17_1001'],'B',k=10),
                'hybrid_general':to_pages(study_search.adaptive_hits(qv,question,idx,chunks,vec,bm,
                    ['ST_SB17_1001'],['assessment']),chunks,10)}
            for mode,pages in methods.items():
                rank=lambda allowed:next((n for n,p in enumerate(pages,1) if p['document_id']==case['document_id']
                    and p['version_id']==live['documents'][0]['version_id'] and p['pdf_page'] in allowed),None)
                results.append({'bank':name,'id':case['id'],'language':lang,'mode':mode,
                    'strict_rank':rank(gold),'qualified_rank':rank(qualified),
                    'pages':[p['pdf_page'] for p in pages[:5]],'query':question})
        bm.db.close()
    buckets=defaultdict(list)
    for r in results:buckets[r['bank'],r['language'],r['mode']].append(r)
    metrics=[]
    for (bank,language,mode),group in buckets.items():
        metrics.append({'bank':bank,'language':language,'mode':mode,'n':len(group),
            **{f'{field}_hit@5':sum(r[field] is not None and r[field]<=5 for r in group) for field in ('strict_rank','qualified_rank')},
            'qualified_mrr@10':sum(1/r['qualified_rank'] for r in group if r['qualified_rank'])/len(group)})
    write(out/'paired_retrieval.json',{'metrics':metrics,'rows':results,
        'controls':'Identical14 questions x3 forms, cached BGE-M3 vectors, same EMA source hash, hard SB17/EU/assessment scope, same current search code. No generation or interpretation calls.',
        'limits':'Isolates combined extraction/chunk/index differences, not OCR alone. Original model prompts, metadata and weighting may differ from historical runs. Same-EMA banks control BM25 corpus IDF.'})
    # Counterfactual retrieval only: keep the saved interpretation/query and enforce
    # the request's explicit scope. This is a diagnostic intervention, not a server fix.
    live_cases={c['id']:c for c in read(ROOT/'data/runs/rag_evaluation_2026-10-09/cases.json')}
    reports=[(key,read(ROOT/f'data/runs/rag_evaluation_2026-10-09/responses/{key}.json'))
        for key in ('Q001','Q005','D06','D07')]
    encoder=Embedder('bge-m3',local_only=True)
    replay_vectors=encoder.encode_queries([r['understanding']['english_query'] for _,r in reports])
    del encoder
    bm=Bm25(current);replays=[]
    for (key,report),qv in zip(reports,replay_vectors):
        case=live_cases[key];payload=case['payload']
        u={**report['understanding'],'products':payload['products'],'jurisdictions':payload['jurisdictions']}
        selected=set(payload.get('documents') or [d['key'] for d in live['documents']])
        corpus=SimpleNamespace(chunks=current,vec=cv,bm=bm,
            include_nondefault=payload.get('include_nondefault',False),
            allowed_documents={(d['document_id'],d['version_id']) for d in live['documents'] if d['key'] in selected})
        found=retrieve(corpus,u,qv,k=5,policy='general',agencies=payload['jurisdictions'],products=payload['products'])
        pages=[p for _,group in found for p in group]
        gold=set(case['gold_pages'])|{int(l['pdf_page']) for l in labels if l['query_id']==key
            and l['document_id']==case['document_id'] and l['grade']=='2' and l['partial']=='0'}
        rank=next((n for n,p in enumerate(pages,1) if (p['document_id'],p['version_id'])==
            (case['document_id'],case['version_id']) and p['pdf_page'] in gold),None)
        replays.append({'id':key,'original_plan':report['plan'],'forced_scope':payload,
            'english_query_unchanged':u['english_query'],'original_pages':[p['pdf_page'] for x in report['results'] for p in x.get('retrieved_pages',[])],
            'new_plan':[unit for unit,_ in found],'new_pages':[p['pdf_page'] for p in pages],
            'qualified_gold_page_rank':rank,'answer_not_generated':True})
    bm.db.close()
    write(out/'scope_replay.json',{'rows':replays,'method':'Saved actual interpretations and English queries; only selected product/agency scope forced. Current index/search, same default/document rules, local BGE-M3 embeddings, no LLM calls.',
        'limit':'Demonstrates search recovery only, not that generated answers will be correct.'})
    assert request('http://127.0.0.1:8765','/api/summary')==live
    assert digest(ROOT/'data/rag/chunks.jsonl')==om['chunks_sha256']
    assert digest(ROOT/'data/rag/candidates/pipeline_review_2026-10-08/chunks.jsonl')==cm['chunks_sha256']
    write(out/'provenance.json',{'created_at':datetime.now(timezone.utc).isoformat(),'server_unchanged':True,
        'old_and_current_indices_unchanged':True,'no_llm_calls':True,'gold_not_added_to_indices':True,
        'sha256':{f:digest(ROOT/f) for f in ['eval/diagnose_rag_regression.py','eval/queries_v0.1.csv',
            'eval/answer_judgments_v0.2.csv','data/rag/eval/answers_v0.2.json','rag/plan_query.py',
            'rag/pipeline.py','rag/search.py','rag/study_search.py','rag/preprocessed_evidence.py',
            'rag/preprocessed_chunks.py','eval/labels_manual_v0.1.csv',
            'data/reviewed/studies.json','rag/understand_query.py',
            'data/runs/rag_evaluation_2026-10-09/analysis.json',
            'data/runs/rag_evaluation_2026-10-09/cases.json',
            'data/runs/pipeline_review_2026-10-08/query_vectors.npz']}})
    print(json.dumps({'output':str(out),'paired_questions':len(paired),'retrieval_rows':len(results),
        'scope_replay':[{'id':r['id'],'gold_rank':r['qualified_gold_page_rank']} for r in replays]},ensure_ascii=False))


if __name__=='__main__':main()
