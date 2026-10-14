"""Source-only selection and conservative candidate gates; never consumes evaluation gold."""
from collections import Counter
import copy
import re
import unicodedata

from extend_abp654_results import related

VERSION = 'visual-preservation-boxes-multi-value-gate-2'
PAIR = re.compile(r'(?<![\w.])[-+]?\d+(?:[.,]\d+)*\s*\(\s*[-+\d.,%\s]+\)')


def adjacent_visual_context(box,figures,page_height):
    for f in figures:
        if f.get('decorative') or f.get('parent_id'):continue
        b=f['bbox']
        horizontal=max(0,min(box[2],b[2])-max(box[0],b[0]))
        gap=max(0,b[1]-box[3],box[1]-b[3])
        if horizontal>=.5*min(box[2]-box[0],b[2]-b[0]) and gap<=.06*page_height:
            return f['figure_id']
    return None


def table_alarms(table):
    """Repeated value/statistic groups in one cell are a routing alarm, not proof of error."""
    cells=[]
    for r,row in enumerate(table):
        if r == 0:
            continue
        for c,text in enumerate(row):
            from preprocess.table_quality import comparable_text
            groups=PAIR.findall(comparable_text(str(text)))
            if len(groups)>=2:
                cells.append({'r':r,'c':c,'value_groups':len(groups),'text':str(text)})
    return {'issues':['multiple_value_groups_in_one_cell'] if cells else [],'cells':cells}


def number_literals(table):
    from preprocess.table_quality import numbers
    values=numbers(' '.join(str(c) for r in table for c in r))
    return {v.rstrip('%').replace(',', '') for v in values}


def words(table):
    from preprocess.table_quality import comparable_text
    text=comparable_text(' '.join(str(c) for r in table for c in r))
    text=unicodedata.normalize('NFKC',text).casefold()
    text=re.sub(r'\\[a-z]+',' ',text)
    return set(re.findall(r'[a-z]{4,}',text))


def candidate_gate(table, content, original=None):
    from preprocess.table_quality import inspect_table
    fatal={'invalid_span','span_outside_table','overlapping_spans','truncated_html',
           'invalid_cell_nesting','invalid_row_end','unclosed_row','nested_table','invalid_html'}
    issues=[]
    if not table or not any(str(c).strip() for row in table for c in row):
        issues.append('empty_output')
    if '<table' in content.lower():
        issues.extend(sorted(fatal & set(inspect_table(content)['issues'])))
    result={'accepted':not issues,'issues':issues,'semantic_status':'unverified source extraction candidate',
            'numeric_evidence_eligible':False}
    if original is not None:
        old,new=number_literals(original),number_literals(table)
        missing=sorted(old-new)
        original_words=words(original)
        retained=len(original_words & words(table))/len(original_words) if original_words else 1.0
        gain=len(table_alarms(original)['cells'])-len(table_alarms(table)['cells'])
        if missing:issues.append('existing_numeric_literals_not_preserved')
        if retained<0.90:issues.append('existing_header_label_words_not_preserved')
        if gain<=0:issues.append('no_structural_alarm_reduction')
        result.update(accepted=not issues,issues=issues,missing_original_numbers=missing,
                      existing_label_word_recall=retained,structural_alarm_reduction=gain,
                      protection_scope='numeric literal set and label word coverage; not semantic truth')
    return result


def boxed_regions(image, pdf_size):
    """Closed source borders, independent of document identity or expected contents."""
    import cv2
    import numpy as np
    gray=cv2.cvtColor(np.array(image.convert('RGB')),cv2.COLOR_RGB2GRAY)
    h,w=gray.shape
    bw=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_MEAN_C,cv2.THRESH_BINARY_INV,31,15)
    horizontal=cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(max(24,w//30),1)))
    vertical=cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(20,h//55))))
    lines=cv2.bitwise_or(horizontal,vertical)
    lines=cv2.dilate(lines,cv2.getStructuringElement(cv2.MORPH_RECT,(3,3)))
    contours,_=cv2.findContours(lines,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    boxes=[]
    for contour in contours:
        x,y,bw_,bh=cv2.boundingRect(contour)
        if bw_<w*.40 or not h*.025<=bh<=h*.45:
            continue
        strip=4
        edges=[horizontal[max(0,y-strip):min(h,y+strip+1),x:x+bw_],
               horizontal[max(0,y+bh-strip-1):min(h,y+bh+strip),x:x+bw_],
               vertical[y:y+bh,max(0,x-strip):min(w,x+strip+1)],
               vertical[y:y+bh,max(0,x+bw_-strip-1):min(w,x+bw_+strip)]]
        coverage=[float((e.max(axis=0)>0).mean()) if i<2 else float((e.max(axis=1)>0).mean())
                  for i,e in enumerate(edges)]
        if min(coverage)<.70:
            continue
        box=[x*pdf_size[0]/w,y*pdf_size[1]/h,(x+bw_)*pdf_size[0]/w,(y+bh)*pdf_size[1]/h]
        if not any(related(box,b['bbox']) for b in boxes):
            boxes.append({'bbox':box,'edge_coverage':coverage,'origin':'source_closed_border'})
    return sorted(boxes,key=lambda b:(b['bbox'][1],b['bbox'][0]))


def preserve_charts(row, detections, original_pictures):
    """Keep chart evidence and remove conflicting promoted grids from authoritative tables."""
    from compare_study.figures import figure,decorative
    result=copy.deepcopy(row)
    records=[]
    for d in detections:
        if d['label']!='chart':
            continue
        box=d['bbox']
        prior=[p for p in original_pictures if related(box,p['bbox'])]
        existing=[f for f in result.get('figures',[]) if not f.get('decorative') and related(box,f['bbox'])]
        removed=[]
        if prior:
            indices=[i for i,b in enumerate(result['table_bboxes']) if related(box,b)]
            for i in reversed(indices):
                removed.append({'table_index':i,'bbox':result['table_bboxes'].pop(i),
                                'table':result['tables'].pop(i),'numeric_evidence_eligible':False,
                                'reason':'original_picture_and_independent_chart_conflict_with_table'})
        if not existing:
            source=prior[0] if prior else {}
            caption=source.get('caption','')
            f=figure(f"chart-preserved-{row['page']}-{len(result.get('figures',[]))}",
                     source.get('bbox',box),'chart',source.get('ocr_text',''),caption,
                     model='PP-DocLayoutV3 + original Docling picture',status='source_preserved',
                     history=['picture' if prior else 'chart','chart_preserved'])
            f['decorative']=decorative(f['bbox'],row['_size'],f['kind'])
            result.setdefault('figures',[]).append(f)
        records.append({'bbox':box,'prior_picture':bool(prior),'already_figure':bool(existing),
                        'restored':not existing,'suppressed_tables':removed})
        result.setdefault('suppressed_table_candidates',[]).extend(removed)
    semantic=[f for f in result.get('figures',[]) if not f.get('decorative')]
    targets=[f for f in semantic if not f.get('parent_id')]
    result['figure_counts']={**result.get('figure_counts',{}),
        'figure_regions':sum(not f['is_logical_group'] for f in semantic),
        'figure_groups':sum(f['is_logical_group'] for f in semantic),'logical_figures':len(targets),
        'decorative_images':sum(f.get('decorative',False) for f in result.get('figures',[]))}
    return result,records
