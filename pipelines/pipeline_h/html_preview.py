"""Verified preview pages for DOM units; text is read from the DOM, not OCR."""
import math
from pathlib import Path
import fitz
from pipelines.pipeline_h.html_source import parse
from pipelines.pipeline_h.prepare import write
from preprocess.ingest import sha256

def build(doc,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    raw=Path(doc['raw_path']).read_text(encoding='utf-8-sig')
    if sha256(doc['raw_path'])!=doc['version_id']:raise ValueError('Original HTML changed')
    parsed=parse(raw,doc['document_id'],doc['version_id']);path=folder/(doc['version_id']+'.pdf')
    css='body {font-family:sans-serif;font-size:11pt;} table {border-collapse:collapse;} td,th {border:0.5pt solid #555;padding:3pt;}'
    groups=[];group=[];used=0
    for unit in parsed['units']:
        measure=fitz.Story(html=unit['render_html'],user_css=css)
        more,rect=measure.place(fitz.Rect(36,36,559,12000));rect=fitz.Rect(rect)
        if more:raise ValueError('DOM unit exceeds preview limits: '+unit['dom_ref']['node_path'])
        needed=math.ceil(rect.height)+8
        if group and used+needed>770:groups.append((group,842));group=[];used=0
        if needed>770:groups.append(([(unit,needed)],needed+72));continue
        group.append((unit,needed));used+=needed
    if group:groups.append((group,842))
    writer=fitz.DocumentWriter(str(path));locations=[]
    try:
        for number,(group,height) in enumerate(groups,1):
            bounds=fitz.Rect(0,0,595,height);device=writer.begin_page(bounds);y=36
            for unit,needed in group:
                story=fitz.Story(html=unit['render_html'],user_css=css)
                more,filled=story.place(fitz.Rect(36,y,559,height-36));filled=fitz.Rect(filled)
                if more or not bounds.contains(filled):raise ValueError('DOM preview overflow: '+unit['dom_ref']['node_path'])
                story.draw(device)
                locations.append({'pdf_page':number,'bbox_pdf':list(filled),'page_size':[595,height],
                    'locator_notice':'Generated DOM preview page; not an original HTML page number'})
                y+=needed
            writer.end_page()
    finally:writer.close()
    if len(locations)!=len(parsed['units']):raise ValueError('Preview content unit missing')
    parsed.update(document_id=doc['document_id'],version_id=doc['version_id'],raw_path=doc['raw_path'],
        raw_sha256=doc['version_id'],rendered_path=str(path.resolve()),rendered_sha256=sha256(path),
        locations=locations,content_primary='original_html_dom',preview_primary=False)
    write(folder/(doc['version_id']+'.json'),parsed)
    return parsed
