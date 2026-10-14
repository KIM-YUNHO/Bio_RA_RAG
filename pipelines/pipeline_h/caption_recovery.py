"""Locate attached Docling captions in actual PDF text lines, with physical boxes."""
import copy,re,unicodedata
import fitz
from preprocess.ingest import stable_hash

def normalized(text):return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',text)).strip().casefold()

def recover(page,assets,doc):
    lines=[]
    for b in page.get_text('dict')['blocks']:
        for line in b.get('lines',[]):
            text=''.join(s['text'] for s in line['spans']).strip()
            if text:lines.append((list(line['bbox']),text))
    result=[]
    for asset in assets:
        if asset['asset_type'] not in {'figure','table'}:continue
        caption=(asset.get('docling_structure') or {}).get('caption','').strip()
        if not caption:continue
        target=normalized(caption);found=None
        for i,(_,text) in enumerate(lines):
            if not target.startswith(normalized(text)):continue
            for end in range(i+1,min(i+6,len(lines))+1):
                joined='\n'.join(t for _,t in lines[i:end])
                if normalized(joined)==target:
                    boxes=[fitz.Rect(b) for b,_ in lines[i:end]];rect=boxes[0]
                    for box in boxes[1:]:rect|=box
                    found=(list(rect),joined);break
            if found:break
        if not found:continue
        box,text=found
        # A real caption block already present wins. A paragraph merely holding
        # a prose reference cannot stand in for the spatial caption evidence.
        if any(a['asset_type']=='text' and normalized(a['selected']['content'])==target for a in assets+result):continue
        display=fitz.Rect(box)*page.rotation_matrix;size=asset['input_image_size']
        px=[round(v*(size[0]/page.rect.width if i%2==0 else size[1]/page.rect.height),4) for i,v in enumerate(display)]
        a=copy.deepcopy(asset);a.update(asset_id='hc'+stable_hash([doc.document_id,doc.version_id,page.number+1,box,text])[:26],
            asset_type='text',block_label='figure_title' if asset['asset_type']=='figure' else 'table_title',block_order=asset['block_order']-.1,
            bbox_pdf=box,bbox_input_px=px,source_refs=[doc.source_ref(page.number+1,box)],
            base={'engine':'PyMuPDF source lines','origin':'h_native_caption_literal','content':text,'quality':None,'printed_text_reference':None,'figure_ocr_error':None},
            native_reference={'text':text,'reliable':True},route={'task':None,'next_stage':'complete','reasons':['attached_caption_located_in_source_lines']},
            selected={'origin':'h_native_caption_literal','content':text},enrichment=None,validation_status='source_caption_literal_unverified',
            caption_source_owner_id=asset['asset_id'],caption_recovery={'basis':'exact_docling_caption_match_to_source_lines'})
        a.pop('source_visual',None);a.pop('visual_classification',None);a['h_candidates']=[]
        a['docling_structure']={'source_ref':asset['docling_structure']['ref'],'attached_caption':caption,'native_lines':text}
        result.append(a)
    return result
