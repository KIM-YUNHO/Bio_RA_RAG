"""Read-only release, source-link and scoped BM25 smoke checks; no model calls."""
import argparse
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag.answer import Corpus, retrieve
from rag.release import load_bundle
from rag.preprocessed_evidence import PreprocessedEvidence


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    b=load_bundle(args.run)
    def get(url,headers=None):
        with urlopen(Request(url,headers=headers or {}),timeout=30) as response:
            return response.status,response.headers.get('Content-Type',''),response.read()
    _,_,payload=get('http://127.0.0.1:8765/api/summary');main=json.loads(payload)
    _,_,payload=get('http://127.0.0.1:8766/api/summary');viewer=json.loads(payload)
    assert main['chunks_sha256']==viewer['chunks_sha256']==b.chunks_sha256
    assert len(main['indexed_products'])==5
    identities=set(b.doc_by_identity)
    protected=[]; manifests=0
    for path in Path('data/preprocessed').rglob('extraction_manifest.json'):
        manifests+=1
        for doc in json.loads(path.read_text(encoding='utf-8'))['documents']:
            m=doc.get('metadata',{})
            if m.get('evaluation_cohort') not in (None,'development') or m.get('model_execution_allowed') is False:
                protected.append({'document_id':doc['document_id'],'version_id':doc['version_id'],
                    'cohort':m.get('evaluation_cohort'),'manifest':str(path)})
    overlap=[p for p in protected if p['version_id'] in {v for d,v in identities}]
    assert not overlap,'Protected source hash was promoted'
    sources=[]
    for key,doc in b.docs.items():
        page=b.pages_by_doc[key][0];pid=page['page_id']
        status,kind,data=get(f'http://127.0.0.1:8766/source/{key}.pdf',{'Range':'bytes=0-31'})
        assert status==206 and data.startswith(b'%PDF') and kind=='application/pdf'
        status,kind,data=get(f'http://127.0.0.1:8766/page-image/{pid}')
        assert status==200 and kind=='image/png' and data.startswith(b'\x89PNG')
        _,_,data=get(f'http://127.0.0.1:8766/api/page/{pid}');detail=json.loads(data)
        assert detail['doc_key']==key and detail['pdf_page']==page['pdf_page']
        sources.append({'document_key':key,'document_id':doc['document_id'],'version_id':doc['version_id'],
            'pdf_range':True,'page_image':True,'page_api':True})
    corpus=Corpus('bge-m3',b.candidate,local_only=True,bundle=b)
    adapter=PreprocessedEvidence(b,corpus.chunks,b.chunks_sha256,'http://127.0.0.1:8766')
    results=[]
    terms={'SB17':'SB17 randomised double-blind Phase I','ABP654':'ABP 654 pharmacokinetic healthy subjects',
        'CTP43':'CT-P43 pharmacokinetic healthy subjects','BMAB1200':'ustekinumab drug substance quality',
        'BAT2206':'Usymro active substance ustekinumab'}
    try:
        for product,query in terms.items():
            agency=next(d['jurisdiction'] for d in main['documents'] if d['product_id']==product)
            u={'intent':'fact_lookup','products':[product],'jurisdictions':[agency],
                'studies':[],'topic':'other','doc_groups':[],'search_terms':query}
            pages=retrieve(corpus,u,None,use_dense=False,policy='general',agencies=[agency],products=[product])[0][1]
            assert pages
            blocks=[]
            for p in pages:
                chunk=adapter.by_id[p['best_chunk']]
                assert chunk['product_id']==product and chunk['jurisdiction']==agency
                for link in adapter.links(chunk):
                    assert b.chunks_sha256 in link['url']
                blocks.append({'document_id':p['document_id'],'pdf_page':p['pdf_page'],'best_chunk':p['best_chunk']})
            results.append({'product':product,'agency':agency,'query':query,'pages':blocks,'scope_pass':True})
    finally:corpus.bm.db.close()
    result={'index_sha256':b.chunks_sha256,'same_live_index':True,'indexed_products':main['indexed_products'],
        'readiness':main['product_readiness'],'sources':sources,'bm25_smoke':results,
        'protected_source_audit':{'manifests_checked':manifests,'protected_document_records':len(protected),'overlap':overlap},
        'limitations':'Coverage and BM25 smoke only; no five-product clinical QA accuracy or full-corpus readiness claim.'}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'indexed_products':main['indexed_products'],'sources_checked':len(sources),
        'scoped_bm25_checks':len(results),'protected_overlap':len(overlap)},ensure_ascii=False))


if __name__=='__main__':main()
