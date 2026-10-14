"""Recheck completed development extraction and enrich only changed/pending units."""
import argparse
import json
from pathlib import Path
from .ingest import Document, assert_model_execution_allowed, sha256
from .hybrid import read_jsonl, enrich_assets, save_outputs
from .hybrid_config import load_config
from .hybrid_validate import validate_extraction
from .regroup import regroup
from .inventory import write_json
from .reporting import build_report, save_report


def refine(run, out, config, use_qwen=True, max_calls=None):
    run,out=Path(run).resolve(),Path(out).resolve()
    manifest=json.loads((run/'extraction_manifest.json').read_text(encoding='utf-8'))
    documents=[Document(**d) for d in manifest['documents']]
    assert_model_execution_allowed(documents)
    change=regroup(run,out)
    assets=read_jsonl(out/'extracted_assets.jsonl')
    pending={a['asset_id'] for a in assets if a['route']['task'] and
        (not a.get('enrichment') or a['enrichment']['status'].startswith('pending'))}
    stats=enrich_assets(assets,documents,out,config,max_calls=max_calls,only_asset_ids=pending) if use_qwen else {
        'attempted_calls':0,'cache_reused':0,'pending':len(pending),'failed':0}
    summary=json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'))
    summary['qwen']=stats
    summary['refinement']={**change,'selected_asset_ids':sorted(pending),
        'unchanged_candidates_preserved':sum(bool(a.get('enrichment')) for a in assets if a['asset_id'] not in pending)}
    save_outputs(out,assets,read_jsonl(out/'extraction_pages.jsonl'),documents,summary,read_jsonl(out/'visual_groups.jsonl'))
    revised=json.loads((out/'extraction_manifest.json').read_text(encoding='utf-8'))
    revised['status']='partial' if stats['failed'] or stats['pending'] else 'extraction_complete'
    write_json(out/'extraction_manifest.json',revised)
    validate_extraction(out)
    save_report(build_report(out),out)
    return summary['refinement'] | {'qwen':stats}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--config',type=Path,default=Path('configs/preprocess.local.json'))
    p.add_argument('--no-qwen',action='store_true');p.add_argument('--max-qwen-calls',type=int)
    a=p.parse_args();print(json.dumps(refine(a.run,a.out,load_config(a.config),not a.no_qwen,a.max_qwen_calls),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
