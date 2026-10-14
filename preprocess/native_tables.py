"""Conservative PDF grid candidate. No OCR, inferred spans or inferred cell text."""
from html import escape

VERSION = 'native-flat-grid-1'


def grids(page, image_size):
    import fitz
    from collection import text_layer
    quality, replacements = text_layer.inspect(page)
    if quality['needs_ocr'] or text_layer.private_use_count(text_layer.normalize(page.get_text(), replacements)):
        return []
    drawings = page.get_drawings()
    if sum(len(d['items']) for d in drawings) < 6:
        return []
    found = page.find_tables(strategy='lines_strict', paths=drawings)
    chars = [c for b in page.get_text('rawdict')['blocks'] if b['type'] == 0
             for line in b['lines'] for span in line['spans'] for c in span['chars'] if c['c'].strip()]
    images = [fitz.Rect(i['bbox']) for i in page.get_image_info()]
    output = []
    def pixel(box):
        box = fitz.Rect(box) * page.rotation_matrix
        return [v * (image_size[0]/page.rect.width if i%2 == 0 else image_size[1]/page.rect.height)
                for i,v in enumerate(box)]
    for table in found.tables:
        if table.row_count < 2 or table.col_count < 2 or table.row_count * table.col_count > 1200:
            continue
        bounds = fitz.Rect(table.bbox)
        if any((bounds & img).get_area() > 0 for img in images):
            continue
        rows = [r.cells for r in table.rows]
        if any(len(row) != table.col_count or any(c is None for c in row) for row in rows):
            continue
        cells = [fitz.Rect(c) for row in rows for c in row]
        # Every row uses the same columns and every column the same rows; no spans.
        if any(abs(c.x0-cells[j].x0)>0.1 or abs(c.x1-cells[j].x1)>0.1
               for row in rows for j,c in enumerate(map(fitz.Rect,row))):
            continue
        if any(abs(c.y0-fitz.Rect(row[0]).y0)>0.1 or abs(c.y1-fitz.Rect(row[0]).y1)>0.1
               for row in rows for c in map(fitz.Rect,row)):
            continue
        if any((a & b).get_area()>0.01 for i,a in enumerate(cells) for b in cells[i+1:]):
            continue
        assigned = [[] for _ in cells]
        valid = True
        for char in chars:
            rect = fitz.Rect(char['bbox']); centre = (rect.tl + rect.br)/2
            if not bounds.contains(centre):
                continue
            hits = [i for i,c in enumerate(cells) if c.contains(centre)]
            if len(hits) != 1 or not (cells[hits[0]] + (-1,-1,1,1)).contains(rect):
                valid = False;break
            assigned[hits[0]].append(char)
        if not valid or not any(assigned):
            continue
        # Use PDF's line order/spacing, and verify all nonspace glyphs exactly once.
        texts = []
        for cell, expected in zip(cells, assigned):
            text = text_layer.normalize(page.get_text('text', clip=cell), replacements).strip()
            if ''.join(text.split()) != ''.join(text_layer.normalize(c['c'],replacements) for c in expected):
                valid = False;break
            texts.append(text)
        if not valid:
            continue
        import re
        # Physical outer cells can conceal many logical rows separated only by
        # line breaks. Preserve those tables on the structural recognition path.
        numeric_lines=lambda text:sum(bool(re.search(r'\d',line) and re.fullmatch(r'[\d\s.,%±−–+\-()/Ee<>≤≥]+',line))
                                      for line in text.splitlines())
        if any(numeric_lines(text)>=2 for text in texts):
            continue
        html = '<table>' + ''.join('<tr>' + ''.join('<td>'+escape(texts[r*table.col_count+c]).replace('\n','<br>')+'</td>'
                 for c in range(table.col_count)) + '</tr>' for r in range(table.row_count)) + '</table>'
        from .table_quality import inspect_table
        reference=text_layer.normalize(page.get_text('text',clip=bounds),replacements)
        if inspect_table(html,reference,True)['issues']:
            continue
        output.append({'version':VERSION, 'origin':'native_table_grid', 'bbox_pdf':list(table.bbox),
            'bbox_input_px':pixel(table.bbox), 'row_count':table.row_count, 'col_count':table.col_count,
            'cells':[{'row':i//table.col_count,'col':i%table.col_count,'bbox_pdf':list(c),'text':texts[i]}
                     for i,c in enumerate(cells)], 'content':html})
    return output


def select(data, label, box):
    if label != 'table':return None
    def area(r):return max(0,r[2]-r[0])*max(0,r[3]-r[1])
    candidates=[]
    for grid in data.get('tables',[]):
        region=grid['bbox_input_px']
        intersection=area([max(box[0],region[0]),max(box[1],region[1]),min(box[2],region[2]),min(box[3],region[3])])
        if intersection/max(area(region),1)>=.98 and intersection/max(area(box),1)>=.90:
            candidates.append(grid)
    return candidates[0] if len(candidates)==1 else None
