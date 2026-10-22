"""Recover complete literal tables from source-fixed cells before any VLM.

Runs over a sealed bundle and writes a separate, replay-validated snapshot.
No model, network, PDF parser or OCR is called by this stage.
"""
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import fitz
from .complex_geometry import table_gate
from .source_grid import prepare,VERSION
from .files import read_jsonl
from .outputs import save_outputs
from .ingest import assert_sources
from .hybrid_validate import validate_extraction
from .ingest import Document,assert_model_execution_allowed,sha256
from .files import write_json
from .planned_enrichment import snapshot
from .files import exclusive


def apply_asset(asset,page):
    if asset['asset_type']!='table' or not asset.get('light_region_ocr'):return 'not_applicable'
    gate=table_gate(asset,page)
    if not gate['geometry']:return 'source_grid_unconfirmed'
    plan=prepare(asset,page,gate['geometry'])
    asset['source_grid_refinement']={'gate':gate,'plan':plan,'transcriptions':[]}
    if plan['issues']:return 'source_cells_require_review'
    if asset.get('enrichment'):asset['superseded_grid_enrichment']=deepcopy(asset['enrichment'])
    asset['enrichment']=None
    asset['selected']={'origin':'source_fixed_grid_table_candidate','content':plan['representation']}
    asset['route']={'task':None,'next_stage':'source_fixed_grid_complete','policy_version':VERSION,
        'reasons':['source_borders_and_literal_cells_recovered'],'numeric_evidence_eligible':False}
    asset['validation_status']='derived_unverified'
    return 'recovered_tables'


def refine(source,out,task_ids=None,eligible=None,protected=()):
    source,out=Path(source).resolve(),Path(out).resolve()
    manifest=json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'))
    documents=[Document(**d) for d in manifest['documents']]
    assert_model_execution_allowed(documents);assert_sources(documents)
    if any(d.version_id in protected or eligible is not None and d.version_id not in eligible for d in documents):
        raise ValueError('Out-of-scope source grid input')
    snapshot(source,out)
    with exclusive(out),ExitStack() as stack:
        assets=read_jsonl(out/'extracted_assets.jsonl');known={a['asset_id'] for a in assets}
        if task_ids is not None and not set(task_ids)<=known:raise ValueError('Unknown source grid target')
        docs={d.version_id:d for d in documents};pdfs={};counts=Counter();before=sha256(source/'extraction_integrity.json')
        for asset in assets:
            if task_ids is not None and asset['asset_id'] not in task_ids:continue
            if asset['asset_type']!='table' or not asset.get('light_region_ocr'):continue
            if task_ids is None and asset['route'].get('next_stage') not in ('qwen','review','light_ocr'):continue
            doc=docs[asset['version_id']]
            if doc.pdf_path not in pdfs:pdfs[doc.pdf_path]=stack.enter_context(fitz.open(doc.pdf_path))
            counts[apply_asset(asset,pdfs[doc.pdf_path][asset['pdf_page']-1])]+=1
        summary=json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'))
        summary['source_grid_refinement']={'version':VERSION,'source':str(source),'source_integrity_sha256':before,
            **dict(counts),'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0}
        manifest.pop('status_run_id',None);manifest['source_grid_refinement_version']=VERSION;manifest['status']='partial'
        write_json(out/'extraction_manifest.json',manifest)
        save_outputs(out,assets,read_jsonl(out/'extraction_pages.jsonl'),documents,summary,read_jsonl(out/'visual_groups.jsonl'))
        validate_extraction(out)
        if sha256(source/'extraction_integrity.json')!=before:raise ValueError('Source bundle changed during grid refinement')
        return summary['source_grid_refinement']


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--asset-id',action='append')
    args=parser.parse_args();print(json.dumps(refine(args.source,args.out,args.asset_id),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
