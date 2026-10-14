"""Rebuild the source adapter without changing the previous extraction/index."""
import argparse,copy,json,math,shutil,time
from collections import Counter,defaultdict
from pathlib import Path
import fitz
from pipelines.pipeline_h.prepare import read,write
from pipelines.pipeline_h.html_source import parse
from pipelines.pipeline_h.exclusion_audit import normalized
from preprocess.ingest import Document,sha256,stable_hash
from preprocess.hybrid import read_jsonl,save_rows,save_outputs
from preprocess.table_quality import inspect_table

def native_recovery(page,block):
    l,b,r,t=block['bbox'];hint=fitz.Rect(l,page.rect.height-t,r,page.rect.height-b)*page.derotation_matrix
    matches=[]
    for rect in page.search_for(block.get('text','')):
        overlap=max(0,min(rect.x1,hint.x1)-max(rect.x0,hint.x0))
        if overlap<.7*max(.01,hint.width):continue
        dist=abs((rect.y0+rect.y1)/2-(hint.y0+hint.y1)/2)
        if dist<=30 and not rect.is_empty:matches.append((dist,rect))
    matches.sort(key=lambda x:x[0])
    if not matches or len(matches)>1 and matches[1][0]-matches[0][0]<1:return None
    return list(matches[0][1])

def append_dom(doc,run,pages,assets):
    dom=read(run/'html_dom_preview'/(doc.version_id+'.json'))
    with fitz.open(doc.pdf_path) as pdf:
        images={}
        for n,page in enumerate(pdf,1):
            image=run/'dom_page_images'/doc.version_id[:16]/f'{n:04d}.png';image.parent.mkdir(parents=True,exist_ok=True)
            page.get_pixmap(dpi=100,alpha=False).save(image);images[n]=str(image)
            pix=page.get_pixmap(dpi=100,alpha=False)
            pages.append({'page_id':stable_hash([doc.document_id,doc.version_id,'dom-preview',n])[:24],
                'document_id':doc.document_id,'version_id':doc.version_id,'pdf_page':n,'pdf_page_label':str(n),'status':'parsed',
                'physical_pdf_page':None,'pdf_catalog_page_label':None,'printed_page_candidates':[],
                'coverage':'original DOM unit coverage separately verified; preview is not the content source',
                'original_rotation':0,'image_path':str(image),'image_size':[pix.width,pix.height],
                'raw_result_path':str(run/'html_dom_preview'/(doc.version_id+'.json')),
                'invalid_blocks':[],'docling_route':{'route':'HTML_DOM','page':n},'locator_notice':'Generated DOM preview page; not an original HTML page number'})
        for order,(u,loc) in enumerate(zip(dom['units'],dom['locations'])):
            n=loc['pdf_page'];page=pdf[n-1];box=loc['bbox_pdf'];p=next(p for p in pages if p['document_id']==doc.document_id and p['pdf_page']==n);size=p['image_size']
            px=[v*(size[0]/page.rect.width if i%2==0 else size[1]/page.rect.height) for i,v in enumerate(box)]
            ref=doc.source_ref(n,box);ref.update(content_locator_type='html_dom_node',html_ref=u['dom_ref'])
            kind='table' if u['kind']=='table' else 'text';quality=inspect_table(u['text']) if kind=='table' else None
            label='paragraph_title' if u['tag'].startswith('h') and u['tag'][1:].isdigit() else 'text'
            assets.append({'schema_version':'ra-extraction-1','asset_id':u['id'],'document_id':doc.document_id,'version_id':doc.version_id,
                'pdf_page':n,'asset_type':kind,'block_label':label,'block_order':order,'bbox_pdf':box,'bbox_input_px':px,'input_image_size':size,
                'coordinate_space':'pymupdf_unrotated_crop_points_top_left','source_refs':[ref],'page_image':images[n],
                'base':{'engine':'Python HTMLParser original DOM','origin':'original_html_dom','content':u['text'],'quality':quality},
                'native_reference':{'text':page.get_text('text',clip=fitz.Rect(box)),'reliable':True},
                'route':{'task':None,'next_stage':'complete','reasons':['original_dom_content_preserved']},'enrichment':None,
                'selected':{'origin':'original_html_dom','content':u['text']},'validation_status':'extracted_unverified','numeric_evidence_eligible':False,
                'source_structure':u,'h_candidates':[],'original_extraction_path':p['raw_result_path'],
                'document_metadata_ref':str(run/'intake_manifest.json')})
    return dom

def build(previous,run):
    previous=Path(previous).resolve();run=Path(run).resolve();run.mkdir(exist_ok=True)
    intake=copy.deepcopy(read(previous/'intake_manifest.json'));original=copy.deepcopy(intake['documents'])
    for name in ('catalog_snapshot.json','products_snapshot.json','docling_progress.json','docling_runtime.json','selection_manifest.json','candidate_decisions.json'):
        shutil.copyfile(previous/name,run/name)
    for name in ('layout_runtime.json','vlm_runtime.json','fix_regression.json'):
        if (previous/name).exists():shutil.copyfile(previous/name,run/name)
    docs=[];doms={}
    for d in intake['documents']:
        if d['kind']=='html':
            dom=read(run/'html_dom_preview'/(d['version_id']+'.json'));doms[d['document_id']]=dom
            d['rendered_path']=dom['rendered_path'];d['rendered_sha256']=dom['rendered_sha256']
        docs.append(Document(**d))
    intake.update(parent_run=str(previous),parent_intake_sha256=sha256(previous/'intake_manifest.json'),
        original_document_contract=original,repair_method='HTML DOM primary; verified native glyph geometry; warning/content status separation')
    write(run/'intake_manifest.json',intake)
    out=run/'extraction';out.mkdir(exist_ok=True);pdf_ids={d.document_id for d in docs if d.kind=='pdf'}
    assets=[a for a in read_jsonl(previous/'extraction/extracted_assets.jsonl') if a['document_id'] in pdf_ids]
    pages=[p for p in read_jsonl(previous/'extraction/extraction_pages.jsonl') if p['document_id'] in pdf_ids]
    groups=[g for g in read_jsonl(previous/'extraction/visual_groups.jsonl') if g['document_id'] in pdf_ids]
    old_by_id={d['document_id']:d for d in original};by_id={d.document_id:d for d in docs};warnings=[];repairs=[]
    by_page=defaultdict(list)
    for a in assets:by_page[(a['document_id'],a['pdf_page'])].append(a)
    for p in pages:
        if p['status']=='parsed':continue
        doc=by_id[p['document_id']];remaining=[]
        with fitz.open(doc.pdf_path) as pdf:
            page=pdf[p['pdf_page']-1]
            for e in p.get('invalid_blocks',[]):
                b=e['block'];box=native_recovery(page,b) if b['type']=='text' else None
                if box:
                    template=next(a for a in by_page[doc.document_id,p['pdf_page']] if a['asset_type']=='text')
                    a=copy.deepcopy(template);aid='geo'+stable_hash([doc.document_id,doc.version_id,p['pdf_page'],b['ref'],box])[:28]
                    display=fitz.Rect(box)*page.rotation_matrix;size=p['image_size']
                    px=[v*(size[0]/page.rect.width if i%2==0 else size[1]/page.rect.height) for i,v in enumerate(display)]
                    a.update(asset_id=aid,bbox_pdf=box,bbox_input_px=px,block_label=b.get('label','text'),
                        block_order=template.get('block_order',0)+.001,source_refs=[doc.source_ref(p['pdf_page'],box)],
                        docling_structure=b,h_candidates=[],validation_status='extracted_unverified',
                        base={'engine':'PyMuPDF exact text and native glyph bbox','origin':'verified_native_geometry_recovery','content':b['text'],'quality':None},
                        native_reference={'text':page.get_text('text',clip=fitz.Rect(box)),'reliable':True},
                        selected={'origin':'verified_native_geometry_recovery','content':b['text']},
                        route={'task':None,'next_stage':'complete','reasons':['native_source_geometry_recovered']},enrichment=None,
                        geometry_repair={'raw_bbox_bottom_left':b['bbox'],'native_bbox_top_left':box,'raw_text_unchanged':True})
                    assets.append(a);repairs.append({'document_id':doc.document_id,'pdf_page':p['pdf_page'],'asset_id':aid,'source_block_ref':b['ref'],'raw_text':b['text'],'bbox_pdf':box})
                elif b['type']=='text' and len(normalized(b.get('text','')))<=12 and abs(b['bbox'][3]-b['bbox'][1])<=.11:
                    # The raw content remains in its source block and review
                    # record. A failed tiny glyph locator cannot exclude all
                    # other successfully sourced content on the parent page.
                    warnings.append({'document_id':doc.document_id,'pdf_page':p['pdf_page'],'block':b,
                        'reason':'unresolved_degenerate_glyph_locator','source_pdf':doc.pdf_path,
                        'source_page_image':p['image_path'],'content_not_certified':True})
                else:remaining.append(e)
        p.update(invalid_blocks=remaining,geometry_warnings=[w for w in warnings if w['document_id']==doc.document_id and w['pdf_page']==p['pdf_page']],
            status='partial' if remaining else 'parsed')
    for doc in docs:
        if doc.kind=='html':append_dom(doc,run,pages,assets)
    for a in assets:a['document_metadata_ref']=str(run/'intake_manifest.json')
    pages.sort(key=lambda p:(next(i for i,d in enumerate(docs) if d.document_id==p['document_id']),p['pdf_page']))
    summary={'pipeline':'production-h-4-source-content-geometry','documents':len(docs),'pages':len(pages),'assets':len(assets),
        'html_dom_units':sum(len(d['units']) for d in doms.values()),'native_geometry_recovered':len(repairs),'geometry_warning_blocks':len(warnings),
        'parent_run':str(previous),'by_asset_type':dict(Counter(a['asset_type'] for a in assets)),'figure_meaning_generation':False}
    from preprocess import figure_labels
    from pipelines.pipeline_h.figure_literals import apply
    original_apply=figure_labels.apply
    try:figure_labels.apply=apply;save_outputs(out,assets,pages,docs,summary,groups)
    finally:figure_labels.apply=original_apply
    save_rows(out/'geometry_warnings.jsonl',warnings);write(out/'geometry_repairs.json',repairs)
    save_rows(out/'html_dom_inventory.jsonl',[{'document_id':d,'raw_path':v['raw_path'],'raw_sha256':v['raw_sha256'],
        'nodes':[u['dom_ref'] for u in v['units']],'explicit_exclusions':v['explicit_exclusions'],'content_primary':'original_html_dom'} for d,v in doms.items()])
    shutil.copyfile(previous/'extraction/document_structures.jsonl',out/'document_structures.jsonl')
    manifest=read(previous/'extraction/extraction_manifest.json');manifest.update(documents=[d.to_dict() for d in docs],pipeline_version=summary['pipeline'],
        status='partial' if any(p['status']!='parsed' for p in pages) else 'extraction_complete',
        source_metadata_contract='original catalog metadata unchanged; DOM preview derivative lineage separate',
        original_intake_sha256=sha256(previous/'intake_manifest.json'),parent_run=str(previous))
    write(out/'extraction_manifest.json',manifest);write(out/'processing_status.json',{'lifecycle':'finished','state':manifest['status']})
    from preprocess.hybrid_validate import validate_extraction
    report=validate_extraction(out);write(out/'validation_report.json',report)
    write(run/'repair_summary.json',summary);return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--previous',required=True);p.add_argument('--run',required=True);a=p.parse_args()
    print(build(a.previous,a.run))
