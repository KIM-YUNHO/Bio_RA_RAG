"""Run the current project's region OCR and source structure stages before Qwen.

Production functions and guards are imported unchanged. No gold, product ID,
page-specific tuning, chunks, embeddings, or retrieval enter this adapter.
"""
import copy,time
from collections import Counter
import fitz
from PIL import Image
from ..common import write,sha

VERSION='benchmark-current-complete-regions-1'

def refine_regions(assets,groups,path,work,parser):
    from preprocess.visual_router import usable_native,reapply,REVIEW_ONLY
    from preprocess.light_refinement import VERSION as LIGHT_VERSION
    from preprocess.hybrid import cache_write
    from preprocess.ingest import stable_hash
    from preprocess.simple_grid import reconstruct
    from preprocess.table_quality import inspect_table
    from preprocess.region_structure import analyze
    from preprocess.projection_grid import proof,ORIGIN
    from preprocess.grid_refinement import apply_asset
    from preprocess.native_table_fragments import prepare as fragment,ORIGIN as FRAGMENT_ORIGIN
    started=time.perf_counter();counts=Counter();fingerprint=None
    before_empty=sum(not a['selected']['content'].strip() for a in assets if a['asset_type']=='table')
    with fitz.open(path) as pdf:
        for asset in assets:
            if asset.get('is_logical_group') or asset['asset_type'] not in ('table','figure'):continue
            page=pdf[asset['pdf_page']-1]
            quality=asset['base'].get('quality') or {}
            native_ok=usable_native(asset)
            review_only=asset['asset_type']=='table' and quality.get('issues') and set(quality['issues'])<=REVIEW_ONLY
            if not native_ok and not review_only:
                if fingerprint is None:fingerprint=parser.fingerprint()
                crop=work/'light_crops'/f"{asset['asset_id']}.png";crop.parent.mkdir(exist_ok=True)
                clip=fitz.Rect(asset['bbox_pdf'])*page.rotation_matrix
                pix=page.get_pixmap(dpi=300,clip=clip,alpha=False);pix.save(crop)
                metadata={'version':LIGHT_VERSION,'model':fingerprint,'asset_id':asset['asset_id'],
                    'source_refs':asset['source_refs'],'crop_path':str(crop),'crop_sha256':sha(crop),
                    'image_size':[pix.width,pix.height],'dpi':300,'numeric_evidence_eligible':False}
                raw=parser.ocr(crop);counts['region_ocr_calls']+=1
                raw_path=work/'light_raw'/f'{stable_hash(metadata)}.json'
                cache_write(raw_path,stable_hash(metadata),{'metadata':metadata,'raw':raw})
                provenance=copy.deepcopy(raw['ra_light_ocr']['0'])
                grid=None
                if asset['asset_type']=='table':
                    with Image.open(crop) as image:grid=reconstruct(image,provenance['ocr'])
                    provenance['grid']=grid
                reference=asset['native_reference']
                updated=inspect_table(grid['html'],reference['text'],reference['reliable']) if grid else None
                asset['light_region_ocr']={'metadata':metadata,'raw_path':str(raw_path),'raw_sha256':sha(raw_path),
                    'provenance':provenance,'quality':updated}
                asset['region_structure']=analyze(asset,page,assets,groups)
                reapply(asset)
                structure=asset['region_structure'] or {}
                if asset['asset_type']=='table':
                    projected=proof(asset)
                    if projected:
                        asset['projection_grid']=projected
                        asset['selected']={'origin':ORIGIN,'content':projected['html']}
                        asset['validation_status']='derived_unverified';reapply(asset)
                        counts['projection_tables']+=1
                    elif asset['route']['next_stage'] in ('source_geometry_complete','light_ocr_complete') and structure.get('grid',{}).get('html'):
                        asset['selected']={'origin':structure['selected_origin'],'content':structure['grid']['html']}
                        asset['validation_status']='derived_unverified';counts['structure_tables']+=1
                    elif asset['route']['next_stage']=='existing_table_complete':
                        asset['selected']={'origin':asset['base']['origin'] if asset['base']['origin']=='native_table_grid' else 'paddle',
                            'content':asset['base']['content']};counts['existing_tables']+=1
                    else:
                        outcome=apply_asset(asset,page);counts[outcome]+=1
                        # Preserve the source-fixed completion route. reapply()
                        # handles earlier region stages, not this later stage.
            if asset['asset_type']=='table':
                closed=fragment(asset,page)
                if closed:
                    asset['native_table_fragment']=closed
                    asset['selected']={'origin':FRAGMENT_ORIGIN,'content':closed['fragment']['html']}
                    asset['route']={'task':None,'next_stage':'native_closed_rows_complete',
                        'policy_version':closed['version'],'reasons':['physical_native_header_and_closed_row_groups'],
                        'numeric_evidence_eligible':False}
                    asset['validation_status']='derived_unverified';counts['closed_native_fragments']+=1
            counts['native_reused']+=bool(native_ok)
            print(f"CUR refinement p{asset['pdf_page']} {asset['asset_type']} {asset['route'].get('next_stage')}",flush=True)
    result={'version':VERSION,'wall_seconds':time.perf_counter()-started,'region_ocr_dpi':300,
        'stages':['ordinary_region_ocr','region_structure','projection_grid','source_fixed_grid','closed_native_fragments'],
        **dict(counts),'table_origins':dict(Counter(a['selected']['origin'] for a in assets if a['asset_type']=='table')),
        'before_empty_selected_tables':before_empty,
        'empty_selected_tables':sum(not a['selected']['content'].strip() for a in assets if a['asset_type']=='table'),
        'gold_consulted':False}
    write(work/'current_stage_summary.json',result)
    return result
