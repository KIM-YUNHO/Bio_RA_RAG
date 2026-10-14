"""Freeze H crop jobs from source geometry and extraction alarms, without gold."""
import argparse,copy,time
from pathlib import Path
import fitz
from PIL import Image
from preprocess.ingest import sha256,stable_hash
from pipelines.pipeline_h.prepare import read,write
from pipelines.pipeline_h.structure import box_top_left,table_rows,original_pictures
from pipelines.pipeline_h.policy import VERSION,related,adjacent_visual_context,boxed_regions,table_alarms,SUSPECT_GLYPH
from pipelines.pipeline_h.metadata_tools import normalize_block_metadata

def crop(run,job):
    target=run/'crops'/(job['id']+'.png');target.parent.mkdir(parents=True,exist_ok=True)
    dpi=450 if job['task']=='ocr' else 300;padding=12*72/dpi
    with fitz.open(job['source_pdf']) as pdf:
        page=pdf[job['pdf_page']-1];clip=(fitz.Rect(job['bbox'])+(-padding,-padding,padding,padding))&page.rect
        if clip.is_empty:raise ValueError('Empty crop')
        pix=page.get_pixmap(dpi=dpi,clip=clip,alpha=False);image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
        image.thumbnail((1800,1800),Image.Resampling.LANCZOS);image.save(target)
    job.update(image=str(target),image_sha256=sha256(target),pixels=list(image.size),context_bbox=list(clip),dpi=dpi)

def plan(run):
    run=Path(run).resolve();start=time.perf_counter();intake=read(run/'intake_manifest.json');jobs=[];events=[];states=[]
    for di,doc in enumerate(intake['documents']):
        raw_path=run/'docling'/f"{di:03d}_{doc['version_id']}.json";raw=read(raw_path)
        if raw['status']!='complete':raise ValueError('Incomplete Docling document '+doc['document_id'])
        result=raw['result'];blocks=copy.deepcopy(result['blocks']);source=doc.get('rendered_path') or doc['raw_path']
        suppressed=[];visual_context=[]
        if doc['kind']=='html' and (run/'html_dom_preview'/(doc['version_id']+'.json')).exists():
            normalization=normalize_block_metadata(blocks,doc['metadata'])
            states.append({'document':doc,'raw_path':str(raw_path),'blocks':blocks,'source_metadata_normalization':normalization,
                'suppressed_table_candidates':[],'visual_context_regions':[]})
            events.append({'document_id':doc['document_id'],'action':'original_dom_primary_no_visual_retranscription'})
            continue
        with fitz.open(source) as pdf:
            for number,page in enumerate(pdf,1):
                layout=read(run/'layout'/f'{di:03d}'/f'{number:04d}.json')
                if layout['status']!='complete':raise ValueError('Incomplete layout page')
                sx,sy=page.rect.width/layout['pixels'][0],page.rect.height/layout['pixels'][1]
                ds=[{**d,'bbox':[v*(sx if i%2==0 else sy) for i,v in enumerate(d['coordinate'])]} for d in layout['boxes']]
                before=original_pictures(result,raw['visual_audit'],number,page.rect.height,page.rect.width)
                for d in ds:
                    if d['label']!='chart':continue
                    prior=[p for p in before if related(d['bbox'],p['bbox_top_left'])]
                    # Restore visual source evidence when OCR-grid rules promoted a chart to a table.
                    if prior:
                        remove=[b for b in blocks if b['page']==number and b['type']=='table' and related(d['bbox'],box_top_left(b,page.rect.height,page.rect.width))]
                        for b in remove:blocks.remove(b);suppressed.append({'block':b,'reason':'original_picture_and_independent_chart','numeric_evidence_eligible':False})
                    exists=any(b['type']=='picture' and b['page']==number and related(d['bbox'],box_top_left(b,page.rect.height,page.rect.width)) for b in blocks)
                    if not exists:
                        if prior:b=copy.deepcopy(prior[0]);b.pop('bbox_top_left',None)
                        else:
                            l,t,r,bt=d['bbox'];b={'type':'picture','ref':f'pp-chart-{number}-{len(blocks)}','page':number,'bbox':[l,page.rect.height-bt,r,page.rect.height-t],
                                'caption':'','text':'','source':'PP-DocLayoutV3','layer':'body','status':'source_preserved'}
                        b['chart_preserved']=True;blocks.append(b)
                own=[b for b in blocks if b['page']==number]
                tables=[b for b in own if b['type']=='table' and not b.get('furniture')]
                figures=[{'figure_id':b['ref'],'bbox':box_top_left(b,page.rect.height,page.rect.width),
                          'decorative':(b['bbox'][2]-b['bbox'][0])<page.rect.width*.12 and (b['bbox'][3]-b['bbox'][1])<page.rect.height*.12 and not b.get('caption')}
                         for b in own if b['type']=='picture']
                proposals=[{'bbox':d['bbox'],'origin':'PP-DocLayoutV3 table','score':d['score']} for d in ds if d['label']=='table']
                with Image.open(layout['image']) as image:proposals+=boxed_regions(image,layout['size'])
                pagejobs=[]
                for proposal in proposals:
                    box=proposal['bbox'];matches=[b['ref'] for b in tables if related(box,box_top_left(b,page.rect.height,page.rect.width))]
                    conflicts=[f['figure_id'] for f in figures if not f['decorative'] and related(box,f['bbox'])]
                    adjacent=adjacent_visual_context(box,figures,page.rect.height) if proposal['origin']=='source_closed_border' else None
                    action='keep_existing_table' if matches else 'preserve_figure' if conflicts else 'preserve_visual_context' if adjacent else 'duplicate_region' if any(related(box,j['bbox']) for j in pagejobs) else 'add_missing_region'
                    event={'document_id':doc['document_id'],'pdf_page':number,**proposal,'action':action,'primary_refs':matches,'figure_refs':conflicts}
                    if action=='preserve_visual_context':visual_context.append({**event,'figure_ref':adjacent})
                    if action=='add_missing_region':pagejobs.append({**proposal,'task':'table','action':'add','bbox':box})
                    events.append(event)
                for table in tables:
                    rows=table_rows(table);alarm=table_alarms(rows);box=box_top_left(table,page.rect.height,page.rect.width)
                    glyph_cells=[cell for cell in table['cells'] if SUSPECT_GLYPH.search(cell.get('text',''))]
                    if len(glyph_cells)>=2:
                        alarm={'issues':alarm['issues']+['multiple_ambiguous_special_glyph_labels'],'cells':alarm['cells'],
                               'glyph_cells':len(glyph_cells),'source_context_policy':'keep full table context for ambiguous scientific labels'}
                    if alarm['issues']:
                        pagejobs.append({'task':'table','action':'replace','block_ref':table['ref'],'bbox':box,
                                         'origin':'existing_table_structure_alarm','original_table':rows,'quality_alarm':alarm})
                    else:
                        for ci,cell in enumerate(table['cells']):
                            if not SUSPECT_GLYPH.search(cell.get('text','')):continue
                            if cell.get('bbox'):
                                cb=box_top_left({'bbox':cell['bbox']},page.rect.height,page.rect.width)
                                pagejobs.append({'task':'ocr','action':'glyph','block_ref':table['ref'],'cell_index':ci,'bbox':cb,
                                                 'origin':'ambiguous_special_glyph','original_text':cell['text']})
                            else:events.append({'document_id':doc['document_id'],'pdf_page':number,'action':'review_glyph_without_physical_cell','block_ref':table['ref']})
                for ji,job in enumerate(pagejobs):
                    job.update(document_id=doc['document_id'],version_id=doc['version_id'],document_index=di,pdf_page=number,source_pdf=source)
                    job['id']=f'{di:03d}_p{number:04d}_{job["action"]}_{ji:03d}_{stable_hash([VERSION,doc["version_id"],job])[:12]}'
                    crop(run,job);jobs.append(job)
        normalization=normalize_block_metadata(blocks,doc['metadata'])
        states.append({'document':doc,'raw_path':str(raw_path),'blocks':blocks,'source_metadata_normalization':normalization,
                       'suppressed_table_candidates':suppressed,'visual_context_regions':visual_context})
        print(f'PLAN {di+1}/92 jobs={len(jobs)}',flush=True)
    write(run/'selected_documents.json',states)
    manifest={'version':VERSION,'gold_consulted':False,'selection_frozen_before_model_calls':True,'jobs':jobs,'events':events,
              'intake_sha256':sha256(run/'intake_manifest.json'),'selected_documents_sha256':sha256(run/'selected_documents.json'),
              'planning_seconds':time.perf_counter()-start,'policy':'H plus validation-only footnote normalization and source-only suspicious glyph reread'}
    write(run/'selection_manifest.json',manifest);return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();m=plan(a.run);print('JOBS',len(m['jobs']))
