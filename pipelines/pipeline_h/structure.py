"""Lossless raw blocks and a separate, source-bound selected representation."""
import copy,html
from preprocess.tables.table_quality import inspect_table

def box_top_left(block,height,width):
    l,b,r,t=block['bbox']
    return [round(max(0,min(width,l)),4),round(max(0,min(height,height-t)),4),
            round(max(0,min(width,r)),4),round(max(0,min(height,height-b)),4)]

def table_rows(block):
    nr=max([block.get('n_rows',0)]+[c['r']+max(1,c.get('rs',1)) for c in block['cells']])
    nc=max([block.get('n_cols',0)]+[c['c']+max(1,c.get('cs',1)) for c in block['cells']])
    rows=[['']*nc for _ in range(nr)]
    for cell in block['cells']:
        if 0<=cell['r']<nr and 0<=cell['c']<nc:rows[cell['r']][cell['c']]=cell.get('text') or ''
    return rows

def table_html(block):
    rows=table_rows(block);cells={(c['r'],c['c']):c for c in block['cells']};covered=set();out=['<table>']
    for r,row in enumerate(rows):
        out.append('<tr>')
        for c,text in enumerate(row):
            if (r,c) in covered:continue
            cell=cells.get((r,c),{});rs=max(1,cell.get('rs',1));cs=max(1,cell.get('cs',1))
            tag='th' if cell.get('header') else 'td'
            attrs=(f' rowspan="{rs}"' if rs>1 else '')+(f' colspan="{cs}"' if cs>1 else '')
            out.append(f'<{tag}{attrs}>{html.escape(text)}</{tag}>')
            covered.update((rr,cc) for rr in range(r,r+rs) for cc in range(c,c+cs) if (rr,cc)!=(r,c))
        out.append('</tr>')
    return ''.join(out+['</table>'])

def cell_structure_issues(block):
    occupied=set();issues=[];nr=block.get('n_rows',0);nc=block.get('n_cols',0)
    for cell in block.get('cells',[]):
        r,c=cell['r'],cell['c'];rs=max(1,cell.get('rs',1));cs=max(1,cell.get('cs',1))
        if r<0 or c<0 or nr and r+rs>nr or nc and c+cs>nc:issues.append('raw_cell_span_outside_declared_table')
        area={(rr,cc) for rr in range(r,r+rs) for cc in range(c,c+cs)}
        if area & occupied:issues.append('overlapping_raw_cell_spans')
        occupied.update(area)
    return sorted(set(issues))

def parsed_rows(content):
    quality=inspect_table(content)
    nr=quality['rows'];nc=quality['columns'];rows=[['']*nc for _ in range(nr)]
    for c in quality['cells']:
        if c['row']<nr and c['column']<nc:rows[c['row']][c['column']]=c['text']
    return rows

def original_pictures(result,audit,page,height,width):
    events={e['ref']:e for e in audit.get('events',[])}
    return [{**copy.deepcopy(b),'bbox_top_left':box_top_left(b,height,width),
             'ocr_text':events.get(b['ref'],{}).get('ocr_text',b.get('ocr_text',''))}
            for b in audit.get('before_blocks',result['blocks']) if b['type']=='picture' and b['page']==page]
