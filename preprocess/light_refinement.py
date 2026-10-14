"""Region-only ordinary OCR over immutable completed extraction bundles."""
from copy import deepcopy
import io,json
from pathlib import Path
import fitz
from PIL import Image
from .hybrid import read_jsonl,save_outputs,cache_read,cache_write,assert_sources
from .hybrid_validate import validate_extraction
from .ingest import Document,sha256,stable_hash,assert_model_execution_allowed
from .inventory import write_json
from .planned_enrichment import snapshot,TERMINAL
from .table_quality import inspect_table
from .visual_router import decide,REVIEW_ONLY,usable_native,reapply,figure_group_route
from .workflow import exclusive

VERSION='region-light-refinement-3'


def refine(source,out,task_ids,config,backend,notify=None):
    source,out=Path(source).resolve(),Path(out).resolve()
    source_manifest=json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'))
    documents=[Document(**d) for d in source_manifest['documents']]
    assert_model_execution_allowed(documents);assert_sources(documents)
    snapshot(source,out)
    with exclusive(out):
        manifest=json.loads((out/'extraction_manifest.json').read_text(encoding='utf-8'))
        assets=read_jsonl(out/'extracted_assets.jsonl');lookup={a['asset_id']:a for a in assets}
        groups=read_jsonl(out/'visual_groups.jsonl');docs={d.version_id:d for d in documents}
        if not set(task_ids)<=set(lookup):raise ValueError('Unknown frozen task id')
        fingerprint=backend.fingerprint();calls=reused=held=native_reused=0
        before=sha256(source/'extraction_integrity.json')
        for uid in task_ids:
            target=lookup[uid]
            if (target.get('enrichment') or {}).get('status') in TERMINAL:continue
            if target.get('enrichment'):
                target['superseded_pending_enrichment']=deepcopy(target['enrichment'])
                target['enrichment']=None
            quality=target['base'].get('quality') or {}
            if usable_native(target):
                target['route']=decide(target['asset_type'],quality,native_complete=True)
                native_reused+=1;continue
            if target['asset_type']=='table' and quality.get('issues') and set(quality['issues'])<=REVIEW_ONLY:
                target['route']=decide('table',quality);held+=1;continue
            members=[lookup[i] for i in target.get('member_asset_ids',[uid])]
            for member in members:
                if usable_native(member):
                    member['route']=decide(member['asset_type'],member['base'].get('quality'),native_complete=True)
                    native_reused+=1;continue
                if member.get('light_region_ocr'):
                    reapply(member);continue
                doc=docs[member['version_id']];assert_model_execution_allowed([doc])
                crop=out/'light_crops'/f'{member["asset_id"]}.png';crop.parent.mkdir(exist_ok=True)
                with fitz.open(doc.pdf_path) as pdf:
                    page=pdf[member['pdf_page']-1]
                    # Original region coordinates are unrotated crop points.
                    clip=fitz.Rect(member['bbox_pdf'])*page.rotation_matrix
                    pix=page.get_pixmap(dpi=300,clip=clip,alpha=False);pix.save(crop)
                metadata={'version':VERSION,'model':fingerprint,'asset_id':member['asset_id'],
                    'source_refs':member['source_refs'],'crop_path':str(crop),'crop_sha256':sha256(crop),
                    'image_size':[pix.width,pix.height],'dpi':300,'numeric_evidence_eligible':False}
                key=stable_hash(metadata);raw_path=out/'light_raw'/f'{key}.json'
                payload=cache_read(raw_path,key)
                if payload is None:
                    if notify:notify(asset=member['asset_id'],document=doc.document_id,page=member['pdf_page'],calls=calls)
                    raw=backend.ocr(crop);payload={'metadata':metadata,'raw':raw};cache_write(raw_path,key,payload);calls+=1
                else:reused+=1
                raw=payload['raw'];proof=raw['ra_light_ocr']['0']
                result=deepcopy(proof['ocr'])
                grid=None;updated_quality=None
                if member['asset_type']=='table':
                    from .simple_grid import reconstruct
                    with Image.open(crop) as image:grid=reconstruct(image,result)
                    native=member['native_reference']
                    updated_quality=inspect_table(grid['html'],native['text'],native['reliable'])
                member['light_region_ocr']={'metadata':metadata,'raw_path':str(raw_path),'raw_sha256':sha256(raw_path),
                    'provenance':{'ocr':result,**({'grid':grid} if grid else {})},'quality':updated_quality}
                if member['asset_type']=='table' and not (grid or {}).get('complexity',{}).get('simple_grid_complete'):
                    from .projection_grid import proof as projection_proof,ORIGIN as PROJECTION_ORIGIN
                    projected=projection_proof(member)
                    if projected:
                        member['projection_grid']=projected
                        updated_quality=inspect_table(projected['html'],native['text'],native['reliable'])
                        member['selected']={'origin':PROJECTION_ORIGIN,'content':projected['html']}
                        member['validation_status']='derived_unverified'
                        member['route']=decide('table',updated_quality,light=result,complexity={'simple_grid_complete':True})
                        continue
                member['route']=decide(member['asset_type'],updated_quality,
                    light=result,complexity=(grid or {}).get('complexity'))
                if member['asset_type']=='table' and member['route']['next_stage']=='light_ocr_complete':
                    member['selected']={'origin':'pp_ocr_table_candidate','content':grid['html']}
                    member['validation_status']='derived_unverified'
                elif member['asset_type']=='table':
                    from .grid_refinement import apply_asset
                    with fitz.open(doc.pdf_path) as pdf:apply_asset(member,pdf[member['pdf_page']-1])
            if target.get('is_logical_group'):
                target['route']=figure_group_route(members) or decide('figure_group')
                for m in members:
                    m['route']={'task':None,'reasons':['handled_via_logical_figure_group'],'parent_asset_id':uid}
        summary=json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'))
        summary['light_refinement']={'version':VERSION,'source':str(source),'source_integrity_sha256':before,
            'ocr_calls':calls,'cache_reused':reused,'native_reused':native_reused,
            'review_without_ocr':held,'new_qwen_calls':0,'api_calls':0}
        manifest.pop('status_run_id',None);manifest['status']='partial'
        write_json(out/'extraction_manifest.json',manifest)
        save_outputs(out,assets,read_jsonl(out/'extraction_pages.jsonl'),documents,summary,groups)
        validate_extraction(out)
        if sha256(source/'extraction_integrity.json')!=before:raise ValueError('Source bundle changed during refinement')
        return summary['light_refinement']
