"""Paired retrieval for a metadata-only change, using frozen cached vectors."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
import numpy as np
from eval.review_pipeline import cases_for
from rag.search import load_index, filter_idx, Bm25, to_pages
from rag.study_search import adaptive_hits
from rag import study_registry as sr


def main():
    parent,pv,_=load_index('bge-m3','data/rag/candidates/pipeline_review_2026-10-08')
    updated,uv,_=load_index('bge-m3','data/rag/candidates/study_candidates_2026-10-09_v2')
    assert np.array_equal(pv,uv)
    assert [(c['header'],c['text']) for c in parent]==[(c['header'],c['text']) for c in updated]
    cases=cases_for(parent)
    forms=[(c,l,c[l]) for c in cases for l in ('en','ko','ko_b','ja') if l in c]
    saved=np.load('data/runs/pipeline_review_2026-10-08/query_vectors.npz',allow_pickle=False)
    import hashlib
    assert str(saved['fingerprint'])==hashlib.sha256(json.dumps([q for _,_,q in forms],ensure_ascii=False).encode()).hexdigest()
    results=[]
    for name,chunks,vec in [('before',parent,pv),('after',updated,uv)]:
        idx=filter_idx(chunks,'SB17','assessment','EU');bm=Bm25(chunks)
        try:
            for i,(case,lang,query) in enumerate(forms):
                if case['suite']!='historical_qa':continue
                studies=list(sr.find(query,'SB17'))
                pages=to_pages(adaptive_hits(saved['vectors'][i],query,idx,chunks,vec,bm,studies),chunks,5)
                gold=set(case['pages'])
                rank=next((i for i,p in enumerate(pages,1) if p['document_id']==case['document_id'] and p['pdf_page'] in gold),None)
                results.append({'version':name,'id':case['id'],'language':lang,'rank':rank,'pages':[p['pdf_page'] for p in pages]})
        finally:bm.db.close()
    metrics=[]
    for name in ['before','after']:
        for lang in ['en','ko','ko_b']:
            group=[r for r in results if r['version']==name and r['language']==lang]
            metrics.append({'version':name,'language':lang,'n':len(group),'strict_Hit@5':sum(r['rank'] is not None for r in group)/len(group)})
    out=Path('data/runs/rag_updates_2026-10-09/stage2_metadata');out.mkdir(parents=True,exist_ok=True)
    report={'control':'identical source/text/header/vectors/query vectors; only source-backed study candidates changed; strict original page labels',
        'metrics':metrics,'rows':results,'semantic_approval':False,'hard_filter_used':False}
    (out/'retrieval_comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(metrics))


if __name__=='__main__':main()
