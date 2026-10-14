"""Conservative native-only path for single-column prose without visual regions."""
from .native_text import packet, project

VERSION='native-prose-page-1'


def parse(page, image_size):
    # Charts, ruling, embedded images and suspect font layers stay on Paddle.
    data=packet(page,image_size)
    if not data['eligible'] or data['images'] or page.get_drawings():return None
    blocks=[b for b in page.get_text('dict')['blocks'] if b['type']==0]
    if not blocks:return None
    import re
    from rag.document_structure import identify
    rows=[]
    for block in blocks:
        for line in block['lines']:
            spans=line['spans']
            if any(b['bbox'][0]-a['bbox'][2]>20 for a,b in zip(spans,spans[1:])):return None
        for other in blocks:
            if other is block:continue
            a,b=block['bbox'],other['bbox']
            if min(a[3],b[3])-max(a[1],b[1])>2 and (a[2]+8<b[0] or b[2]+8<a[0]):return None
        text='\n'.join(''.join(s['text'] for s in line['spans']) for line in block['lines']).strip()
        # Lists of numeric cells can be borderless tables even in one PDF block.
        if re.search(r'\S[ \t]{3,}\S',text):return None
        if sum(bool(re.fullmatch(r'[\s\d.,%±()\-/]+',line)) for line in text.splitlines())>=3:return None
        box=__import__('fitz').Rect(block['bbox'])*page.rotation_matrix
        box=[v*(image_size[0]/page.rect.width if i%2==0 else image_size[1]/page.rect.height) for i,v in enumerate(box)]
        if not 0<=box[0]<box[2]<=image_size[0] or not 0<=box[1]<box[3]<=image_size[1]:return None
        label='paragraph_title' if identify(' '.join(text.split())) else 'text'
        if re.fullmatch(r'(?:Page\s+)?\d+(?:\s*(?:of|/)\s*\d+)?',text,re.I):label='number'
        rows.append({'block_label':label,'block_content':text,'block_bbox':box,'block_order':len(rows)})
    raw={'res':{'width':image_size[0],'height':image_size[1],
        'model_settings':{'use_doc_preprocessor':False,'use_layout_detection':True},'parsing_res_list':rows},
        'ra_runtime':{'inference_seconds':0,'native_page_fastpath':VERSION,'vlm_blocks':0}}
    return project(raw,data)
