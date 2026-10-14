"""General region policy: native -> non-generative OCR -> complex visuals only."""
VERSION='region-cascade-2'
REVIEW_ONLY=frozenset(('redacted_cells_require_review','source_redactions_require_review',
    'continuation_needs_context','first_row_spans_entire_table_needs_context',
    'rowspan_in_redacted_table_requires_review','visible_text_spans_redacted_rows',
    'source_redaction_marker_not_preserved'))


def decide(kind,quality=None,*,native_complete=False,light=None,complexity=None):
    quality=quality or {};issues=set(quality.get('issues',[]));complexity=complexity or {}
    def route(stage,task=None,reasons=()):
        return {'task':task,'next_stage':stage,'reasons':list(reasons),
            'policy_version':VERSION,'numeric_evidence_eligible':False}
    if native_complete and not issues:
        return route('native',reasons=['usable_native_region'])
    if kind=='table' and issues and issues<=REVIEW_ONLY:
        return route('review',reasons=sorted(issues))
    if light is None:
        return route('light_ocr',reasons=['light_ocr_required_before_vlm'])
    tokens=light.get('tokens',[])
    low=sum(t['confidence']<.8 for t in tokens)/max(1,len(tokens))
    if kind=='table':
        if complexity.get('simple_grid_complete') and not issues and tokens and low==0:
            return route('light_ocr_complete',reasons=['flat_grid_and_readable_cells'])
        hard=[k for k in ('merged_cells','irregular_grid','nested_table','borderless_unresolved') if complexity.get(k)]
        if hard:
            return route('qwen','repair_table',hard+(['low_confidence_text'] if low else []))
        # An unreadable simple cell is an OCR retry/review, not a reason to
        # generate an entire table. Redaction/continuation also stay explicit.
        return route('review',reasons=sorted(issues) or ['simple_table_ocr_incomplete'])
    if kind in ('figure','figure_group'):
        # Layout region/panel count does not distinguish a chart from a set
        # of injection instructions. Require a positive structural signal.
        hard=[k for k in ('chart_axes','dense_diagram','overlapping_labels') if complexity.get(k)]
        if hard:return route('qwen','describe_figure',hard)
        if tokens and low<=.1:
            return {**route('light_ocr_complete',reasons=['readable_visual_labels']),
                'representation_scope':'literal_labels_only','visual_structure_status':'unassessed'}
        return route('review',reasons=['visual_labels_incomplete'])
    if tokens and low<=.1:return route('light_ocr_complete',reasons=['readable_text'])
    return route('review',reasons=['text_ocr_incomplete'])


def usable_native(asset):
    """Reuse only a selected, source-bound native representation with no issues."""
    base=asset['base'];selected=asset.get('selected') or {}
    return (asset['asset_type'] in ('table','text')
        and base.get('origin') in ('native_table_grid','native_text_layer')
        and bool(base.get('native_text_provenance'))
        and selected.get('origin')==base['origin']
        and bool(selected.get('content','').strip())
        and selected['content']==base['content']
        and not (base.get('quality') or {}).get('issues'))


def figure_group_route(members,review=None):
    """Keep region OCR proof when rebuilding a logical figure parent."""
    proofs=[(m.get('light_region_ocr') or {}).get('provenance')
        or m['base'].get('light_ocr_provenance') for m in members]
    if not any(p is not None for p in proofs):return None  # explicit legacy replay
    if any(p is None or p.get('ocr') is None for p in proofs):
        return decide('figure_group')
    combined={'tokens':[t for p in proofs for t in p['ocr'].get('tokens',[])]}
    complexity={key:any((m.get('region_structure') or {}).get('visual_complexity',{}).get(key)
        or (p.get('visual_complexity') or {}).get(key) for m,p in zip(members,proofs))
        for key in ('chart_axes','dense_diagram','overlapping_labels')}
    if review and review.get('visual_category')=='chart_panels':complexity['chart_axes']=True
    return decide('figure_group',light=combined,complexity=complexity)


def reapply(asset):
    if asset.get('projection_grid'):
        from .projection_grid import eligible as projection_eligible
        if projection_eligible(asset):
            asset['route']={**decide('table',native_complete=True),'next_stage':'light_ocr_complete',
                'reasons':['complete_disjoint_source_ocr_row_and_column_bands']}
            return
    proof=asset['base'].get('light_ocr_provenance')
    native=asset['base'].get('native_text_provenance')
    region=asset.get('light_region_ocr')
    structure=asset.get('region_structure')
    if proof is not None or native or region:
        provenance=(region or {}).get('provenance') or proof or {}
        quality=(region or {}).get('quality') if region and not usable_native(asset) else asset['base'].get('quality')
        complexity=provenance.get('grid',{}).get('complexity')
        if structure and not usable_native(asset):
            quality=structure.get('quality',quality)
            grid=structure.get('grid',{})
            if asset['asset_type']=='table' and not (quality or {}).get('issues') and grid.get('html'):
                asset['route']={**decide('table',quality,native_complete=True),
                    'next_stage':'source_geometry_complete' if structure['selected_origin']=='source_geometry_table_candidate'
                        else 'light_ocr_complete','reasons':['source_bound_grid_recovered']}
                return
            existing=structure.get('existing_candidate_quality')
            if existing is not None and not existing['issues']:
                asset['route']={**decide('table',existing,native_complete=True),
                    'next_stage':'existing_table_complete','reasons':['existing_table_body_coverage_checked']}
                return
            complexity=grid.get('complexity',{}) if asset['asset_type']=='table' else structure.get('visual_complexity',{})
        asset['route']=decide(asset['asset_type'],quality,
            native_complete=usable_native(asset),light=provenance.get('ocr'),
            complexity=complexity)
