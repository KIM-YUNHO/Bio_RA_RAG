"""Extract only complete, unique printed GMR/CI request slots.

Original bounded requests, whole source cells, actual local roles, independent
trial/analysis attribution and ambiguity checks are all required. No values are
computed or copied from evaluation answers; partial/unparsed requests abstain.
"""
from rag.literal_requests import compile_requests,automatic_roles_complete
from rag.answer_conditions import missing,attribution_missing,explicit_conflicts

VERSION='unique-literal-source-slots-1'


def unique(candidates,evidence,question,u):
    contract=compile_requests(question)
    if not contract or not automatic_roles_complete(question) or any(r['field']['dimension']!='statistic' or r['field']['value'] not in ('GMR','CI90') for r in contract['requests']):return None
    targets={v['value'] for r in contract['requests'] for c in r['conditions'] if c['dimension']=='study' for v in c['values']}
    if len(targets)!=1 or set(u.get('studies',[]))!=targets:return None
    from rag.selection_candidate import audits,resolve
    _,checked=audits(candidates,evidence,question);raw={}
    for cid,a in checked.items():
        c=candidates[cid];fact=a['fact']
        if c.get('value_eligible') is False or c['kind']!='table_cell' or fact.get('value_binding_status')!='whole_cell':continue
        if not any(check['status']=='match' for check in a['checks']):continue
        raw[cid]={'study':next(iter(targets)),'item':'printed GMR/CI cell','source_label':'원문 GMR/CI','value_id':cid,'exact':True,'conditions':[]}
    if not raw:return None
    proposals=resolve({'status':'partial','items':list(raw.values()),'premise_source_ids':[]},candidates,evidence,question,u,_complete_counts=False)['items']
    gaps=attribution_missing(proposals,evidence,u,missing(proposals,question,u))
    rejected={g['item_index'] for g in gaps}|{c['item_index'] for c in explicit_conflicts(proposals,question,u)}
    valid={item['source_selection']['value_id'] for n,item in enumerate(proposals) if n not in rejected
        and item.get('own_analysis_binding') and item.get('own_comparison_binding')}
    slots={r['id']:[] for r in contract['requests']}
    for cid in valid:
        for check in checked[cid]['checks']:
            if check['status']=='match':slots[check['request_id']].append(cid)
    # Equal values at different source locations/analyses are still ambiguous.
    if any(len(ids)!=1 for ids in slots.values()):return None
    selected=sorted({ids[0] for ids in slots.values()},key=lambda cid:(candidates[cid]['evidence'],candidates[cid]['start']))
    return {'status':'answered','items':[raw[cid] for cid in selected],'premise_source_ids':[],
        '_proof':{'version':VERSION,'request_source_ids':{k:v[0] for k,v in slots.items()},'source_ids':selected,
            'basis':'complete_unique_whole_source_slots_and_independent_attribution','new_model_calls':0,'semantic_approval':False}}
