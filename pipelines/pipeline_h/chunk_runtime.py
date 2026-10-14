"""Index source units by page once; preserve the existing packing implementation."""
import inspect

def enable_dom_schema():
    from preprocessing_chunk_schema import REF
    REF['properties'].update({
        'content_locator_type':{'type':'string','enum':['html_dom_node']},
        'html_ref':{'type':'object','required':['node_path','start','end','raw_fragment_sha256'],
            'additionalProperties':False,'properties':{
                'node_path':{'type':'string'},'start':{'type':'integer','minimum':0},
                'end':{'type':'integer','minimum':1},'raw_fragment_sha256':{'type':'string'}}}})

def accelerate(original):
    enable_dom_schema()
    if getattr(original.build,'_h_page_index',False):return
    source=inspect.getsource(original.build)
    old="""    for key in pages:
        physical = [u for u in units if not assets[u['unit_id']].get('is_logical_group') and
            (u['document_id'], u['version_id'], assets[u['unit_id']]['pdf_page']) == key]
        page_positions.update({u['unit_id']: i for i, u in enumerate(physical)})"""
    new="""    physical_by_page = defaultdict(list)
    for u in units:
        asset = assets[u['unit_id']]
        if not asset.get('is_logical_group'):
            physical_by_page[(u['document_id'], u['version_id'], asset['pdf_page'])].append(u)
    for key in pages:
        page_positions.update({u['unit_id']: i for i, u in enumerate(physical_by_page[key])})"""
    if source.count(old)!=1:raise ValueError('Existing chunker version differs; refusing an unverified patch')
    namespace={};exec(compile(source.replace(old,new),'<pipeline_h_page_index>', 'exec'),original.__dict__,namespace)
    original.build=namespace['build'];original.build._h_page_index=True
