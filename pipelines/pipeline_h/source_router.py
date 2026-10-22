"""Format-based routing for future runs; no product/page exceptions."""
import copy
from pathlib import Path
from pipelines.pipeline_h.prepare import read,write
from pipelines.pipeline_h.html_preview import build
from pipelines.pipeline_h.html_source import parse
from preprocess.ingest import sha256

def ensure(run):
    run=Path(run);intake=read(run/'intake_manifest.json');changed=False
    if 'original_document_contract' not in intake:
        if (run/'selection_manifest.json').exists():
            raise ValueError('Legacy selected run must be upgraded into a new run with repair_extraction; source checkpoints cannot be silently relabeled')
        intake['original_document_contract']=copy.deepcopy(intake['documents']);changed=True
    for d in intake['documents']:
        if d['kind']!='html':continue
        path=run/'html_dom_preview'/(d['version_id']+'.json')
        if path.exists():
            dom=read(path)
            if dom['raw_sha256']!=sha256(d['raw_path']) or dom['rendered_sha256']!=sha256(dom['rendered_path']):
                raise ValueError('DOM preview source changed')
        else:dom=build(d,run/'html_dom_preview')
        if d.get('rendered_path')!=dom['rendered_path']:
            d.update(rendered_path=dom['rendered_path'],rendered_sha256=dom['rendered_sha256']);changed=True
    if changed:write(run/'intake_manifest.json',intake)

def result(doc,run):
    from preprocess.tables.table_quality import inspect_table
    dom=read(Path(run)/'html_dom_preview'/(doc['version_id']+'.json'));blocks=[]
    for u,loc in zip(dom['units'],dom['locations']):
        l,t,r,b=loc['bbox_pdf'];h=loc['page_size'][1]
        block={'type':'table' if u['kind']=='table' else 'text','ref':u['id'],'parent':u['dom_ref']['node_path'].rsplit('/',1)[0],
            'page':loc['pdf_page'],'bbox':[l,h-b,r,h-t],'source':'original_html_dom','layer':'body','route':'HTML_DOM',
            'label':'section_header' if u['tag'].startswith('h') and u['tag'][1:].isdigit() else 'text',
            'text':u['text'],'status':'kept','furniture':False,'section_path':u['section_path'],'source_structure':u,
            'speaker':'unknown','study_ids':[],'redacted':False}
        if u['kind']=='table':
            q=inspect_table(u['text']);block.update(selected_html=u['text'],n_rows=q['rows'],n_cols=q['columns'],caption='',
                cells=[{'r':c['row'],'c':c['column'],'rs':c['row_span'],'cs':c['column_span'],'text':c['text'],'header':c['is_header'],'bbox':None} for c in q['cells']])
        blocks.append(block)
    return {'engine':'Python HTMLParser original DOM','blocks':blocks,'timing':{'html_dom':0},
        'routes':[{'page':n,'route':'HTML_DOM','reason':'original DOM content primary'} for n in range(1,max(x['pdf_page'] for x in dom['locations'])+1)],
        'qa':{'source_units':len(blocks),'tables':sum(b['type']=='table' for b in blocks)},'source_dom_manifest':str(Path(run)/'html_dom_preview'/(doc['version_id']+'.json'))}
