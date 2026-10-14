"""Finish remaining table decisions: literal recovery or explicit source review.

Unconfirmed structure is a review result, not permission for blanket VLM work.
This stage never calls OCR, Qwen, embeddings or an external API.
"""
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import fitz
from .grid_refinement import apply_asset
from .complex_geometry import table_gate
from .hybrid import read_jsonl,save_outputs,assert_sources
from .hybrid_validate import validate_extraction
from .ingest import Document,sha256,stable_hash,assert_model_execution_allowed
from .inventory import write_json
from .planned_enrichment import snapshot,TERMINAL
from .workflow import exclusive

VERSION='source-queue-finalization-1'


def bind(asset,gate):
    return {'version':VERSION,'gate':gate,'source_state_sha256':stable_hash(
        [asset['base'],asset['native_reference'],asset.get('light_region_ocr')]),
        'numeric_evidence_eligible':False}


def finish_asset(asset,page):
    outcome=apply_asset(asset,page)
    if outcome=='recovered_tables':return outcome
    if outcome=='not_applicable':raise ValueError('Finalization requires a completed ordinary OCR table')
    gate=table_gate(asset,page);plan=(asset.get('source_grid_refinement') or {}).get('plan')
    if gate['complexity_confirmed'] and plan and plan['unresolved_cells']:
        # Do not make a genuinely unresolved complex cell disappear by relabeling
        # it as complete. The separate bounded transcription step must handle it.
        raise ValueError('Complex source cells still require bounded transcription')
    asset['source_grid_gate_review']=bind(asset,gate)
    asset['superseded_unconfirmed_route']=deepcopy(asset['route'])
    asset['route']={'task':None,'next_stage':'review','policy_version':VERSION,
        'reasons':['source_structure_unconfirmed' if not gate['geometry'] else 'source_text_coverage_requires_review'],
        'numeric_evidence_eligible':False}
    return 'explicit_review_tables'


def validate_review(asset,page):
    proof=asset.get('source_grid_gate_review')
    if proof is None:return
    gate=table_gate(asset,page)
    if proof!=bind(asset,gate):raise ValueError('Final review no longer matches its source geometry/OCR state')
    plan=(asset.get('source_grid_refinement') or {}).get('plan')
    if (gate['geometry'] and plan and not plan['issues'] or gate['complexity_confirmed'] and plan and plan['unresolved_cells']):
        raise ValueError('Final review hides recoverable/unfinished complex source cells')
    reason='source_structure_unconfirmed' if not gate['geometry'] else 'source_text_coverage_requires_review'
    if asset['route']!={'task':None,'next_stage':'review','policy_version':VERSION,'reasons':[reason],'numeric_evidence_eligible':False}:
        raise ValueError('Final source review was put back into a model queue')


def finalize(source,out,task_ids=None,eligible=None,protected=()):
    source,out=Path(source).resolve(),Path(out).resolve()
    manifest=json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'))
    docs=[Document(**d) for d in manifest['documents']]
    assert_model_execution_allowed(docs);assert_sources(docs)
    if any(d.version_id in protected or eligible is not None and d.version_id not in eligible for d in docs):
        raise ValueError('Out-of-scope finalization input')
    snapshot(source,out)
    with exclusive(out),ExitStack() as stack:
        assets=read_jsonl(out/'extracted_assets.jsonl');known={a['asset_id'] for a in assets}
        if task_ids is not None and not set(task_ids)<=known:raise ValueError('Unknown finalization target')
        lookup={d.version_id:d for d in docs};pdfs={};counts=Counter();selected=[]
        before=sha256(source/'extraction_integrity.json')
        for a in assets:
            if task_ids is not None and a['asset_id'] not in task_ids:continue
            if task_ids is None and (a['asset_type']!='table' or not a['route'].get('task')
                    or (a.get('enrichment') or {}).get('status') in TERMINAL):continue
            doc=lookup[a['version_id']]
            if doc.pdf_path not in pdfs:pdfs[doc.pdf_path]=stack.enter_context(fitz.open(doc.pdf_path))
            page=pdfs[doc.pdf_path][a['pdf_page']-1]
            counts[finish_asset(a,page)]+=1;validate_review(a,page);selected.append(a['asset_id'])
        summary=json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'))
        result={'version':VERSION,'source':str(source),'source_integrity_sha256':before,
            'selected_asset_ids':selected,**dict(counts),'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0}
        summary['source_queue_finalization']=result
        manifest.pop('status_run_id',None);manifest['status']='partial';manifest['source_queue_finalization_version']=VERSION
        write_json(out/'extraction_manifest.json',manifest)
        save_outputs(out,assets,read_jsonl(out/'extraction_pages.jsonl'),docs,summary,read_jsonl(out/'visual_groups.jsonl'))
        validate_extraction(out)
        if sha256(source/'extraction_integrity.json')!=before:raise ValueError('Finalization changed original source bundle')
        return result


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--asset-id',action='append')
    args=parser.parse_args();print(json.dumps(finalize(args.run,args.out,args.asset_id),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
