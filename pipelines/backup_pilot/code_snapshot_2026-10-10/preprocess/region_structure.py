"""Source geometry and cached literal OCR refinement. No model execution."""
from copy import deepcopy
import re

import fitz
from PIL import Image

from .ingest import stable_hash
from .native_text import packet,extract
from .table_quality import inspect_table,REDACTION
from .table_representation import render_structured_table

VERSION='region-structure-1'


def coverage(start,end,intervals):
    cursor=start;total=0
    for left,right in sorted(intervals):
        left=max(left,cursor,start);right=min(right,end)
        if right>left:total+=right-left;cursor=right
    return total/max(end-start,1e-6)


def drawing_lines(page):
    lines=[]
    for path in page.get_drawings():
        for item in path['items']:
            if item[0]=='l':lines.append((*item[1],*item[2]))
            elif item[0]=='re':
                r=item[1];lines.extend([(r.x0,r.y0,r.x1,r.y0),(r.x1,r.y0,r.x1,r.y1),
                    (r.x0,r.y1,r.x1,r.y1),(r.x0,r.y0,r.x0,r.y1)])
    return lines


def borders_supported(rect,lines,tolerance=1.2):
    for vertical,fixed,start,end in ((False,rect.y0,rect.x0,rect.x1),(False,rect.y1,rect.x0,rect.x1),
            (True,rect.x0,rect.y0,rect.y1),(True,rect.x1,rect.y0,rect.y1)):
        spans=[]
        for x0,y0,x1,y1 in lines:
            a,b,c,d=(x0,x1,y0,y1) if vertical else (y0,y1,x0,x1)
            if abs(a-fixed)<=tolerance and abs(b-fixed)<=tolerance:spans.append((min(c,d),max(c,d)))
        if coverage(start,end,spans)<.98:return False
    return True


def native_grid(page,asset,data=None):
    """All cell borders and every native glyph must be supported independently."""
    data=data or packet(page,asset['input_image_size'])
    if not data.get('eligible'):return None
    lines=drawing_lines(page)
    if len(lines)<6:return None
    tables=page.find_tables(strategy='lines_strict')
    roi=fitz.Rect(asset['bbox_pdf']);candidates=[]
    for table in tables.tables:
        rect=fitz.Rect(table.bbox);intersection=(rect & roi).get_area()
        if (table.row_count<2 or table.col_count<2 or table.row_count*table.col_count>1200
                or intersection/max(rect.get_area(),1)<.98 or intersection/max(roi.get_area(),1)<.90):continue
        cells=[fitz.Rect(c) for c in dict.fromkeys(tuple(c) for row in table.rows for c in row.cells if c is not None)]
        xs=sorted({round(v,2) for c in cells for v in (c.x0,c.x1)})
        ys=sorted({round(v,2) for c in cells for v in (c.y0,c.y1)})
        if len(xs)!=table.col_count+1 or len(ys)!=table.row_count+1:continue
        if not all(borders_supported(c,lines) for c in cells):continue
        # A native caption over a scanned table does not supply the cells.
        if any((rect & fitz.Rect(i['bbox'])).get_area()>1 for i in page.get_image_info()):continue
        def position(value,values):return min(range(len(values)),key=lambda i:abs(values[i]-value))
        header=table.header
        rows=[{'row_index':r,'section':'header' if not header.external
            and fitz.Rect(header.bbox).contains(fitz.Rect(rect.x0,ys[r],rect.x1,ys[r+1])) else 'body','cells':[]}
            for r in range(table.row_count)]
        physical=[];seen=set();good=True
        for cell in cells:
            identity=tuple(cell)
            if identity in seen:continue
            seen.add(identity);r=position(cell.y0,ys);c=position(cell.x0,xs)
            bottom=position(cell.y1,ys);right=position(cell.x1,xs)
            pixels=cell*page.rotation_matrix
            box=[v*(asset['input_image_size'][0]/page.rect.width if i%2==0
                    else asset['input_image_size'][1]/page.rect.height) for i,v in enumerate(pixels)]
            text=extract(data,'text',box) or ''
            rows[r]['cells'].append({'column_index':c,'row_span':bottom-r,'col_span':right-c,
                'text':text,'state':'redacted' if REDACTION.fullmatch(text.strip()) else 'visible' if text else 'blank'})
            physical.append({'row':r,'column':c,'bbox_pdf':list(cell),'text':text})
        for row in rows:row['cells'].sort(key=lambda c:c['column_index'])
        answer={'column_count':table.col_count,'rows':rows,'caption':None,'footnotes':[],'uncertainties':[]}
        html,issues=render_structured_table(answer)
        # Check every nonspace source glyph belongs to exactly one physical cell.
        for line in data['lines']:
            for char in line:
                if not char['text'].strip():continue
                box=fitz.Rect(char['box']);centre=(box.tl+box.br)/2
                source_box=(box*fitz.Matrix(page.rect.width/asset['input_image_size'][0],
                    page.rect.height/asset['input_image_size'][1]))*page.derotation_matrix
                if rect.contains((source_box.tl+source_box.br)/2):
                    hits=[c for c in cells if (c+(-.01,-.01,.01,.01)).contains((source_box.tl+source_box.br)/2)]
                    if len(hits)!=1 or not (hits[0]+(-1,-1,1,1)).contains(source_box):good=False
        if not good or issues or not any(c['text'].strip() for c in physical):continue
        candidates.append({'html':html,'logical_grid':answer,'physical_cells':physical,
            'bbox_pdf':list(rect),'geometry_status':'source_borders_and_glyph_coverage_checked',
            'quality':inspect_table(html,'\n'.join(c['text'] for c in physical),True)})
    return candidates[0] if len(candidates)==1 else None


def context_masks(asset,assets,groups):
    """Only explicitly linked caption/note regions can remove crop context."""
    lookup={a['asset_id']:a for a in assets};masks=[];roi=fitz.Rect(asset['bbox_pdf'])
    group=next((g for g in groups if asset['asset_id'] in g.get('member_asset_ids',[]) and g['kind']=='table'),None)
    if not group:return masks
    for identifier in group.get('context_asset_ids',group.get('caption_part_asset_ids',[])):
        a=lookup[identifier]
        if (a['document_id'],a['version_id'],a['pdf_page'])!=(asset['document_id'],asset['version_id'],asset['pdf_page']):continue
        overlap=roi & fitz.Rect(a['bbox_pdf'])
        if overlap.is_empty:continue
        # Only crop-edge context is removable; an internal region may be a cell.
        if overlap.y0>roi.y0+2 and overlap.y1<roi.y1-2:continue
        masks.append({'asset_id':identifier,'role':group.get('context_roles',{}).get(identifier,'caption'),
            'bbox_pdf':list(overlap),'source_refs':a['source_refs']})
    return masks


def partition_ocr(asset,proof,masks,page=None):
    roi=fitz.Rect(asset['bbox_pdf']);display=roi*page.rotation_matrix if page else roi
    w,h=proof['metadata']['image_size'];body=[];context=[];ambiguous=[]
    for index,token in enumerate(proof['provenance']['ocr']['tokens']):
        x0,y0,x1,y1=token['bbox']
        box=fitz.Rect(display.x0+x0*display.width/w,display.y0+y0*display.height/h,
            display.x0+x1*display.width/w,display.y0+y1*display.height/h)
        if page:box=box*page.derotation_matrix
        hits=[m for m in masks if (box & fitz.Rect(m['bbox_pdf'])).get_area()/max(box.get_area(),1e-6)>.95]
        if len(hits)==1:context.append({'token_index':index,'context_asset_id':hits[0]['asset_id']})
        else:
            body.append(deepcopy(token))
            if any((box & fitz.Rect(m['bbox_pdf'])).get_area()>0 for m in masks):ambiguous.append(index)
    return {'tokens':body},context,ambiguous


def chart_geometry(image,ocr):
    """Require printed tick rows/columns beside intersecting physical axes."""
    import cv2,numpy as np
    scale=min(1.,1400/max(image.size));w,h=image.size
    small=image.convert('L').resize((max(1,int(w*scale)),max(1,int(h*scale))))
    ink=cv2.threshold(np.array(small),0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
    lines=cv2.HoughLinesP(ink,1,np.pi/180,threshold=35,minLineLength=max(35,int(min(ink.shape)*.16)),maxLineGap=8)
    horizontal=[];vertical=[]
    for line in ([] if lines is None else lines.reshape(-1,4)):
        x0,y0,x1,y1=[float(v)/scale for v in line]
        if abs(y1-y0)<3/scale and abs(x1-x0)>.2*w:horizontal.append((min(x0,x1),min(y0,y1),max(x0,x1)))
        if abs(x1-x0)<3/scale and abs(y1-y0)>.2*h:vertical.append((min(x0,x1),min(y0,y1),max(y0,y1)))
    ticks=[t for t in ocr['tokens'] if t['confidence']>=.6 and re.fullmatch(r'[-−+]?\d+(?:[.,]\d+)?',t['text'].strip())]
    pairs=[];tol=max(8/scale,min(w,h)*.012)
    for left,y,right in horizontal:
        for x,top,bottom in vertical:
            if abs(x-left)>tol or abs(y-bottom)>tol:continue
            xt=[t for t in ticks if left-tol<=(t['bbox'][0]+t['bbox'][2])/2<=right+tol
                and -tol<=t['bbox'][1]-y<=max(h*.10,30)]
            yt=[t for t in ticks if top-tol<=(t['bbox'][1]+t['bbox'][3])/2<=bottom+tol
                and -tol<=x-t['bbox'][2]<=max(w*.12,30)]
            if len(xt)>=3 and len(yt)>=3:pairs.append({'axes_bbox_input_px':[x,top,right,y],
                'x_tick_count':len(xt),'y_tick_count':len(yt)})
    return {'chart_axes':bool(pairs),'axis_candidates':pairs[:8],'method':'physical_axes_and_literal_numeric_tick_geometry'}


def analyze(asset,page,assets,groups):
    proof=asset.get('light_region_ocr')
    if not proof:return None
    binding={'version':VERSION,'asset_id':asset['asset_id'],'source_refs':asset['source_refs'],
        'crop_sha256':proof['metadata']['crop_sha256'],'raw_sha256':proof['raw_sha256'],
        'context_masks':context_masks(asset,assets,groups)}
    result={'binding':binding,'input_sha256':stable_hash(binding),'numeric_evidence_eligible':False}
    if asset['asset_type']=='figure':
        with Image.open(proof['metadata']['crop_path']) as image:
            result['visual_complexity']=chart_geometry(image,proof['provenance']['ocr'])
        return result
    if asset['asset_type']!='table':return None
    candidate=native_grid(page,asset)
    if candidate:
        result.update(grid=candidate,selected_origin='source_geometry_table_candidate',quality=candidate['quality'])
        return result
    body,context,ambiguous=partition_ocr(asset,proof,binding['context_masks'],page)
    from .simple_grid import reconstruct
    with Image.open(proof['metadata']['crop_path']) as image:grid=reconstruct(image,body)
    # Variable row occupancy alone can be normal wrapped text or a header.
    if grid['complexity'].get('borderless_unresolved'):
        grid['complexity']['borderless_unresolved']=False
        grid['complexity']['column_alignment_requires_review']=True
    native=asset['native_reference'];reference=native['text']
    if binding['context_masks']:
        data=packet(page,asset['input_image_size']);box=asset['bbox_input_px'];lines=[]
        for line in data['lines']:
            kept=[]
            for c in line:
                cx=(c['box'][0]+c['box'][2])/2;cy=(c['box'][1]+c['box'][3])/2
                if not (box[0]<=cx<=box[2] and box[1]<=cy<=box[3]):continue
                pdf_centre=fitz.Point(cx*page.rect.width/asset['input_image_size'][0],
                    cy*page.rect.height/asset['input_image_size'][1])*page.derotation_matrix
                if not any(fitz.Rect(m['bbox_pdf']).contains(pdf_centre) for m in binding['context_masks']):kept.append(c['text'])
            if kept:lines.append(''.join(kept))
        reference='\n'.join(lines).strip()
    quality=inspect_table(grid['html'],reference,native['reliable'])
    if ambiguous:quality['issues']=sorted(set([*quality['issues'],'context_mask_intersects_body_token']))
    result.update(grid=grid,body_reference_text=reference,context_tokens=context,ambiguous_tokens=ambiguous,
        quality=quality,selected_origin='cached_ocr_table_candidate')
    if '<table' in asset['base'].get('content','').casefold():
        result['existing_candidate_quality']=inspect_table(asset['base']['content'],reference,native['reliable'])
    return result
