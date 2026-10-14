"""Independent positive table complexity, without OCR or model inference."""
from PIL import Image
from .region_structure import drawing_lines, borders_supported
from .ingest import sha256
VERSION='source-complexity-3-complete-region'


def rule_bands(positions,scale):
    """Treat tightly adjacent parallel strokes as one physical rule band.

    A double printed border must not become a text row a few pixels high.
    The conservative bound scales with the crop, far below ordinary text rows.
    Keep the original strokes in the proof instead of inventing blank cells.
    """
    bands=[];limit=max(3,round(scale*.004))
    for position in positions:
        if bands and position-bands[-1][0]<=limit:bands[-1].append(position)
        else:bands.append([position])
    return [round(sum(b)/len(b)) for b in bands],bands


def vector_grid(page, box):
    import fitz
    roi=fitz.Rect(box);lines=drawing_lines(page)
    for table in page.find_tables(strategy='lines_strict').tables:
        target=fitz.Rect(table.bbox);overlap=target&roi
        if overlap.get_area()<target.get_area()*.98 or overlap.get_area()<roi.get_area()*.7:continue
        cells=list(dict.fromkeys(tuple(c) for c in table.cells if c))
        if not cells or len(cells)>1200 or not all(borders_supported(fitz.Rect(c),lines) for c in cells):continue
        if abs(sum(fitz.Rect(c).get_area() for c in cells)-target.get_area())>target.get_area()*.01:continue
        xs=sorted(set(round(x,2) for c in cells for x in (c[0],c[2])))
        ys=sorted(set(round(y,2) for c in cells for y in (c[1],c[3])))
        if len(xs)<3 or len(ys)<3:continue
        logical=[{'row':ys.index(round(c[1],2)),'column':xs.index(round(c[0],2)),
            'row_span':ys.index(round(c[3],2))-ys.index(round(c[1],2)),
            'col_span':xs.index(round(c[2],2))-xs.index(round(c[0],2)), 'bbox_pdf':list(c)} for c in cells]
        return {'method':'closed_vector_cells_and_complete_area','cells':logical,'bbox_pdf':list(table.bbox),
            'merged_cells':any(c['row_span']>1 or c['col_span']>1 for c in logical)}
    return None


def raster_grid(image):
    """Only closed grids with complete or fully absent internal segments qualify.

    Low confidence OCR, partial/broken rules and nonrectangular components are
    explicitly insufficient. A fully absent internal rule joins adjacent cells.
    """
    import cv2,numpy as np
    gray=cv2.cvtColor(np.array(image.convert('RGB')),cv2.COLOR_RGB2GRAY)
    ink=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
    h,w=ink.shape
    horizontal=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((1,max(15,w//20)),np.uint8))
    vertical=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((max(15,h//20),1),np.uint8))
    def positions(mask,axis,size,fraction=.35):
        indices=np.flatnonzero((mask>0).sum(axis=axis)>size*fraction);runs=[]
        for value in indices:
            if runs and value<=runs[-1][-1]+1:runs[-1].append(int(value))
            else:runs.append([int(value)])
        return [round(sum(run)/len(run)) for run in runs]
    xs,xb=rule_bands(positions(vertical,0,h),min(h,w))
    ys,yb=rule_bands(positions(horizontal,1,w,.08),min(h,w))
    if len(xs)<3 or len(ys)<3 or (len(xs)-1)*(len(ys)-1)>1200:return None
    def v(x,top,bottom):return float(np.mean(vertical[top+3:bottom-2,max(0,x-2):x+3].max(axis=1)>0)) if bottom-top>6 else 0
    def hor(y,left,right):return float(np.mean(horizontal[max(0,y-2):y+3,left+3:right-2].max(axis=0)>0)) if right-left>6 else 0
    # A partial body rule can span just one column. Require a complete segment
    # between established vertical rules; prose underlines cannot become rows.
    supported=[(y,b) for y,b in zip(ys,yb) if any(hor(y,xs[c],xs[c+1])>=.98 for c in range(len(xs)-1))]
    ys=[y for y,b in supported];yb=[b for y,b in supported]
    if len(ys)<3:return None
    if min(v(xs[0],ys[0],ys[-1]),v(xs[-1],ys[0],ys[-1]),
           hor(ys[0],xs[0],xs[-1]),hor(ys[-1],xs[0],xs[-1]))<.98:return None
    rows,columns=len(ys)-1,len(xs)-1
    parent=list(range(rows*columns))
    def root(i):
        while parent[i]!=i:i=parent[i]
        return i
    def join(a,b):parent[root(a)]=root(b)
    for r in range(rows):
        for c in range(1,columns):
            coverage=v(xs[c],ys[r],ys[r+1])
            if coverage<=.02:join(r*columns+c-1,r*columns+c)
            elif coverage<.98:return None
    for r in range(1,rows):
        for c in range(columns):
            coverage=hor(ys[r],xs[c],xs[c+1])
            if coverage<=.02:join((r-1)*columns+c,r*columns+c)
            elif coverage<.98:return None
    groups={}
    for i in range(rows*columns):groups.setdefault(root(i),[]).append(divmod(i,columns))
    cells=[]
    for positions in groups.values():
        top=min(r for r,c in positions);bottom=max(r for r,c in positions)+1
        left=min(c for r,c in positions);right=max(c for r,c in positions)+1
        if len(positions)!=(bottom-top)*(right-left):return None
        cells.append({'row':top,'column':left,'row_span':bottom-top,'col_span':right-left,
            'bbox_crop_px':[xs[left],ys[top],xs[right],ys[bottom]]})
    return {'method':'closed_raster_grid_with_rectangular_boundary_components','cells':cells,
        'rule_bands':{'x':xb,'y':yb},
        'merged_cells':any(c['row_span']>1 or c['col_span']>1 for c in cells)}


def table_gate(asset,page):
    import fitz,hashlib
    crop=(asset.get('light_region_ocr') or {}).get('metadata',{}).get('crop_path')
    result=vector_grid(page,asset['bbox_pdf'])
    if result is None and crop:
        with Image.open(crop) as image:result=raster_grid(image)
        # A closed subgrid is not proof that the full table was recovered. Layout
        # crops can cut a printed outer rule by a few points. Re-render the exact
        # source with bounded border padding, without another OCR/model call.
        width,height=asset['light_region_ocr']['metadata']['image_size']
        if result:
            cells=result['cells'];bounds=[min(c['bbox_crop_px'][0] for c in cells),min(c['bbox_crop_px'][1] for c in cells),
                max(c['bbox_crop_px'][2] for c in cells),max(c['bbox_crop_px'][3] for c in cells)]
            if (bounds[2]-bounds[0])*(bounds[3]-bounds[1])<width*height*.90:result=None
        if result is None:
            clip=(fitz.Rect(asset['bbox_pdf'])+(-4,-4,4,4))*page.rotation_matrix
            clip&=page.rect;pix=page.get_pixmap(dpi=300,clip=clip,alpha=False)
            image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
            grid=raster_grid(image)
            if grid:
                for cell in grid['cells']:
                    x0,y0,x1,y1=cell.pop('bbox_crop_px')
                    cell['bbox_pdf']=list(fitz.Rect(clip.x0+x0*clip.width/pix.width,clip.y0+y0*clip.height/pix.height,
                        clip.x0+x1*clip.width/pix.width,clip.y0+y1*clip.height/pix.height)*page.derotation_matrix)
                grid['source_render']={'clip_pdf':list(clip*page.derotation_matrix),'dpi':300,
                    'image_size':[pix.width,pix.height],'pixels_sha256':hashlib.sha256(pix.samples).hexdigest(),
                    'border_padding_pdf_points':4}
                result=grid
    return {'version':VERSION,'asset_id':asset['asset_id'],'source_refs':asset['source_refs'],
        'crop_sha256':sha256(crop) if crop else None,'geometry':result,
        'complexity_confirmed':bool(result and result['merged_cells']),
        'decision':'qwen_complex_structure' if result and result['merged_cells'] else
            'flat_grid_ocr_or_review' if result else 'structural_complexity_unconfirmed_review',
        'numeric_evidence_eligible':False}
