"""Bind planned VLM work and caches to region, member and context provenance."""
VERSION='visual-region-input-2'


def region_state(asset):
    return {key:asset.get(key) for key in ('asset_id','document_id','version_id','pdf_page',
        'bbox_pdf','bbox_input_px','input_image_size','source_refs','base','native_reference',
        'selected','light_region_ocr','label_ocr','label_ocr_regions','logical_group_id',
        'member_asset_ids','region_structure','visual_structure_review','route')}


def input_state(asset,assets,documents,context=None):
    from preprocess.extraction.hybrid import context_for
    from preprocess.ocr.qwen_contracts import task_fingerprint
    lookup={a['asset_id']:a for a in assets}
    docs={(d.document_id,d.version_id):d for d in documents}
    context=context if context is not None else context_for(asset,assets,docs)
    identifiers=list(dict.fromkeys([*asset.get('member_asset_ids',[]),
        *[i['asset_id'] for i in context.get('context_items',[])],
        *[i['asset_id'] for i in context.get('previous_table_regions',[])]]))
    return {'version':VERSION,'task_contract':task_fingerprint({},asset['route']['task']),
        'target':region_state(asset),'context':context,
        'related_regions':[region_state(lookup[identifier]) for identifier in identifiers]}


def planned_task(source,asset,assets,documents):
    from pathlib import Path
    from preprocess.ingest import stable_hash
    from preprocess.extraction.visual_router import VERSION as ROUTING_VERSION
    route=asset['route']
    if (route.get('policy_version')!=ROUTING_VERSION or route.get('next_stage')!='qwen'
            or route.get('task') not in ('repair_table','describe_figure')):
        raise ValueError('Reclassify the region with the current policy before planning VLM work')
    return {**{k:asset[k] for k in ('asset_id','document_id','version_id','pdf_page',
        'bbox_pdf','bbox_input_px','source_refs')},'extraction':str(Path(source).resolve()),
        'task':route['task'],'route':route,'input_contract_version':VERSION,
        'input_state_sha256':stable_hash(input_state(asset,assets,documents))}
