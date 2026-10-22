"""Render explicit logical positions; model spans remain source-unverified."""
from html import escape

from preprocess.tables.table_quality import REDACTION


def render_table(rows):
    issues, output = [], ['<table>']
    for row in rows:
        output.append('<tr>')
        for cell in row['cells']:
            text, state = cell['text'], cell['state']
            if state == 'blank' and text.strip():
                issues.append('blank_cell_contains_text')
            elif state == 'visible' and not text.strip():
                issues.append('visible_cell_has_no_text')
            elif state == 'redacted':
                if REDACTION.sub('', text).strip():
                    issues.append('redacted_cell_contains_visible_text')
                if not text.strip():
                    text = '[redacted]'
            elif state == 'unreadable':
                issues.append('unreadable_cells')
                if not text.strip():
                    text = '[unreadable]'
            tag = 'th' if row['section'] == 'header' else 'td'
            output.append(f'<{tag}>' + escape(text).replace('\n', '<br>') + f'</{tag}>')
        output.append('</tr>')
    output.append('</table>')
    return ''.join(output), sorted(set(issues))


def render_structured_table(answer):
    """Validate occupancy before rendering; never fill a hole or flatten a span."""
    rows=answer['rows'];columns=answer['column_count'];issues=[]
    if [r['row_index'] for r in rows]!=list(range(len(rows))):
        issues.append('invalid_table_row_order')
    occupied={};body_seen=False
    for index,row in enumerate(rows):
        if row['section']=='body':body_seen=True
        elif body_seen:issues.append('table_header_after_body')
        positions=[c['column_index'] for c in row['cells']]
        if positions!=sorted(set(positions)):issues.append('invalid_table_column_order')
        for cell in row['cells']:
            left=cell['column_index'];bottom=index+cell['row_span'];right=left+cell['col_span']
            if bottom>len(rows) or right>columns:
                issues.append('table_span_out_of_bounds');continue
            if cell['row_span']>1 and any(r['section']!=row['section'] for r in rows[index:bottom]):
                issues.append('table_span_crosses_header_body')
            for r in range(index,bottom):
                for c in range(left,right):
                    if (r,c) in occupied:issues.append('table_cells_overlap')
                    occupied[(r,c)]=(index,left)
    if len(occupied)!=len(rows)*columns:issues.append('table_positions_missing')
    if issues:return '',sorted(set(issues))
    # Use the legacy literal/state checks; only the HTML span attributes differ.
    output=['<table>']
    for row in rows:
        output.append('<tr>')
        for cell in row['cells']:
            fragment,state_issues=render_table([{'section':row['section'],'cells':[cell]}])
            issues+=state_issues
            fragment=fragment[len('<table><tr>'):-len('</tr></table>')]
            tag='th' if row['section']=='header' else 'td'
            attributes=''.join(f' {name}="{cell[key]}"' for key,name in
                (('row_span','rowspan'),('col_span','colspan')) if cell[key]>1)
            output.append(fragment.replace(f'<{tag}>',f'<{tag}{attributes}>',1))
        output.append('</tr>')
    output.append('</table>')
    return ''.join(output),sorted(set(issues))
