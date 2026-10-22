"""Recover short native text omitted by layout near a figure; never infer values."""
from pathlib import Path
import fitz
from preprocess.ingest import stable_hash

VERSION = 'native-visual-context-1'


def recover(doc, page, assets, image_path, image_size):
    from preprocess.extraction.hybrid import native_region
    from preprocess.visual.visual_groups import horizontal_overlap
    figures = [a for a in assets if a['asset_type'] == 'figure']
    if not figures:
        return []
    recovered = []
    for block in page.get_text('blocks'):
        if block[6] != 0:
            continue
        rect = fitz.Rect(block[:4])
        raw = block[4].strip()
        if not raw or len(raw) > 240 or len(raw.splitlines()) > 6 or not __import__('re').search(r'[A-Za-z]{3,}|[\u3040-\u30ff\u4e00-\u9fff]{2,}',raw):
            continue
        # Any existing text/table coverage wins, including captions/headings.
        if any((rect & fitz.Rect(a['bbox_pdf'])).get_area() / max(rect.get_area(),1) >=
               (.15 if a['asset_type'] in {'text','table'} else .45) for a in assets):
            continue
        candidates = []
        for a in figures:
            box = fitz.Rect(a['bbox_pdf'])
            gap = max(0,rect.y0-box.y1,box.y0-rect.y1)
            if gap > 100 or horizontal_overlap(list(rect),list(box)) < .5:
                continue
            lo,hi = min(rect.y1,box.y1),max(rect.y0,box.y0)
            if any(r['block_label'] in {'paragraph_title','figure_title','table_title','caption'}
                   and lo <= r['bbox_pdf'][1] and r['bbox_pdf'][3] <= hi
                   and horizontal_overlap(list(rect),r['bbox_pdf']) >= .15 for r in assets):
                continue
            candidates.append((gap,a))
        candidates.sort(key=lambda item:item[0])
        if not candidates or len(candidates)>1 and candidates[1][0]-candidates[0][0] < 8:
            continue
        text,reliable = native_region(page,list(rect))
        if not reliable:
            continue
        owner = candidates[0][1]
        displayed = rect * page.rotation_matrix
        pixels = [v*(image_size[0]/page.rect.width if i%2==0 else image_size[1]/page.rect.height)
                  for i,v in enumerate(displayed)]
        box = list(rect)
        identity = 'nv'+stable_hash([doc.document_id,doc.version_id,page.number+1,VERSION,box,text])[:26]
        recovered.append({'schema_version':'ra-extraction-1','asset_id':identity,'document_id':doc.document_id,
            'version_id':doc.version_id,'pdf_page':page.number+1,'asset_type':'text',
            'block_label':'native_figure_context','block_order':None,'bbox_pdf':box,'bbox_input_px':pixels,
            'input_image_size':list(image_size),'coordinate_space':'pymupdf_unrotated_crop_points_top_left',
            'source_refs':[doc.source_ref(page.number+1,box)],
            'page_image':str(image_path),'base':{'engine':'PyMuPDF','origin':'native_text',
            'content':text,'quality':None,'printed_text_reference':None,'figure_ocr_error':None},
            'native_reference':{'text':text,'reliable':True},'native_figure_owner_id':owner['asset_id'],
            'route':{'task':None,'reasons':['native_visual_context_geometry_candidate']},'enrichment':None,
            'selected':{'origin':'native_visual_context','content':text},
            'validation_status':'context_association_unverified','numeric_evidence_eligible':False})
    return recovered


def recover_pages(documents, pages, assets):
    docs={(d.document_id,d.version_id):d for d in documents}
    for p in pages:
        if p['status'] != 'parsed':
            continue
        local=[a for a in assets if (a['document_id'],a['version_id'],a['pdf_page']) ==
               (p['document_id'],p['version_id'],p['pdf_page'])]
        doc=docs[(p['document_id'],p['version_id'])]
        with fitz.open(doc.pdf_path) as pdf:
            assets.extend(recover(doc,pdf[p['pdf_page']-1],local,p['image_path'],p['image_size']))
