"""Native text for text layout blocks; tables, figures and suspect layers stay visual."""
from copy import deepcopy

VERSION = 'native-layout-text-2-selective'
TEXT_LABELS = frozenset(('text', 'paragraph_title', 'doc_title', 'figure_title', 'table_title',
    'caption', 'header', 'footer', 'footnote', 'vision_footnote', 'reference', 'reference_content',
    'abstract', 'content', 'aside_text', 'number', 'formula_number'))


def packet(page, image_size):
    """Use exactly inventory's font/OCR-layer check; positions match the rendered image."""
    import fitz
    import text_layer
    quality, replacements = text_layer.inspect(page)
    full = text_layer.normalize(page.get_text('text'), replacements)
    eligible = bool(full.strip()) and not quality['needs_ocr'] and not text_layer.private_use_count(full)
    result = {'version': VERSION, 'eligible': eligible, 'quality': quality,
              'lines': [], 'images': [], 'image_size': list(image_size)}
    result['regional_eligible'] = bool(full.strip()) and 'publisher_ocr_layer' not in quality['reasons']
    result['unreliable_boxes'] = []
    result['tables'] = []
    if not result['regional_eligible']:return result
    def box(rect):
        rect = fitz.Rect(rect) * page.rotation_matrix
        return [rect.x0*image_size[0]/page.rect.width, rect.y0*image_size[1]/page.rect.height,
                rect.x1*image_size[0]/page.rect.width, rect.y1*image_size[1]/page.rect.height]
    for block in page.get_text('rawdict')['blocks']:
        if block['type'] != 0:continue
        for line in block['lines']:
            result['lines'].append([{'text': text_layer.normalize(c['c'], replacements), 'box': box(c['bbox'])}
                for span in line['spans'] for c in span['chars']])
    result['images'] = [box(img['bbox']) for img in page.get_image_info()]
    # A damaged glyph elsewhere on the page cannot invalidate readable regions.
    # Every unmapped trace glyph and unresolved private-use character remains visual.
    for span in page.get_texttrace():
        for code, _, _, rect in span['chars']:
            if code == 0xFFFD:
                result['unreliable_boxes'].append(box(rect))
    for line in result['lines']:
        result['unreliable_boxes'].extend(c['box'] for c in line
            if '\ufffd' in c['text'] or text_layer.private_use_count(c['text']))
    if quality['needs_ocr'] and not result['unreliable_boxes']:
        result['regional_eligible'] = False
    from .native_tables import grids
    result['tables'] = grids(page,image_size)
    return result


def intersects(a, b):
    return min(a[2],b[2]) > max(a[0],b[0]) and min(a[3],b[3]) > max(a[1],b[1])


def extract(data, label, box):
    if label == 'table':
        from .native_tables import select
        table=select(data,label,box)
        return table['content'] if table else None
    if label not in TEXT_LABELS:return None
    if not data.get('eligible'):
        if not data.get('regional_eligible'):return None
        if any(intersects(box,bad) for bad in data.get('unreliable_boxes',[])):return None
    if any(intersects(box, image) for image in data['images']):return None
    lines=[]
    for line in data['lines']:
        chars=[c['text'] for c in line if box[0] <= (c['box'][0]+c['box'][2])/2 <= box[2]
               and box[1] <= (c['box'][1]+c['box'][3])/2 <= box[3]]
        if chars:lines.append(''.join(chars))
    text='\n'.join(lines).strip()
    if not text or '\ufffd' in text or any(0xE000<=ord(c)<=0xF8FF for c in text) or any(ord(c)<32 and c not in '\t\n\r' for c in text):return None
    return text


def project(raw, data, *, cached=False):
    """Restore native literals after SDK formatting and expose the actual text source."""
    result=deepcopy(raw) if cached else raw
    rows=result.get('res', result)['parsing_res_list'];sources={}
    for i,row in enumerate(rows):
        text=extract(data,row['block_label'],row['block_bbox'])
        if text is not None:
            row['block_content']=text
            if row['block_label']=='table':
                from .native_tables import select
                sources[str(i)]={**select(data,'table',row['block_bbox']), 'layout_bbox_input_px':row['block_bbox']}
            else:
                sources[str(i)]={'origin':'native_text_layer','bbox_input_px':row['block_bbox']}
    result['ra_native_text']={'version':VERSION,'quality':data['quality'],
        'page_eligible':data['eligible'],'native_blocks':len(sources),'block_sources':sources}
    if cached:
        prior=result.pop('ra_runtime', {})
        result['ra_runtime']={'inference_seconds':0,'legacy_full_cache_reused':True,
            'cached_inference_seconds':prior.get('inference_seconds'),
            'native_blocks':len(sources),'vlm_blocks':0}
    return result


class NativeRecognizer:
    """Small version-checked SDK adapter; the installed package is never patched on disk."""
    def __init__(self, pipeline):
        self.core=getattr(pipeline.paddlex_pipeline,'_pipeline',pipeline.paddlex_pipeline)
        for name in ('_paddleocr_vl_collect_page_vlm_entries_core','_paddleocr_vl_run_vl_recognition_batches'):
            if not callable(getattr(self.core,name,None)):raise RuntimeError('Unsupported PaddleX native-text interface')
        self.collect=self.core._paddleocr_vl_collect_page_vlm_entries_core
        self.recognize=self.core._paddleocr_vl_run_vl_recognition_batches
        self.data=None;self.native={};self.native_count=0;self.vlm_count=0
        self.core._paddleocr_vl_collect_page_vlm_entries_core=self.collect_inputs
        self.core._paddleocr_vl_run_vl_recognition_batches=self.recognize_inputs

    def begin(self, data):
        self.data=data;self.native={};self.native_count=0;self.vlm_count=0

    def collect_inputs(self, page_idx, blocks, images, config):
        entries, spotting, dropped=self.collect(page_idx,blocks,images,config)
        if self.data:
            for _,j,_,_,_,_ in entries:
                block=blocks[j];text=extract(self.data,block['label'],block['box'])
                if text is not None:self.native[(page_idx,j)]=text
        return entries,spotting,dropped

    def recognize_inputs(self, batches, has_spotting, kwargs):
        if not self.data:return self.recognize(batches,has_spotting,kwargs)
        filtered={}
        for key,info in batches.items():
            indices=[i for i,uid in enumerate(info['vlm_block_ids']) if uid not in self.native]
            if indices:
                filtered[key]={**info, **{name:[info[name][i] for i in indices]
                    for name in ('images','queries','figure_token_maps','vlm_block_ids')},'curr_vlm_block_idx':0}
                self.vlm_count+=len(indices)
        self.recognize(filtered,has_spotting,kwargs)
        for key,info in batches.items():
            generated=iter(filtered.get(key,{}).get('vlm_results',[]));merged=[]
            for uid in info['vlm_block_ids']:
                if uid in self.native:
                    merged.append({'result':self.native[uid]});self.native_count+=1
                else:merged.append(next(generated))
            info['vlm_results']=merged
