"""Source-only H routing; validation normalizes footnotes without editing extraction."""
import re
import unicodedata
from preprocess.tables.table_quality import comparable_text, numbers, inspect_table

VERSION='production-h-3-footnote-glyph-context'
PAIR=re.compile(r'(?<![\w.])[-+]?\d+(?:[.,]\d+)*\s*\(\s*[-+\d.,%\s]+\)')
# A superscript letter after prose is a footnote, not part of the preceding word.
# Numerical powers and scientific subscripts deliberately do not match.
FOOTNOTE=re.compile(r'(?<=[A-Za-z]{3})\s*\$\s*\^\s*\{\s*[a-z]\s*\}\s*\$')
SUSPECT_GLYPH=re.compile(r'\bFc(?:[xy]R[Il1]+[ab]?|γR(?=[Il1]*[l1])[Il1]+[ab]?)(?:\s*\([A-Z]\d+\))?',re.I)
RECEPTOR_TOKEN=re.compile(r'\bFc[xyγ]R[IVXIl1]+[ab]?',re.I)
CANONICAL_RECEPTOR=re.compile(r'\bFcγR[IVX]+[abc]?(?=\W|$)')

def glyph_text(text):
    text=comparable_text(text)
    text=re.sub(r'\\+gamma\b','γ',text)
    text=re.sub(r'\\+(?:mathrm|text)\{([^{}]*)\}',r'\1',text)
    text=re.sub(r'\bFc\s*([xyγ])\s*R\s*([IVXIl1]+)\s*([abc]?)(?=\W|$)',
                lambda m:'Fc'+m[1]+'R'+m[2]+m[3],text)
    return text

def related(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    aa=(a[2]-a[0])*(a[3]-a[1]);bb=(b[2]-b[0])*(b[3]-b[1])
    return min(aa,bb)>0 and (intersection/min(aa,bb)>=.8 or intersection/(aa+bb-intersection)>=.5)

def adjacent_visual_context(box,figures,page_height):
    for f in figures:
        if f.get('decorative') or f.get('parent_id'):continue
        b=f['bbox'];horizontal=max(0,min(box[2],b[2])-max(box[0],b[0]))
        gap=max(0,b[1]-box[3],box[1]-b[3])
        if horizontal>=.5*min(box[2]-box[0],b[2]-b[0]) and gap<=.06*page_height:return f['figure_id']

def table_alarms(table):
    cells=[{'r':r,'c':c,'value_groups':len(PAIR.findall(comparable_text(str(text)))),'text':str(text)}
           for r,row in enumerate(table) if r for c,text in enumerate(row)
           if len(PAIR.findall(comparable_text(str(text))))>=2]
    return {'issues':['multiple_value_groups_in_one_cell'] if cells else [],'cells':cells}

def number_literals(table):
    return {v.rstrip('%').replace(',','') for v in numbers(' '.join(str(c) for r in table for c in r))}

def words(table,normalize_glyphs=False):
    text=FOOTNOTE.sub('',' '.join(str(c) for r in table for c in r))
    text=glyph_text(text) if normalize_glyphs else comparable_text(text)
    if normalize_glyphs:text=RECEPTOR_TOKEN.sub(' ',text)
    text=unicodedata.normalize('NFKC',text).casefold()
    return set(re.findall(r'[a-z]{4,}',re.sub(r'\\[a-z]+',' ',text)))

def candidate_gate(table,content,original=None):
    fatal={'invalid_span','span_outside_table','overlapping_spans','truncated_html','invalid_cell_nesting',
           'invalid_row_end','unclosed_row','nested_table','invalid_html'}
    issues=[]
    if not table or not any(str(c).strip() for row in table for c in row):issues.append('empty_output')
    if '<table' in content.lower():issues.extend(sorted(fatal & set(inspect_table(content)['issues'])))
    result={'accepted':not issues,'issues':issues,'semantic_status':'unverified source extraction candidate',
            'numeric_evidence_eligible':False,'gate_version':VERSION}
    if original is not None:
        missing=sorted(number_literals(original)-number_literals(table));old=words(original)
        raw_retained=len(old & words(table))/len(old) if old else 1.
        gain=len(table_alarms(original)['cells'])-len(table_alarms(table)['cells'])
        old_text=glyph_text(' '.join(str(c) for row in original for c in row))
        new_text=glyph_text(' '.join(str(c) for row in table for c in row))
        old_glyphs=len(SUSPECT_GLYPH.findall(old_text));new_glyphs=len(SUSPECT_GLYPH.findall(new_text))
        glyph_gain=old_glyphs-new_glyphs
        if old_glyphs>=2:
            old=words(original,normalize_glyphs=True);new=words(table,normalize_glyphs=True)
            retained=len(old & new)/len(old) if old else 1.
        else:retained=raw_retained
        variant=lambda text:{re.sub(r'\s+','',v) for v in re.findall(r'\b[A-Z]\s*\d{2,}\b',text)}
        missing_variants=sorted(variant(old_text)-variant(new_text))
        if missing:issues.append('existing_numeric_literals_not_preserved')
        if missing_variants:issues.append('existing_alphanumeric_variants_not_preserved')
        if retained<.90:issues.append('existing_header_label_words_not_preserved')
        if gain<=0 and not (old_glyphs>=2 and glyph_gain>0):issues.append('no_structural_alarm_reduction')
        if old_glyphs>=2 and gain<=0 and table_alarms(table)['issues']:issues.append('new_structural_alarm_in_glyph_repair')
        glyph_cell_checks=[]
        if old_glyphs>=2 and gain<=0:
            if len(table)!=len(original) or [len(row) for row in table]!=[len(row) for row in original]:
                issues.append('glyph_repair_changed_table_dimensions')
            else:
                for r,row in enumerate(original):
                    for c,value in enumerate(row):
                        if SUSPECT_GLYPH.search(glyph_text(str(value))):
                            check=glyph_gate(str(value),str(table[r][c]));glyph_cell_checks.append({'r':r,'c':c,**check})
                            if not check['accepted']:issues.append('glyph_cell_context_not_preserved')
        result.update(accepted=not issues,missing_original_numbers=missing,existing_label_word_recall=retained,
                      structural_alarm_reduction=gain,ambiguous_glyph_reduction=glyph_gain,
                      missing_alphanumeric_variants=missing_variants,
                      raw_label_word_recall=raw_retained,label_comparison_excludes_flagged_receptor_glyph_tokens=old_glyphs>=2,
                      glyph_cell_checks=glyph_cell_checks,
                      protection_scope='literal and label coverage, not semantic certification')
    return result

def glyph_gate(original,candidate):
    """Accept only source rereads differing inside a flagged receptor glyph token."""
    candidate=glyph_text(candidate).strip()
    valid=re.search(r'\bFcγR[IVX]+[ab]?(?:\s*\([A-Z]\d+\))?',candidate)
    old=SUSPECT_GLYPH.search(original)
    def surrounding(text,match):return re.sub(r'\s+',' ',text[:match.start()]+'<GLYPH>'+text[match.end():]).strip().casefold()
    accepted=bool(old and valid and surrounding(original,old)==surrounding(candidate,valid)
                  and numbers(original)==numbers(candidate)
                  and re.findall(r'\([A-Z]\d+\)',original)==re.findall(r'\([A-Z]\d+\)',candidate))
    return {'accepted':accepted,'content':candidate,'issues':[] if accepted else ['glyph_source_reread_requires_review'],
            'numeric_evidence_eligible':False,'semantic_status':'source-supported OCR candidate; not human verified'}

def boxed_regions(image,pdf_size):
    import cv2
    import numpy as np
    gray=cv2.cvtColor(np.array(image.convert('RGB')),cv2.COLOR_RGB2GRAY);h,w=gray.shape
    bw=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_MEAN_C,cv2.THRESH_BINARY_INV,31,15)
    horizontal=cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(max(24,w//30),1)))
    vertical=cv2.morphologyEx(bw,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(20,h//55))))
    lines=cv2.dilate(cv2.bitwise_or(horizontal,vertical),cv2.getStructuringElement(cv2.MORPH_RECT,(3,3)))
    contours,_=cv2.findContours(lines,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE);boxes=[]
    for contour in contours:
        x,y,ww,hh=cv2.boundingRect(contour)
        if ww<w*.40 or not h*.025<=hh<=h*.45:continue
        strip=4
        edges=[horizontal[max(0,y-strip):min(h,y+strip+1),x:x+ww],
               horizontal[max(0,y+hh-strip-1):min(h,y+hh+strip),x:x+ww],
               vertical[y:y+hh,max(0,x-strip):min(w,x+strip+1)],
               vertical[y:y+hh,max(0,x+ww-strip-1):min(w,x+ww+strip)]]
        coverage=[float((e.max(axis=0)>0).mean()) if i<2 else float((e.max(axis=1)>0).mean()) for i,e in enumerate(edges)]
        if min(coverage)<.70:continue
        box=[x*pdf_size[0]/w,y*pdf_size[1]/h,(x+ww)*pdf_size[0]/w,(y+hh)*pdf_size[1]/h]
        if not any(related(box,b['bbox']) for b in boxes):boxes.append({'bbox':box,'edge_coverage':coverage,'origin':'source_closed_border'})
    return sorted(boxes,key=lambda b:(b['bbox'][1],b['bbox'][0]))
