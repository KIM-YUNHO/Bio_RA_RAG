"""Source-fixed merged grids, literal native/OCR cells, and bounded VLM gaps."""
from collections import Counter
from copy import deepcopy
from html import escape

import fitz
from PIL import Image

from .ingest import sha256,stable_hash
from .native_text import packet,intersects
from .table_quality import REDACTION,comparable_text
from .table_representation import render_structured_table

VERSION='source-fixed-grid-5-preserved-context'


def validate_geometry(geometry):
    cells=geometry.get('cells',[])
    if not cells or len(cells)>1200:raise ValueError('Missing or oversized physical grid')
    rows=max(c['row']+c['row_span'] for c in cells)
    cols=max(c['column']+c['col_span'] for c in cells)
    if not 2<=rows<=200 or not 2<=cols<=200:raise ValueError('Physical grid dimensions out of range')
    occupied=set()
    for c in cells:
        if any(type(c[k]) is not int for k in ('row','column','row_span','col_span')) or min(c['row'],c['column'])<0 or min(c['row_span'],c['col_span'])<1:
            raise ValueError('Invalid physical cell indices')
        area={(r,col) for r in range(c['row'],c['row']+c['row_span']) for col in range(c['column'],c['column']+c['col_span'])}
        if occupied&area:raise ValueError('Physical cells overlap')
        occupied.update(area)
    if len(occupied)!=rows*cols:raise ValueError('Physical grid positions missing')
    return rows,cols


def pixel_box(asset,page,box):
    r=fitz.Rect(box)*page.rotation_matrix
    return [r.x0*asset['input_image_size'][0]/page.rect.width,r.y0*asset['input_image_size'][1]/page.rect.height,
        r.x1*asset['input_image_size'][0]/page.rect.width,r.y1*asset['input_image_size'][1]/page.rect.height]


def physical_cells(asset,page,geometry):
    validate_geometry(geometry)
    meta=asset['light_region_ocr']['metadata'];width,height=meta['image_size']
    crop=fitz.Rect(asset['bbox_pdf'])*page.rotation_matrix
    result=[]
    for source in sorted(geometry['cells'],key=lambda c:(c['row'],c['column'])):
        if 'bbox_pdf' in source:box=fitz.Rect(source['bbox_pdf'])
        else:
            x0,y0,x1,y1=source['bbox_crop_px']
            box=fitz.Rect(crop.x0+x0*crop.width/width,crop.y0+y0*crop.height/height,
                crop.x0+x1*crop.width/width,crop.y0+y1*crop.height/height)*page.derotation_matrix
        if box.is_empty or not (fitz.Rect(asset['bbox_pdf'])+(-4.01,-4.01,4.01,4.01)).contains(box):raise ValueError('Physical cell outside source region')
        rot=box*page.rotation_matrix
        cp=[(rot.x0-crop.x0)*width/crop.width,(rot.y0-crop.y0)*height/crop.height,
            (rot.x1-crop.x0)*width/crop.width,(rot.y1-crop.y0)*height/crop.height]
        result.append({**{k:source[k] for k in ('row','column','row_span','col_span')},
            'cell_id':f"R{source['row']}C{source['column']}",'bbox_pdf':list(box),
            'bbox_input_px':pixel_box(asset,page,box),'bbox_crop_px':cp})
    return result


def ink_fraction(image,box):
    import numpy as np
    left,top,right,bottom=[int(round(v)) for v in box]
    if right-left<8 or bottom-top<8:return 1.0
    # Remove the original grid border, not source glyphs or model content.
    margin=min(6,max(1,min(right-left,bottom-top)//4))
    part=image.convert('L').crop((left+margin,top+margin,right-margin,bottom-margin))
    pixels=np.asarray(part)
    return float((pixels<160).mean()) if pixels.size else 1.0


def native_cells(data,cells,region=None):
    result={c['cell_id']:{'lines':{},'indices':[],'issues':[]} for c in cells}
    grid=fitz.Rect(cells[0]['bbox_input_px'])
    for c in cells[1:]:grid|=fitz.Rect(c['bbox_input_px'])
    outside=[]
    for li,line in enumerate(data['lines']):
        for ci,char in enumerate(line):
            b=fitz.Rect(char['box']);centre=(b.tl+b.br)/2
            if not grid.contains(centre):
                if region is not None and fitz.Rect(region).contains(centre) and char['text'].strip():outside.append([li,ci])
                continue
            hits=[c for c in cells if fitz.Rect(c['bbox_input_px']).contains(centre)]
            if len(hits)!=1:
                if char['text'].strip():outside.append([li,ci])
                continue
            cell=hits[0];own=result[cell['cell_id']]
            own['lines'].setdefault(li,[]).append(char['text']);own['indices'].append([li,ci])
            # A glyph crossing a physical boundary remains unresolved.
            tolerance=2*data['image_size'][0]/612
            if char['text'].strip() and not (fitz.Rect(cell['bbox_input_px'])+(-tolerance,-tolerance,tolerance,tolerance)).contains(b):
                own['issues'].append('native_glyph_crosses_cell_boundary')
    for own in result.values():own['text']='\n'.join(''.join(chars).strip() for chars in own.pop('lines').values()).strip()
    return result,outside


def ocr_cells(ocr,cells):
    result={c['cell_id']:[] for c in cells};bad=set();outside=[]
    grid=fitz.Rect(cells[0]['bbox_crop_px'])
    for c in cells[1:]:grid|=fitz.Rect(c['bbox_crop_px'])
    for index,token in enumerate(ocr['tokens']):
        b=fitz.Rect(token['bbox']);centre=(b.tl+b.br)/2
        if not grid.contains(centre):
            if token['text'].strip():outside.append(index)
            continue
        hits=[c for c in cells if fitz.Rect(c['bbox_crop_px']).contains(centre)]
        if len(hits)!=1:outside.append(index);continue
        cell=hits[0];result[cell['cell_id']].append((index,token))
        if token['confidence']<.90 or not (fitz.Rect(cell['bbox_crop_px'])+(-3,-3,3,3)).contains(b):bad.add(cell['cell_id'])
    return result,bad,outside


def joined_tokens(tokens):
    lines=[]
    for index,t in sorted(tokens,key=lambda p:((p[1]['bbox'][1]+p[1]['bbox'][3])/2,p[1]['bbox'][0])):
        cy=(t['bbox'][1]+t['bbox'][3])/2;height=t['bbox'][3]-t['bbox'][1]
        match=next((line for line in lines if abs(line[0]-cy)<=min(line[1],height)*.4),None)
        if match is None:match=[cy,height,[]];lines.append(match)
        match[2].append(t)
    return '\n'.join(' '.join(t['text'] for t in sorted(line[2],key=lambda t:t['bbox'][0])) for line in lines)


def logical_table(plan):
    rows=[{'row_index':r,'section':'body','cells':[]} for r in range(plan['rows'])]
    for c in plan['cells']:
        rows[c['row']]['cells'].append({'column_index':c['column'],'row_span':c['row_span'],'col_span':c['col_span'],
            'text':c['text'] if c['state']!='unreadable' else '', 'state':c['state']})
    return {'column_count':plan['columns'],'rows':rows,'caption':None,'footnotes':[],'uncertainties':[]}


def context_from_source(data,cells,region):
    grid=fitz.Rect(cells[0]['bbox_input_px'])
    for cell in cells[1:]:grid|=fitz.Rect(cell['bbox_input_px'])
    result=[]
    for li,line in enumerate(data['lines']):
        chars=[(ci,c) for ci,c in enumerate(line) if fitz.Rect(region).contains((fitz.Rect(c['box']).tl+fitz.Rect(c['box']).br)/2)
            and not grid.contains((fitz.Rect(c['box']).tl+fitz.Rect(c['box']).br)/2)]
        if not chars or not ''.join(c['text'] for ci,c in chars).strip():continue
        box=fitz.Rect(chars[0][1]['box'])
        for ci,c in chars[1:]:box|=fitz.Rect(c['box'])
        if box.y1<=grid.y0+2:position='before'
        elif box.y0>=grid.y1-2:position='after'
        else:continue  # Never disguise a missing side column as a footnote.
        reliable=data.get('eligible') or data.get('regional_eligible') and not any(intersects(list(box),b) for b in data.get('unreliable_boxes',[]))
        if reliable:result.append({'text':''.join(c['text'] for ci,c in chars).strip(),'position':position,
            'bbox_input_px':list(box),'native_char_indices':[[li,ci] for ci,c in chars],
            'origin':'verified_native_grid_context'})
    return result


def representation(plan):
    def context(position):return ''.join('<p>'+escape(c['text'])+'</p>' for c in plan.get('preserved_context',[]) if c['position']==position)
    return context('before')+plan['table_html']+context('after')


def prepare(asset,page,geometry):
    proof=asset['light_region_ocr'];meta=proof['metadata']
    if sha256(meta['crop_path'])!=meta['crop_sha256']:raise ValueError('Source crop changed')
    cells=physical_cells(asset,page,geometry);rows,cols=validate_geometry(geometry)
    data=packet(page,asset['input_image_size']);native,unassigned_native=native_cells(data,cells,asset['bbox_input_px'])
    ocr,low_ocr,unassigned_ocr=ocr_cells(proof['provenance']['ocr'],cells)
    context=context_from_source(data,cells,asset['bbox_input_px'])
    context_indices={tuple(i) for c in context for i in c['native_char_indices']}
    unassigned_native=[i for i in unassigned_native if tuple(i) not in context_indices]
    r=fitz.Rect(asset['bbox_input_px']);width,height=meta['image_size']
    for c in context:
        b=fitz.Rect(c['bbox_input_px']);c['bbox_crop_px']=[(b.x0-r.x0)*width/r.width,(b.y0-r.y0)*height/r.height,
            (b.x1-r.x0)*width/r.width,(b.y1-r.y0)*height/r.height]
    unassigned_ocr=[i for i in unassigned_ocr if not any((fitz.Rect(c['bbox_crop_px'])+(-8,-8,8,8)).contains(
        (fitz.Rect(proof['provenance']['ocr']['tokens'][i]['bbox']).tl+fitz.Rect(proof['provenance']['ocr']['tokens'][i]['bbox']).br)/2) for c in context)]
    with Image.open(meta['crop_path']) as original:
        image=original
        if geometry.get('source_render'):
            clip=fitz.Rect(geometry['source_render']['clip_pdf'])*page.rotation_matrix
            pix=page.get_pixmap(dpi=geometry['source_render']['dpi'],clip=clip,alpha=False)
            image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        grid=fitz.Rect(cells[0]['bbox_input_px'])
        for c in cells[1:]:grid|=fitz.Rect(c['bbox_input_px'])
        for cell in cells:
            key=cell['cell_id'];n=native[key];box=cell['bbox_input_px'];ink_box=cell['bbox_crop_px']
            if geometry.get('source_render'):
                b=fitz.Rect(cell['bbox_pdf'])*page.rotation_matrix
                ink_box=[(b.x0-clip.x0)*pix.width/clip.width,(b.y0-clip.y0)*pix.height/clip.height,
                    (b.x1-clip.x0)*pix.width/clip.width,(b.y1-clip.y0)*pix.height/clip.height]
            ink=ink_fraction(image,ink_box)
            has_image=any(intersects(box,b) for b in data['images'])
            embedded=[b for b in data['images'] if (fitz.Rect(box)+(-2,-2,2,2)).contains(fitz.Rect(b))
                and fitz.Rect(b).get_area()<grid.get_area()*.8]
            eligible=data.get('eligible') or data.get('regional_eligible') and not any(intersects(box,b) for b in data.get('unreliable_boxes',[]))
            native_ok=eligible and not has_image and not n['issues']
            raw_ocr=joined_tokens(ocr[key])
            cell.update(native_text=n['text'],native_char_indices=n['indices'],native_issues=sorted(set(n['issues'])),
                ocr_text=raw_ocr,ocr_token_indices=[i for i,t in ocr[key]],ink_fraction=round(ink,6))
            if eligible and embedded and n['text'] and not n['issues']:
                # Literal labels within an illustration are usable text. Its
                # shapes are not a failed numeric table cell or a VLM trigger.
                text=n['text'];origin='verified_native_image_cell_labels';state='visible'
                cell.update(embedded_images_input_px=embedded,visual_description_review_required=True)
            elif native_ok and n['text']:
                text=n['text'];origin='verified_native_cell';state='redacted' if REDACTION.fullmatch(text.strip()) else 'visible'
            elif ocr[key] and key not in low_ocr and not n['issues']:
                text=raw_ocr;origin='cached_ordinary_ocr_cell';state='redacted' if REDACTION.fullmatch(text.strip()) else 'visible'
            elif ink<.002 and not n['text'] and not ocr[key]:text='';origin='source_empty_cell';state='blank'
            else:text='';origin='unresolved_cell';state='unreadable'
            cell.update(text=text,state=state,origin=origin)
    binding={'version':VERSION,'asset_id':asset['asset_id'],'source_refs':asset['source_refs'],
        'crop_sha256':meta['crop_sha256'],'ocr_raw_sha256':proof['raw_sha256'],
        'geometry_sha256':stable_hash(geometry),'native_packet_sha256':stable_hash(data),
        'base_sha256':stable_hash(asset['base'])}
    plan={'version':VERSION,'binding':binding,'input_sha256':stable_hash(binding),'geometry':geometry,
        'rows':rows,'columns':cols,'cells':cells,'preserved_context':context,'unassigned_native_chars':unassigned_native,
        'unassigned_ocr_tokens':unassigned_ocr,'numeric_evidence_eligible':False,'semantic_status':'unverified'}
    plan['unresolved_cells']=[c['cell_id'] for c in cells if c['origin']=='unresolved_cell']
    html,issues=render_structured_table(logical_table(plan))
    if unassigned_native:issues.append('native_glyphs_unassigned_to_physical_cells')
    if unassigned_ocr:issues.append('ocr_tokens_unassigned_to_physical_cells')
    if plan['unresolved_cells']:issues.append('cell_text_requires_refinement')
    plan.update(table_html=html,issues=sorted(set(issues)),cell_origins=dict(Counter(c['origin'] for c in cells)))
    plan['representation']=representation(plan)
    return plan


def response_schema(cell_ids):
    cell={'type':'object','properties':{'text':{'type':'string'},'state':{'enum':['visible','blank','redacted','unreadable']}},
        'required':['text','state'],'additionalProperties':False}
    return {'type':'object','properties':{key:deepcopy(cell) for key in cell_ids},'required':list(cell_ids),'additionalProperties':False}


def resolve_transcription(plan,selected,reply):
    if set(reply)!=set(selected):raise ValueError('Response changes immutable cell IDs')
    result=deepcopy(plan);lookup={c['cell_id']:c for c in result['cells']}
    for key in selected:
        cell=lookup[key];value=reply[key]
        if cell['origin']!='unresolved_cell':raise ValueError('Model cannot overwrite a resolved source cell')
        if set(value)!={'text','state'} or not isinstance(value['text'],str) or value['state'] not in ('visible','blank','redacted','unreadable'):
            raise ValueError('Invalid cell transcription')
        cell['model_candidate']=value
        # Agreement is a literal check, not clinical/semantic certification.
        reference=cell['native_text'] or cell['ocr_text']
        supported=value['state']=='visible' and bool(reference.strip()) and comparable_text(value['text'])==comparable_text(reference)
        empty=value['state']=='blank' and cell['ink_fraction']<.002 and not reference.strip() and not value['text']
        redacted=value['state']=='redacted' and bool(REDACTION.fullmatch(reference.strip())) and (not value['text'] or REDACTION.fullmatch(value['text'].strip()))
        if supported or empty or redacted:cell.update(value,origin='source_supported_qwen_cell')
    result['unresolved_cells']=[c['cell_id'] for c in result['cells'] if c['origin']=='unresolved_cell']
    result['table_html'],issues=render_structured_table(logical_table(result))
    if result['unassigned_native_chars']:issues.append('native_glyphs_unassigned_to_physical_cells')
    if result['unassigned_ocr_tokens']:issues.append('ocr_tokens_unassigned_to_physical_cells')
    if result['unresolved_cells']:issues.append('cell_text_requires_refinement')
    result.update(issues=sorted(set(issues)),cell_origins=dict(Counter(c['origin'] for c in result['cells'])))
    result['representation']=representation(result)
    return result
