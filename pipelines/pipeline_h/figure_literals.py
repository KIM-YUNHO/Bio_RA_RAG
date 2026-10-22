"""Keep ordinary printed figure text and captions without assigning visual semantics."""
from preprocess.visual.text_checks import figure_label_reference

def apply(assets,groups):
    lookup={a['asset_id']:a for a in assets}
    scopes=[g for g in groups or [] if g['kind']=='figure']
    grouped={uid for g in scopes for uid in g['member_asset_ids']}
    scopes += [{'group_id':a['asset_id'],'member_asset_ids':[a['asset_id']],'caption_text':'','caption_part_asset_ids':[]}
               for a in assets if a['asset_type']=='figure' and a['asset_id'] not in grouped]
    for group in scopes:
        parent=lookup[group['group_id']];fields=[]
        for uid in group.get('caption_part_asset_ids',[]):
            a=lookup[uid];text=a['selected']['content']
            if text.strip():fields.append({'role':'caption_literal','text':text,'asset_id':uid,'source_refs':a['source_refs'],'engine':a['base']['engine']})
        if not fields and group['caption_text'].strip():
            fields.append({'role':'caption_literal','text':group['caption_text'],'asset_id':parent['asset_id'],
                           'source_refs':parent['source_refs'],'engine':'source caption association'})
        if not fields and (parent.get('docling_structure') or {}).get('caption','').strip():
            fields.append({'role':'attached_caption_literal','text':parent['docling_structure']['caption'],
                'asset_id':parent['asset_id'],'source_refs':parent['source_refs'],'engine':'Docling attached caption metadata',
                'spatial_status':'caption text associated with source visual; separate caption bbox unavailable'})
        for uid in group['member_asset_ids']:
            a=lookup[uid];text=figure_label_reference(a['base'].get('printed_text_reference') or '')
            if text.strip():fields.append({'role':'printed_literal','text':text,'asset_id':uid,'source_refs':a['source_refs'],
                                          'engine':a['base'].get('printed_literal_engine',a['base']['engine'])})
        parent['selected']={'origin':'h_figure_printed_literals','content':'\n'.join(dict.fromkeys(f['text'] for f in fields))}
        parent['h_literal_fields']=fields;parent['figure_meaning_generation']=False
        parent.pop('figure_labels',None)
