"""Read-only source-border recovery probe over every held continuation table."""
from collections import Counter
from pathlib import Path
import argparse
import json
import time
import fitz
from preprocess.native_table_fragments import prepare
from eval.h_v2_development_eval import read, save, digest


def probe(source, out):
    if out.exists(): raise ValueError('Choose a new output directory')
    assets=[json.loads(x) for x in (source/'extracted_assets.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    candidates=[a for a in assets if a['asset_type']=='table' and a.get('continuation_status')=='data_fragment_continuation_candidate']
    docs={d['version_id']:d for d in read(source/'extraction_manifest.json')['documents']}
    rows=[];pdfs={};start=time.monotonic()
    try:
        for a in candidates:
            d=docs[a['version_id']];path=d.get('rendered_path') or d['raw_path']
            if path not in pdfs:pdfs[path]=fitz.open(path)
            proof=prepare(a,pdfs[path][a['pdf_page']-1])
            r={'asset_id':a['asset_id'],'document_id':a['document_id'],'pdf_page':a['pdf_page'],
               'recoverable':bool(proof),'original_sha256':a['version_id']}
            if proof:
                save(out/'proofs'/(a['asset_id']+'.json'),proof)
                r.update(closed_source_row_groups=proof['fragment']['closed_source_row_groups'],
                    excluded_source_rows=proof['fragment']['excluded_source_rows'],
                    native_html_chars=len(proof['fragment']['html']))
            rows.append(r)
    finally:
        for pdf in pdfs.values():pdf.close()
    summary={'source':str(source),'source_integrity_sha256':digest(source/'extraction_integrity.json'),
        'candidate_count':len(candidates),'recovered_count':sum(r['recoverable'] for r in rows),
        'seconds':time.monotonic()-start,'rows':rows,'new_model_calls':0,'source_modified':False}
    save(out/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();probe(a.source.resolve(),a.out.resolve())
