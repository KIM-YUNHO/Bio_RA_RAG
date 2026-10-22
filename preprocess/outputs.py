"""Write an extraction bundle: assets, pages, queues, source units, visual groups and their context, and the
integrity record over those files."""
from preprocess.files import save_rows, write_json
from preprocess.ingest import sha256


def save_outputs(out, assets, pages, documents, summary, groups=None):
    from preprocess.visual.figure_labels import apply
    apply(assets, groups)
    save_rows(out / "extracted_assets.jsonl", assets)
    save_rows(out / "extraction_pages.jsonl", pages)
    queue = [{"asset_id": a["asset_id"], "document_id": a["document_id"], "pdf_page": a["pdf_page"],
              "task": a["route"]["task"], "reasons": a["route"]["reasons"],
              "status": (a["enrichment"] or {}).get("status", "pending")}
             for a in assets if a["route"]["task"]]
    save_rows(out / "enrichment_queue.jsonl", queue)
    reviews = [{"page_id": p["page_id"], "reason": "page_incomplete", "details": p.get("error") or p.get("invalid_blocks")}
               for p in pages if p["status"] != "parsed"]
    reviews += [{"asset_id": a["asset_id"], "reason": "requires_review", "details": a["enrichment"]}
                for a in assets if a["route"]["task"] and (a["enrichment"] or {}).get("status") != "heuristic_checks_passed"]
    reviews += [{"asset_id":a['asset_id'],"reason":"continued_table_association_requires_review",
                 "logical_group_id":a.get('logical_group_id')}
                for a in assets if a.get('continuation_status')=='data_fragment_continuation_candidate']
    reviews += [{'asset_id': a['asset_id'], 'reason': 'table_context_association_requires_review',
                 'details': a['context_association_issues']}
                for a in assets if a.get('context_association_issues')]
    reviews += [{'asset_id':a['asset_id'],'reason':'cascade_region_requires_review','details':a['route']}
                for a in assets if a['route'].get('next_stage') in {'review','light_ocr'}]
    save_rows(out / "extraction_review_queue.jsonl", reviews)
    units = [{"unit_id": a["asset_id"], "document_id": a["document_id"], "version_id": a["version_id"],
        "kind": a["asset_type"], "text": a["selected"]["content"], "content_origin": a["selected"]["origin"],
        "source_refs": a["source_refs"], "representation_range": [0, len(a["selected"]["content"])],
        "validation_status": a["validation_status"], "numeric_evidence_eligible": False}
        for a in assets]
    for unit, asset in zip(units, assets):
        unit["review_required"] = (bool(asset["route"]["task"]) and (asset["enrichment"] or {}).get("status") != "heuristic_checks_passed"
            or asset['route'].get('next_stage') in {'review','light_ocr'})
        if asset.get('continuation_status') == 'data_fragment_continuation_candidate':
            from preprocess.source.native_table_fragments import eligible as native_fragment_eligible
            unit['review_required'] = not native_fragment_eligible(asset)
            if not unit['review_required']:
                unit['native_table_fragment'] = {k: asset['native_table_fragment']['fragment'][k] for k in
                    ('version','header_source_rows','closed_source_row_groups','excluded_source_rows','cross_page_relationship')}
        if asset['selected']['origin'] in {'native_figure_caption','paddle_figure_caption','figure_labels_candidate'}:
            unit['description_review_required'] = True
            unit['review_required'] = False
        if asset.get('figure_labels'):
            unit['figure_labels'] = asset['figure_labels']
        if any(c.get('visual_description_review_required') for c in
                (asset.get('source_grid_refinement') or {}).get('plan',{}).get('cells',[])):
            unit['description_review_required'] = True
        unit["retrieval_eligible"] = bool(unit["text"].strip()) and not unit["review_required"]
        if asset['block_label'] == 'native_figure_context':
            unit['retrieval_eligible'] = False
            unit['retrieval_role'] = 'visual_context_only'
        group = next((g for g in groups or [] if g['group_id']==asset.get('logical_group_id')), None)
        if group:
            unit['structure'] = {'logical_group_id':group['group_id'],
                'figure_id':group['group_id'] if group['kind']=='figure' else None,
                'table_id':group['group_id'] if group['kind']=='table' else None,
                'caption':group['caption_text'], 'panel_index':asset.get('panel_index'),
                'member_unit_ids':group['member_asset_ids'],
                'context_unit_ids':group.get('context_asset_ids', group['caption_part_asset_ids']),
                'context_roles':group.get('context_roles', {}),
                'association_status':group['association_status'], 'continuation_status':group['continuation_status']}
            if group['kind']=='figure' and not asset.get('is_logical_group'):
                unit['retrieval_eligible'] = False
                unit['retrieval_role'] = 'logical_figure_member'
                unit['parent_unit_id'] = group['group_id']
    save_rows(out / "source_units.jsonl", units)
    write_json(out / "extraction_summary.json", summary)
    if groups is not None:
        save_rows(out / 'visual_groups.jsonl',groups)
    from preprocess.visual.visual_context import assemble, save_bundle
    context_contract = save_bundle(out, assemble(assets, units, groups or [], [d.to_dict() for d in documents], pages))
    write_json(out / "extraction_integrity.json", {"files": {name: sha256(out / name) for name in
        ("extracted_assets.jsonl", "extraction_pages.jsonl", "source_units.jsonl",*(("visual_groups.jsonl",) if groups is not None else ()))},
        'visual_context': context_contract})
