"""Recover self-contained native row blocks without resolving continuation.

Only source borders and native glyphs supply cells. A repeated header must be
closed before data. Incomplete boundary blocks remain outside retrieval.
"""
from copy import deepcopy
import re
from preprocess.ingest import stable_hash
from preprocess.tables.table_representation import render_structured_table

VERSION='native-closed-table-fragment-1'
ORIGIN='native_closed_table_fragment'

def fragment(grid):
    if not grid or grid.get('geometry_status')!='source_borders_and_glyph_coverage_checked' or grid['quality']['issues']:
        return None
    source=grid['logical_grid'];rows=source['rows'];columns=source['column_count']
    if columns<3:return None
    # Bare observations identify the first data row. N=... and dose/unit labels
    # do not count as outcomes; this stage does not interpret endpoint semantics.
    numeric=lambda s:bool(re.fullmatch(r'\s*[-+−]?\d+(?:[.,]\d+)?\s*',s))
    start=next((i for i,r in enumerate(rows) if any(c['column_index']>=2 and numeric(c['text']) for c in r['cells'])),None)
    if start is None or start==0:return None
    header=rows[:start]
    if any(i+c['row_span']>start for i,r in enumerate(header) for c in r['cells']):return None
    # Each value column needs its own printed, nonempty header.
    if any(not any(c['column_index']<=col<c['column_index']+c['col_span'] and c['text'].strip()
            for r in header for c in r['cells']) for col in range(2,columns)):return None
    groups=[];cursor=start
    while cursor<len(rows):
        labels=[c for c in rows[cursor]['cells'] if c['column_index']==0 and c['col_span']==1 and c['text'].strip()]
        if len(labels)!=1 or labels[0]['row_span']<=1:break
        end=cursor+labels[0]['row_span']
        if end>len(rows):break
        if any(i+c['row_span']>end for i in range(cursor,end) for c in rows[i]['cells']):break
        groups.append([cursor,end]);cursor=end
    if not groups:return None
    selected=deepcopy(source);selected['rows']=deepcopy(rows[:cursor])
    for i,r in enumerate(selected['rows']):r['section']='header' if i<start else 'body'
    html,issues=render_structured_table(selected)
    if issues:return None
    return {'version':VERSION,'html':html,'header_source_rows':[0,start],'closed_source_row_groups':groups,
        'excluded_source_rows':list(range(cursor,len(rows))),'cross_page_relationship':'unresolved',
        'semantic_approval':False,'numeric_evidence_eligible':False}

def binding(asset):
    return {k:deepcopy(asset[k]) for k in ('asset_id','document_id','version_id','pdf_page','source_refs',
        'bbox_pdf','bbox_input_px','input_image_size','base')}

def prepare(asset,page,data=None):
    if asset['asset_type']!='table' or asset.get('continuation_status')!='data_fragment_continuation_candidate':return None
    from preprocess.tables.region_structure import native_grid
    grid=native_grid(page,asset,data);part=fragment(grid)
    if not part:return None
    return {'version':VERSION,'source_binding_sha256':stable_hash(binding(asset)),
        'grid':grid,'fragment':part,'semantic_approval':False}

def eligible(asset):
    proof=asset.get('native_table_fragment')
    if not proof or proof.get('version')!=VERSION or proof.get('source_binding_sha256')!=stable_hash(binding(asset)):return False
    part=fragment(proof.get('grid'))
    return bool(part and part==proof.get('fragment') and asset['selected']=={'origin':ORIGIN,'content':part['html']})

def refine(source,out,eligible_versions=None,protected=()):
    import json
    from collections import Counter
    from contextlib import ExitStack
    from pathlib import Path
    import fitz
    from preprocess.files import read_jsonl
    from preprocess.outputs import save_outputs
    from preprocess.ingest import assert_sources
    from preprocess.extraction.hybrid_validate import validate_extraction
    from preprocess.ingest import Document, assert_model_execution_allowed, sha256
    from preprocess.files import write_json
    from preprocess.extraction.planned_enrichment import snapshot
    from preprocess.files import exclusive
    source,out=Path(source).resolve(),Path(out).resolve()
    manifest=json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'))
    documents=[Document(**d) for d in manifest['documents']]
    assert_model_execution_allowed(documents);assert_sources(documents)
    if any(d.version_id in protected or eligible_versions is not None and d.version_id not in eligible_versions for d in documents):
        raise ValueError('Out-of-scope native fragment input')
    snapshot(source,out)
    with exclusive(out),ExitStack() as stack:
        assets=read_jsonl(out/'extracted_assets.jsonl');docs={d.version_id:d for d in documents};pdfs={};counts=Counter()
        before=sha256(source/'extraction_integrity.json');recovered=[]
        for a in assets:
            if a['asset_type']!='table' or a.get('continuation_status')!='data_fragment_continuation_candidate':continue
            doc=docs[a['version_id']]
            if doc.pdf_path not in pdfs:pdfs[doc.pdf_path]=stack.enter_context(fitz.open(doc.pdf_path))
            proof=prepare(a,pdfs[doc.pdf_path][a['pdf_page']-1])
            if not proof:counts['unresolved_native_fragments']+=1;continue
            a['superseded_native_fragment_selection']=deepcopy(a['selected'])
            if a.get('enrichment'):a['superseded_native_fragment_enrichment']=deepcopy(a['enrichment'])
            a['enrichment']=None;a['native_table_fragment']=proof
            a['selected']={'origin':ORIGIN,'content':proof['fragment']['html']}
            a['route']={'task':None,'next_stage':'native_closed_rows_complete','policy_version':VERSION,
                'reasons':['physical_native_header_and_closed_row_groups'],'numeric_evidence_eligible':False}
            a['validation_status']='derived_unverified';counts['recovered_fragments']+=1
            recovered.append({'asset_id':a['asset_id'],'document_id':a['document_id'],'pdf_page':a['pdf_page'],
                'closed_source_row_groups':proof['fragment']['closed_source_row_groups'],
                'excluded_source_rows':proof['fragment']['excluded_source_rows']})
        result={'version':VERSION,'source':str(source),'source_integrity_sha256':before,**dict(counts),
            'recovered':recovered,'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0}
        summary=json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'));summary['native_table_fragments']=result
        manifest.pop('status_run_id',None);manifest['native_table_fragments_version']=VERSION;manifest['status']='partial'
        write_json(out/'extraction_manifest.json',manifest)
        save_outputs(out,assets,read_jsonl(out/'extraction_pages.jsonl'),documents,summary,read_jsonl(out/'visual_groups.jsonl'))
        validate_extraction(out)
        if sha256(source/'extraction_integrity.json')!=before:raise ValueError('Source bundle changed during native fragment recovery')
        return result
