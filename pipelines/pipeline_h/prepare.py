"""Freeze the user-approved corpus and retain the existing source/metadata contract."""
import json,tempfile,time
from pathlib import Path
from collections import Counter
from preprocess.ingest import Document,catalog_metadata,sha256
from pipelines.pipeline_h.policy import VERSION

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def write(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=p.parent,prefix=p.name+'.',suffix='.tmp',delete=False) as stream:
        stream.write(payload);temp=Path(stream.name)
    try:
        for attempt in range(8):
            try:temp.replace(p);break
            except PermissionError:
                if attempt==7:raise
                time.sleep(min(.05*2**attempt,.5))
    finally:temp.unlink(missing_ok=True)

def prepare(run):
    run=Path(run).resolve();run.mkdir(parents=True,exist_ok=True)
    approved=read(ROOT/'data/runs/corpus_update_2026-10-09/corpus_plan.json')
    catalog=[json.loads(x) for x in (ROOT/'data/catalog/documents.jsonl').read_text(encoding='utf-8-sig').splitlines() if x.strip()]
    catalog={(d['document_id'],d['version_id']):d for d in catalog}
    release=read(ROOT/'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2/release_manifest.json')
    old={}
    for component in release['components']:
        manifest=read(Path(component['run'])/'extraction_manifest.json')
        for d in manifest['documents']:old[(d['document_id'],d['version_id'])]=d
    docs=[]
    for entry in approved['documents']:
        key=entry['document_id'],entry['version_id'];row=catalog[key]
        if key in old:
            doc=Document(**old[key]);doc.metadata.update(catalog_metadata(row))
        else:
            raw=str((ROOT/row['raw_path']).resolve())
            doc=Document(*key,row['kind'],raw,str((ROOT/(row.get('text_path') or row['raw_path'])).resolve()),[raw],catalog_metadata(row),row.get('sha256') or key[1])
        if sha256(doc.raw_path)!=doc.expected_sha256:raise ValueError('Original checksum mismatch: '+doc.document_id)
        if doc.rendered_path and sha256(doc.rendered_path)!=doc.rendered_sha256:raise ValueError('Derivative mismatch')
        if doc.metadata.get('model_execution_allowed') is False:raise ValueError('Approved document unexpectedly execution-denied')
        doc.metadata.update(model_execution_allowed=True,pipeline_h_scope='user_confirmed_existing_92',
                            preprocessing_pipeline=VERSION)
        docs.append(doc)
    if len(docs)!=92:raise ValueError('Approved corpus must contain exactly 92 source versions')
    products=Counter(d.metadata['product_id'] for d in docs)
    if set(products)!={'SB17','ABP654','CTP43','BMAB1200','BAT2206'}:raise ValueError('Product scope mismatch')
    from preprocess.html_render import prepare as prepare_renders
    missing_html=[d for d in docs if d.kind=='html' and not d.rendered_path]
    if missing_html:prepare_renders(missing_html,run/'input')
    protected_paths=[ROOT/'data/versions/01_pilot_baseline_2026-10-10/records/service_release.json',ROOT/'data/collection.sqlite',
        ROOT/'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2/release_manifest.json']
    for subtree in ('preprocess','rag','rag_service'):
        protected_paths += [p for p in (ROOT/subtree).glob('*.py')]
    previous=ROOT/'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2/candidate'
    protected_paths += [previous/n for n in ('chunks.jsonl','chunks_summary.json','emb/bge-m3/doc.npy','emb/bge-m3/meta.json','emb/bge-m3/chunk_ids.json')]
    protected={str(p):sha256(p) for p in protected_paths if p.exists()}
    write(run/'catalog_snapshot.json',[catalog[(d.document_id,d.version_id)] for d in docs])
    all_products=[json.loads(x) for x in (ROOT/'data/catalog/products.jsonl').read_text(encoding='utf-8-sig').splitlines() if x.strip()]
    write(run/'products_snapshot.json',[p for p in all_products if p['product_id'] in products])
    write(run/'intake_manifest.json',{'version':'production-h-intake-1','documents':[d.to_dict() for d in docs],
        'scope_plan_sha256':sha256(ROOT/'data/runs/corpus_update_2026-10-09/corpus_plan.json'),
        'products':dict(products),'protected_exclusions':approved['protected_exclusions'],
        'input_checksums':{d.raw_path:d.expected_sha256 for d in docs},'protected_original_pipeline':protected,
        'catalog_snapshot_sha256':sha256(run/'catalog_snapshot.json'),'products_snapshot_sha256':sha256(run/'products_snapshot.json'),
        'old_release':str(ROOT/'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2')})
    return docs

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);args=p.parse_args()
    docs=prepare(args.run);print(json.dumps({'documents':len(docs),'products':dict(Counter(d.metadata['product_id'] for d in docs))}))
