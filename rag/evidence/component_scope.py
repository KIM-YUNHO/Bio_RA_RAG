"""Explicit local part labels joined to an actually supplied parent heading.

No propagation from a page number or registry family alone. The value's own
paragraph/caption must name one part and the connected source must name its
parent; both quotations and the selected source interval are rechecked.
"""
import re
from rag.retrieval import study_registry as sr

VERSION='explicit-component-scope-1'


def establish(candidate,evidence,target,facet=None):
    study=sr.load().get(target,{})
    part=re.search(r'\bpart\s+(\d+)\b',study.get('internal_id',''),re.I)
    parent=study.get('parent')
    if not part or not parent:return None
    block=evidence[candidate['evidence']];text=block['text']
    in_table=candidate.get('table_start') is not None or candidate.get('parent_table_start') is not None
    if candidate.get('table_start') is not None:
        from rag.evidence.population_counts import table_caption
        caption=table_caption(text,candidate['table_start'])
        local=caption['quote'] if caption else ''
    elif candidate['kind']=='literal_number' and candidate.get('parent_table_start') is not None:
        from rag.evidence.population_counts import table_caption
        caption=table_caption(text,candidate['parent_table_start'])
        local=caption['quote'] if caption else ''
    else:
        # Source-ID sentences/numbers may be smaller than their original
        # paragraph. Keep the printed component branch from that paragraph,
        # bounded by a blank line, table or visible source gap.
        start,end=candidate['start'],candidate['end']
        boundaries=list(re.finditer(r'\n\s*\n|\[UNSUPPLIED SOURCE GAP\]|<table\b|</table\s*>',text,re.I))
        a=max((m.end() for m in boundaries if m.end()<=start),default=0)
        b=min((m.start() for m in boundaries if m.start()>=end),default=len(text))
        local=text[a:b]
    if '[UNSUPPLIED SOURCE GAP]' in local:return None
    parts={m[1] for m in re.finditer(r'\bpart\s+(\d+)\b',local,re.I)}
    if not in_table and len(parts)>1 and (candidate['kind'] in ('literal_number','literal_term') or len(candidate['quote'])<len(local)):
        # A component branch in the value's own paragraph remains in force
        # until the next printed Part label. It cannot cross a source gap or
        # another paragraph. Do not assign the parent paragraph wholesale.
        base=text.find(local)
        offset=candidate['start']-base
        labels=list(re.finditer(r'\bpart\s+(\d+)\b',local,re.I))
        before=[m for m in labels if m.start()<=offset]
        if before:
            label=before[-1];end=min((m.start() for m in labels if m.start()>offset),default=len(local))
            local=local[label.start():end]
            parts={m[1] for m in re.finditer(r'\bpart\s+(\d+)\b',local,re.I)}
    shared_design=(facet=='design' and candidate['kind']=='source_paragraph' and part[1] in parts
        and bool(re.search(r'\b(?:Phase\s+\d|randomi[sz]ed|double[ -]blind)\b',local,re.I)))
    # A printed design of an explicitly two-part parent applies to its named
    # components. This is a design-only relation, never inheritance of N,
    # doses, assays or outcomes. Require the parent in the selected paragraph
    # and the component in the same supplied source block, not page metadata.
    component_design=(facet=='design' and candidate['kind']=='source_paragraph'
        and parent in {k for _,_,k in sr.finditer(local,block.get('product_id'))}
        and bool(re.search(r'\b(?:2|two)[ -]part\b',local,re.I))
        and bool(re.search(r'\b(?:Phase\s+\d|randomi[sz]ed|double[ -]blind)\b',local,re.I))
        and '[UNSUPPLIED SOURCE GAP]' not in text
        and bool(re.search(r'\bpart\s+'+re.escape(part[1])+r'\b',text,re.I))
        and not ({k for _,_,k in sr.finditer(text,block.get('product_id'))}-set((parent,target))))
    if component_design:shared_design=True
    if parts!={part[1]} and not shared_design:return None
    ids={k for _,_,k in sr.finditer(local,block.get('product_id'))}
    if ids-set((parent,target)):return None
    from rag.evidence.source_context import connected
    for key,context in evidence.items():
        if not connected(block,context):continue
        matches=list(sr.finditer(context['text'],block.get('product_id')))
        keys={k for _,_,k in matches}
        if parent not in keys or keys-set((parent,target)):continue
        start,end,_=next(m for m in matches if m[2]==parent)
        return {'version':VERSION,'target':target,'parent':parent,'part':part[1],
            'value_evidence':candidate['evidence'],'source_range':[candidate['start'],candidate['end']],
            'local_quote':local,'parent_quote':context['text'][start:end],'parent_evidence':key,
            'facet':facet,'basis':'printed_parent_design_with_explicit_component' if shared_design else 'own_single_component_label_and_connected_printed_parent'}
    return None


def validate(item,evidence,target):
    proof=item.get('source_component_scope')
    if not proof or proof.get('target')!=target:return False
    key=proof.get('value_evidence');span=proof.get('source_range',[])
    if key not in evidence or len(span)!=2:return False
    text=evidence[key]['text'];start,end=span
    if not 0<=start<end<=len(text) or text[start:end]!=item['value']:return False
    table_start=text.rfind('<table',0,start)
    table_end=text.rfind('</table>',0,start)
    candidate={'evidence':key,'start':start,'end':end,'quote':item['quote'],
               'kind':'table_cell' if table_start>table_end else 'source_paragraph'}
    if table_start>table_end:candidate['table_start']=table_start
    elif len(item['value'])<len(item['quote']):
        candidate['kind']='literal_number' if re.search(r'\d',item['value']) else 'literal_term'
        candidate['quote_start']=text.find(item['quote'])
    return establish(candidate,evidence,target,proof.get('facet'))==proof
