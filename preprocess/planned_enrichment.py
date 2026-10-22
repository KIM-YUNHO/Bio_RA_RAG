"""Execute frozen region tasks in a separate bundle without rerunning OCR."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

from .files import read_jsonl
from .hybrid import enrich_assets
from .outputs import save_outputs
from .hybrid_validate import validate_extraction
from .ingest import Document,sha256,stable_hash,assert_model_execution_allowed
from .files import write_json
from .reporting import Progress
from .files import exclusive

TERMINAL={'heuristic_checks_passed','needs_review','complete'}

def bindings(source,tasks,eligible,protected):
    source=Path(source).resolve()
    manifest=json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'))
    documents=[Document(**d) for d in manifest['documents']]
    assert_model_execution_allowed(documents)
    if any(d.version_id in protected or d.version_id not in eligible for d in documents):
        raise ValueError('Out-of-scope enrichment source')
    validate_extraction(source)
    assets={a['asset_id']:a for a in read_jsonl(source/'extracted_assets.jsonl')}
    if len({t['asset_id'] for t in tasks})!=len(tasks):raise ValueError('Duplicate planned task')
    for t in tasks:
        a=assets.get(t['asset_id'])
        if not a:raise ValueError('Frozen visual task no longer matches its source')
        from .visual_task_inputs import VERSION,input_state
        contract=t.get('input_contract_version')
        if contract==VERSION:
            from .visual_router import VERSION as ROUTING_VERSION
            if (a['route'].get('policy_version')!=ROUTING_VERSION
                    or a['route'].get('next_stage')!='qwen' or t.get('route')!=a['route']):
                raise ValueError('Frozen visual task requires current region routing')
            expected=stable_hash(input_state(a,list(assets.values()),documents))
        elif contract is None:
            # Legacy flat tasks did not bind OCR sidecars or figure members.
            # They cannot authorize work on the new region cascade outputs.
            if a.get('light_region_ocr') or a.get('is_logical_group'):
                raise ValueError('Legacy visual task lacks region/member input bindings')
            expected=stable_hash([a['base'],a['native_reference']])
        else:raise ValueError('Unsupported visual task input contract')
        if (not a or Path(t['extraction']).resolve()!=source
            or t.get('task')!=a['route']['task']
            or t['input_state_sha256']!=expected
            or any(t[k]!=a[k] for k in ('document_id','version_id','pdf_page','bbox_pdf','bbox_input_px','source_refs'))):
            raise ValueError('Frozen visual task no longer matches its source')
    return documents

def snapshot(source,out):
    source,out=Path(source).resolve(),Path(out).resolve()
    if source==out or out.is_relative_to(source) or source.is_relative_to(out):
        raise ValueError('Use a separate enrichment bundle')
    proof=out/'planned_snapshot.json'
    integrity=sha256(source/'extraction_integrity.json')
    if proof.exists():
        saved=json.loads(proof.read_text(encoding='utf-8'))
        if saved['source']!=str(source) or saved['integrity_sha256']!=integrity:
            raise ValueError('Enrichment snapshot source changed')
        validate_extraction(out)
        return
    if out.exists() and any(out.iterdir()):raise ValueError('Incomplete snapshot; choose a new output')
    out.mkdir(parents=True,exist_ok=True)
    names=['extraction_manifest.json','extraction_integrity.json','extraction_summary.json',
        'extracted_assets.jsonl','extraction_pages.jsonl','source_units.jsonl','visual_groups.jsonl',
        'visual_context_units.jsonl','visual_context_links.jsonl','visual_context_targets.jsonl',
        'visual_context_summary.json']
    copied={}
    for name in names:
        if (source/name).is_file():
            shutil.copyfile(source/name,out/name);copied[name]=sha256(out/name)
    # Page images, original PDFs and existing reply proofs stay source-bound.
    # No Paddle worker, PDF render or OCR is required to create the snapshot.
    validate_extraction(out)
    write_json(proof,{'source':str(source),'integrity_sha256':integrity,'copied_files':copied,
        'source_files_shared':True,'new_ocr_calls':0})

def execute(source,out,tasks,config,eligible,protected,adapter=None,max_calls=None,notify=None):
    documents=bindings(source,tasks,eligible,protected)
    snapshot(source,out);out=Path(out).resolve()
    with exclusive(out):
        manifest=json.loads((out/'extraction_manifest.json').read_text(encoding='utf-8'))
        if manifest['config']['qwen']!=config.to_dict()['qwen']:
            raise ValueError('Frozen Qwen configuration changed')
        assets=read_jsonl(out/'extracted_assets.jsonl');pages=read_jsonl(out/'extraction_pages.jsonl')
        groups=read_jsonl(out/'visual_groups.jsonl')
        selected={t['asset_id'] for t in tasks if (next(a for a in assets if a['asset_id']==t['asset_id']).get('enrichment') or {}).get('status') not in TERMINAL}
        if not selected:return {'attempted_calls':0,'cache_reused':0,'pending':0,'failed':0,'selected_assets':0}
        untouched={a['asset_id']:deepcopy([a['selected'],a.get('enrichment')]) for a in assets if a['asset_id'] not in selected}
        class TrackingProgress(Progress):
            def enrichment(self,total,statuses=None,current=None):
                super().enrichment(total,statuses,current)
                if notify:notify(total=total,statuses=dict(statuses or {}),current=current)
        progress=TrackingProgress(out,documents,'planned_region_enrichment')
        manifest['status_run_id']=progress.value['run_id']
        manifest['status']='running'
        write_json(out/'extraction_manifest.json',manifest)
        for d in documents:
            own=[p for p in pages if p['version_id']==d.version_id]
            progress.plan(d,len(own));progress.documents[(d.document_id,d.version_id)]['page_counts']={'parsed':len(own)}
        try:
            result=enrich_assets(assets,documents,out,config,adapter,max_calls,progress,only_asset_ids=selected)
            for a in assets:
                if a['asset_id'] in untouched and [a['selected'],a.get('enrichment')]!=untouched[a['asset_id']]:
                    raise ValueError('Unplanned asset changed during enrichment')
            summary=json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'))
            result['selected_assets']=len(selected)
            summary['qwen']=result
            summary['planned_enrichment']={'source':str(Path(source).resolve()),'only_asset_ids':sorted(selected),
                'new_ocr_calls':0,'api_calls':0,'remaining_tasks':sum(bool(a['route']['task'])
                    and (a.get('enrichment') or {}).get('status') not in TERMINAL for a in assets)}
            save_outputs(out,assets,pages,documents,summary,groups)
            manifest['status']='partial' if summary['planned_enrichment']['remaining_tasks'] else 'extraction_complete'
            write_json(out/'extraction_manifest.json',manifest)
            validate_extraction(out);progress.finish()
            return result
        except BaseException as exc:
            progress.fail(exc);raise
