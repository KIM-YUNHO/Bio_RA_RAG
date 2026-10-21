"""Closed scopes for an explicitly printed standalone table row heading.

One nonempty leftmost title and explicitly empty remaining cells open a block.
The next title, blank row, header or table end closes it. No clinical inference.
"""
from collections import defaultdict
import re

VERSION='printed-closed-row-block-1'

def blocks(cells):
    rows=defaultdict(list)
    for c in cells:rows[c['row']].append(c)
    def val(c):return c.get('value',c.get('text','')).strip()
    def span(c,k):return c.get(k,c.get({'rowspan':'row_span','colspan':'col_span'}[k],1))
    def header(c):return c.get('explicit_header',c.get('is_header',False))
    opened=None;result=[]
    for row,own in sorted(rows.items()):
        nonempty=[c for c in own if val(c)]
        left=nonempty[0] if len(nonempty)==1 else None
        title=bool(left and left['column']==0 and span(left,'rowspan')==1 and any(ch.isalpha() for ch in val(left))
            and not re.match(r'^\s*[(+−–-]*\d',val(left))
            and (span(left,'colspan')>1 or len(own)>1 and all(span(c,'rowspan')==1 for c in own)))
        boundary=title or not nonempty or any(header(c) for c in own)
        if boundary and opened is not None:
            if row>opened['start_row']+1:result.append({**opened,'end_row':row})
            opened=None
        if title:opened={'heading_id':left.get('id'),'heading_row':row,'start_row':row}
    end=max(rows,default=-1)+1
    if opened is not None and end>opened['start_row']+1:result.append({**opened,'end_row':end})
    return result

def heading_for(cell,cells,closed_blocks=None):
    if cell['column']==0:return None
    closed=blocks(cells) if closed_blocks is None else closed_blocks
    return next((b['heading_id'] for b in closed if b['start_row']<cell['row']<b['end_row']),None)
