"""Apply source geometry to sealed OCR results in a new extraction bundle."""
from collections import Counter
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
import fitz
from .hybrid import read_jsonl,save_outputs,assert_sources
from .hybrid_validate import validate_extraction
from .ingest import Document,sha256,assert_model_execution_allowed
from .inventory import write_json
from .planned_enrichment import snapshot
from .region_structure import analyze,VERSION
from .region_review import bind
from .visual_router import reapply,figure_group_route,usable_native
from .workflow import exclusive


def refine(source,out,review_path,notify=None):
    source,out=Path(source).resolve(),Path(out).resolve()
    manifest=json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'))
    documents=[Document(**d) for d in manifest['documents']]
    assert_model_execution_allowed(documents);assert_sources(documents)
    snapshot(source,out)
    with exclusive(out),ExitStack() as stack:
        assets=read_jsonl(out/'extracted_assets.jsonl');groups=read_jsonl(out/'visual_groups.jsonl')
        lookup={a['asset_id']:a for a in assets};docs={d.version_id:d for d in documents};pdfs={}
        counts=Counter();before=sha256(source/'extraction_integrity.json')
        for a in assets:
            if not a.get('light_region_ocr') or a.get('is_logical_group'):continue
            doc=docs[a['version_id']]
            if doc.pdf_path not in pdfs:pdfs[doc.pdf_path]=stack.enter_context(fitz.open(doc.pdf_path))
            a['region_structure']=analyze(a,pdfs[doc.pdf_path][a['pdf_page']-1],assets,groups)
            if a['region_structure'] is None:a.pop('region_structure');continue
            counts['analyzed_regions']+=1
            if a['asset_type']=='table':
                old_selection=deepcopy(a['selected']);reapply(a)
                s=a['region_structure']
                if a['route']['next_stage'] in ('source_geometry_complete','light_ocr_complete') and s.get('grid',{}).get('html'):
                    a['selected']={'origin':s['selected_origin'],'content':s['grid']['html']}
                    a['validation_status']='derived_unverified';counts['recovered_tables']+=1
                elif a['route']['next_stage']=='existing_table_complete':
                    origin=a['base']['origin'] if a['base']['origin']=='native_table_grid' else 'paddle'
                    a['selected']={'origin':origin,'content':a['base']['content']};counts['reused_tables']+=1
                if a['selected']!=old_selection and a.get('enrichment'):
                    a['superseded_enrichment']=deepcopy(a['enrichment']);a['enrichment']=None
                if a['route'].get('next_stage')=='qwen' and (a.get('enrichment') or {}).get('status')=='needs_review':
                    # The new structural input and span contract justify a fresh
                    # candidate. Preserve the rejected transcription for audit.
                    a['superseded_enrichment']=deepcopy(a['enrichment']);a['enrichment']=None
            else:reapply(a)
            if notify and counts['analyzed_regions']%25==0:notify(**dict(counts))
        for a in assets:
            if not a.get('is_logical_group'):continue
            review=bind(a,source,review_path)
            if review:a['visual_structure_review']=review
            members=[lookup[uid] for uid in a['member_asset_ids']]
            route=figure_group_route(members,review)
            if route is None:continue
            a['route']=route;counts['figure_groups_reclassified']+=1
            if route['task']:
                counts['complex_figure_groups']+=1
                if (a.get('enrichment') or {}).get('status')=='needs_review':
                    a['superseded_enrichment']=deepcopy(a['enrichment']);a['enrichment']=None
            for member in members:member['route']={'task':None,'reasons':['handled_via_logical_figure_group'],'parent_asset_id':a['asset_id']}
        summary=json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'))
        summary['structural_refinement']={'version':VERSION,'source':str(source),'source_integrity_sha256':before,
            **dict(counts),'new_ocr_calls':0,'new_qwen_calls':0,'new_api_calls':0}
        manifest['structural_refinement_version']=VERSION;manifest['status']='partial';manifest.pop('status_run_id',None)
        write_json(out/'extraction_manifest.json',manifest)
        save_outputs(out,assets,read_jsonl(out/'extraction_pages.jsonl'),documents,summary,groups)
        validate_extraction(out)
        if sha256(source/'extraction_integrity.json')!=before:raise ValueError('Structural refinement changed its source')
        return summary['structural_refinement']
