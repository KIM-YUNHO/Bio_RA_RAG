"""Complete borderless grids from disjoint printed OCR row/column bands.

Wrapped lines can share a physical row despite unequal text-line counts. No
missing cell, overlapping column title or low-confidence token is filled in.
"""
from copy import deepcopy
import re
from preprocess.tables.table_representation import render_structured_table
from preprocess.ingest import stable_hash

VERSION='source-ocr-projection-grid-1'
ORIGIN='source_ocr_projection_grid'

def bands(intervals):
    result=[]
    for a,b in sorted(intervals):
        if result and a<=result[-1][1]+1:result[-1][1]=max(b,result[-1][1])
        else:result.append([a,b])
    return result

def reconstruct(ocr):
    tokens=ocr.get('tokens',[]);size=ocr.get('image_size')
    if not size or not tokens or any(t['confidence']<.9 or not t['text'].strip() for t in tokens):return None
    if any(not (0<=t['bbox'][0]<t['bbox'][2]<=size[0] and 0<=t['bbox'][1]<t['bbox'][3]<=size[1]) for t in tokens):return None
    xs=bands((t['bbox'][0],t['bbox'][2]) for t in tokens);ys=bands((t['bbox'][1],t['bbox'][3]) for t in tokens)
    if not 2<=len(xs)<=16 or not 3<=len(ys)<=100:return None
    # The source must supply a complete rectangular inventory. Projection gaps
    # alone do not authorize reconstructing merged headings or absent cells.
    positions={};physical=[]
    for i,t in enumerate(tokens):
        xx=[c for c,(a,b) in enumerate(xs) if a<=t['bbox'][0]<=t['bbox'][2]<=b]
        yy=[r for r,(a,b) in enumerate(ys) if a<=t['bbox'][1]<=t['bbox'][3]<=b]
        if len(xx)!=1 or len(yy)!=1:return None
        positions.setdefault((yy[0],xx[0]),[]).append(i)
    if len(positions)!=len(xs)*len(ys):return None
    rows=[]
    for r,y in enumerate(ys):
        row={'row_index':r,'section':'header' if r==0 else 'body','cells':[]}
        for c,x in enumerate(xs):
            indices=sorted(positions[(r,c)],key=lambda i:(tokens[i]['bbox'][1],tokens[i]['bbox'][0]))
            text='\n'.join(tokens[i]['text'] for i in indices)
            if c>0:
                numeric=bool(re.match(r'^\s*[(+−–-]*\s*\d',text))
                if numeric!=(r>0):return None
            row['cells'].append({'column_index':c,'row_span':1,'col_span':1,'text':text,'state':'visible'})
            physical.append({'row':r,'column':c,'bbox_crop_px':[x[0],y[0],x[1],y[1]],
                'ocr_token_indices':indices,'text':text})
        rows.append(row)
    logical={'column_count':len(xs),'rows':rows,'caption':None,'footnotes':[],'uncertainties':[]}
    html,issues=render_structured_table(logical)
    if issues:return None
    return {'version':VERSION,'html':html,'logical_grid':logical,'physical_cells':physical,
        'column_bands_crop_px':xs,'row_bands_crop_px':ys,'ocr_sha256':stable_hash(ocr),
        'geometry_status':'complete_disjoint_ocr_projection_bands','numeric_evidence_eligible':False,'semantic_approval':False}

def proof(asset):
    light=asset.get('light_region_ocr')
    if not light:return None
    result=reconstruct(light['provenance']['ocr'])
    if result:
        from preprocess.tables.table_quality import inspect_table
        native=asset.get('native_reference') or {}
        result['quality']=inspect_table(result['html'],native.get('text',''),native.get('reliable',False))
        if result['quality']['issues']:return None
        result['source_binding_sha256']=stable_hash([asset['asset_id'],asset['source_refs'],light['metadata'],light['raw_sha256'],native])
    return result

def eligible(asset):
    p=asset.get('projection_grid')
    return bool(p and proof(asset)==p and asset['selected']=={'origin':ORIGIN,'content':p['html']})

def refine(source,out,task_ids):
    import json
    from pathlib import Path
    from preprocess.files import read_jsonl
    from preprocess.outputs import save_outputs
    from preprocess.ingest import assert_sources
    from preprocess.extraction.hybrid_validate import validate_extraction
    from preprocess.ingest import Document, assert_model_execution_allowed, sha256
    from preprocess.files import write_json
    from preprocess.extraction.planned_enrichment import snapshot
    from preprocess.files import exclusive
    source,out=Path(source).resolve(),Path(out).resolve()
    manifest=json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'));docs=[Document(**d) for d in manifest['documents']]
    assert_model_execution_allowed(docs);assert_sources(docs);snapshot(source,out)
    before=sha256(source/'extraction_integrity.json')
    with exclusive(out):
        assets=read_jsonl(out/'extracted_assets.jsonl');lookup={a['asset_id']:a for a in assets}
        if not set(task_ids)<=set(lookup):raise ValueError('Unknown source projection target')
        recovered=[]
        for uid in task_ids:
            a=lookup[uid];p=proof(a)
            if not p:continue
            a['projection_grid']=p;a['superseded_projection_selection']=deepcopy(a['selected'])
            if a.get('enrichment'):a['superseded_projection_enrichment']=deepcopy(a['enrichment'])
            a['enrichment']=None;a['selected']={'origin':ORIGIN,'content':p['html']};a['validation_status']='derived_unverified'
            a['route']={'task':None,'next_stage':'light_ocr_complete','policy_version':VERSION,
                'reasons':['complete_disjoint_source_ocr_row_and_column_bands'],'numeric_evidence_eligible':False}
            recovered.append(uid)
        summary=json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'))
        result={'version':VERSION,'source':str(source),'source_integrity_sha256':before,'recovered':recovered,
            'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0}
        summary['projection_grid_refinement']=result;manifest.pop('status_run_id',None);manifest['status']='partial'
        write_json(out/'extraction_manifest.json',manifest)
        save_outputs(out,assets,read_jsonl(out/'extraction_pages.jsonl'),docs,summary,read_jsonl(out/'visual_groups.jsonl'))
        validate_extraction(out)
        if sha256(source/'extraction_integrity.json')!=before:raise ValueError('Projection refinement changed its source')
        return result
