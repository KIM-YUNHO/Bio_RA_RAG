"""Bounded descriptive PK requests and facts from the value's own printed grid.

No question IDs, pages or expected values. Whole compound cells remain whole.
Unique source slots can be completed; unresolved scope/conditions cannot.
"""
from copy import deepcopy
from itertools import product
from functools import lru_cache
import re
from rag.contract_terms import atoms, key
from rag.contract_relations import arm, table_fact
from rag.contract_match import condition
from rag.grounded_table_graph import build

VERSION='printed-descriptive-pk-2'
STAT_RULES=[('median',r'\bmedian\b|중앙값|중앙치'),
    ('mean',r'\b(?:arithmetic\s+)?mean\b|\baverage\b|평균'),
    ('SD',r'\bSD\b|standard\s+deviation|표준\s*편차'),
    ('minimum',r'\bmin(?:imum)?\b|최솟값|최소값'),
    ('maximum',r'\bmax(?:imum)?\b|최댓값|최대값'),
    ('range',r'\brange\b|범위')]
STAT_LABELS={'mean':'평균','median':'중앙값','SD':'SD','minimum':'최소','maximum':'최대','range':'범위'}


def statistics(text):
    # Endpoint subscripts are part of the endpoint, not a minimum/maximum
    # request. Retain offsets while masking recognized endpoint mentions.
    masked=list(text)
    for a in atoms(text):
        if a['dimension']=='endpoint':masked[a['start']:a['end']]=' '* (a['end']-a['start'])
    outside_endpoints=''.join(masked)
    result={name for name,pattern in STAT_RULES if re.search(pattern,outside_endpoints,re.I)}
    if re.search(r'geometric\s+(?:LS\s*)?mean|기하\s*평균',text,re.I):result.discard('mean')
    return result


def request(question):
    mentions=atoms(question)
    endpoints=list(dict.fromkeys(a['value'] for a in mentions if a['dimension']=='endpoint'))
    if not endpoints or re.search(r'GMR|기하\s*평균|geometric|\bCI\b|confidence|신뢰\s*구간',question,re.I):return None
    if re.search(r'체중|\d+(?:\.\d+)?\s*kg\b',question,re.I):return None
    if re.search(r';|\n|반면|whereas|respectively|각각|제외|보정\s*전|아닌|말고|except|exclude|rather than|either|또는|혹은',question,re.I):return None
    stats=statistics(question)
    if not stats and not any(a['dimension']=='attribute' and a['value']=='unit' for a in mentions):return None
    # Repeated independently stated fields can be correlated across clauses.
    if any(len(list(re.finditer(p,question,re.I)))>1 for n,p in STAT_RULES if n in stats):return None
    shared=[]
    for dimension in ('population','route','analysis_set','covariate','analysis_model','analysis_timing'):
        values=list(dict.fromkeys(a['value'] for a in mentions if a['dimension']==dimension))
        if len(values)>1 and dimension!='covariate':return None
        shared.extend((dimension,v) for v in values)
    from rag.question_anchors import catalog
    named=list(dict.fromkeys(a['value'] for a in catalog(question).values() if a['dimension']=='comparison'))
    references=[v for v in named if v in ('EU-Stelara','US-Stelara')]
    from rag.study_registry import products
    if references and not any(v in products() for v in named):return None
    arms=named if references else []
    if references and not re.search(r'군|arms?|groups?',question,re.I):return None
    doses=list(dict.fromkeys(a['value'] for a in mentions if a['dimension']=='dose'))
    per_arm=bool(re.search(r'군별|각\s*군|per[ -]?(?:arm|group)|each\s+(?:arm|group)|by\s+(?:arm|group)',question,re.I))
    if len(endpoints)*max(1,len(stats))*max(1,len(arms))*max(1,len(doses))>64:return None
    return {'version':VERSION,'endpoints':endpoints,'statistics':sorted(stats),'arms':arms,'doses':doses,
        'shared_conditions':shared,'per_arm':per_arm,
        'unit_requested':any(a['dimension']=='attribute' and a['value']=='unit' for a in mentions),
        'complete_question_interpretation':False,'semantic_approval':False}


def _printed_arm(text,product_id=None):
    from rag.treatment_identity import printed_arm
    return printed_arm(text,product_id)


def fact(candidate,candidates,evidence,graph=None):
    if candidate['kind']!='table_cell' or candidate.get('source_header'):return None
    own=table_fact(candidate,evidence,graph=graph)
    if not own or own['relationship_status']!='structural_candidate' or own['value_binding_status']!='whole_cell':return None
    contexts=[candidates[i] for i in candidate.get('context_ids',[]) if candidates[i]['kind']=='table_cell']
    roles=deepcopy(own['observations']);printed=[]
    for c in contexts:
        observed=atoms(c['value'])
        if re.search(r'geometric\s+(?:LS\s*)?mean',c['value'],re.I):observed=[a for a in observed if a['value']!='mean']
        for name in statistics(c['value']):
            if name not in {a['value'] for a in observed if a['dimension']=='statistic'}:
                observed.append({'dimension':'statistic','value':name})
        units=[]
        for m in re.finditer(r'\(\s*(h|L|mL)\s*\)',c['value'],re.I):units.append(m[1].casefold())
        observed.extend({'dimension':'unit','value':u} for u in units)
        treatment=_printed_arm(c['value'],evidence[candidate['evidence']].get('product_id'))
        if treatment:observed.append({'dimension':'arm','value':treatment})
        for a in observed:
            if a['dimension'] not in ('endpoint','statistic','unit','dose','arm'):continue
            roles.append({**a,'basis':'own_printed_table_context','source_id':c['id'],'semantic_status':'unverified'})
            printed.append({'dimension':a['dimension'],'value':a['value'],'source_id':c['id']})
    # Keep only own-context statistic interpretations; e.g. geometric mean is
    # not arithmetic mean. Several printed roles in one complete cell are valid.
    roles=[a for a in roles if a['dimension']!='statistic' or a.get('source_id')]
    return {**own,'observations':roles,'printed_roles':printed}


def compatible(req,own):
    if not own:return False
    def values(dim):return {a['value'] for a in own['observations'] if a['dimension']==dim}
    if len(values('endpoint'))!=1 or not values('endpoint')&set(req['endpoints']):return False
    if req['statistics'] and not values('statistic')&set(req['statistics']):return False
    if req['arms'] and (len(values('arm'))!=1 or not values('arm')&set(req['arms'])):return False
    if req['doses'] and (len(values('dose'))!=1 or not values('dose')&set(req['doses'])):return False
    if req['unit_requested'] and not values('unit'):return False
    return all(condition(own,d,'eq',[v])['status']=='match' for d,v in req['shared_conditions'])


def supplement(candidates,evidence,question):
    req=request(question)
    if not req or not req['statistics']:return
    from rag.source_selection import catalog
    from rag.regulatory_scope import eligible
    raw=catalog(evidence);graphs={eid:build(b) for eid,b in evidence.items()}
    for cid,c in raw.items():
        own=fact(c,raw,evidence,graphs[c['evidence']])
        if not compatible(req,own) or not eligible(c,raw,evidence,question):continue
        if not re.match(r'^\s*[($±+−–-]*\s*\d',c['value']):continue
        candidates[cid]=deepcopy(c)
        for parent in c.get('context_ids',[]):candidates.setdefault(parent,deepcopy(raw[parent]))


def apply(candidates,evidence,question):
    req=request(question)
    if not req:return
    graphs={eid:build(b) for eid,b in evidence.items()}
    for c in candidates.values():
        own=fact(c,candidates,evidence,graphs[c['evidence']])
        if own:
            c['printed_pk_fact']=own
            if not compatible(req,own):
                c['value_eligible']=False;c['selection_exclusion']='different_printed_pk_role'
            else:
                from rag.regulatory_scope import eligible
                c['value_eligible']=eligible(c,candidates,evidence,question)
        elif c['kind'] in ('literal_number','table_cell','literal_term'):
            c['value_eligible']=False;c['selection_exclusion']='descriptive_pk_requires_whole_bound_source'
        elif c['kind'] in ('source_paragraph','source_line'):
            found={a['value'] for a in atoms(c['value']) if a['dimension']=='endpoint'}
            # A whole sentence can preserve several explicitly requested
            # endpoints without assigning its separate numbers by proximity.
            quantified=bool(re.search(r'\d+(?:\.\d+)?\s*(?:L(?:/day)?|mL(?:/day/kg)?|days?|hours?|h)\b',c['value'],re.I))
            compatible_text=bool(found and found<=set(req['endpoints']) and quantified)
            if req['statistics']:compatible_text=compatible_text and bool(statistics(c['value'])&set(req['statistics']))
            if req['per_arm'] or req['arms']:compatible_text=False # prose lists require separate correspondence proof
            from rag.regulatory_scope import eligible
            compatible_text=compatible_text and eligible(c,candidates,evidence,question)
            observed=atoms(c['value'])
            for dimension,value in req['shared_conditions']:
                if value not in {a['value'] for a in observed if a['dimension']==dimension}:compatible_text=False
            if req['doses'] and not set(req['doses'])&{a['value'] for a in observed if a['dimension']=='dose'}:compatible_text=False
            if compatible_text:
                c['value_eligible']=True;c['value_binding']='whole_contiguous_source_text'
                c['printed_pk_prose_roles']={'endpoints':sorted(found),'basis':'whole_quantitative_source_sentence',
                    'individual_number_correspondence_certified':False,'semantic_approval':False}
            else:c['value_eligible']=False;c['selection_exclusion']='no_requested_quantitative_pk_sentence'
    # Prefer a complete original sentence over its larger parent when both
    # supply exactly the same requested roles. Preserve the parent as context.
    for c in candidates.values():
        if c.get('value_eligible') is False or c['kind']!='source_paragraph' or c.get('source_sentence'):continue
        smaller=[v for v in candidates.values() if v.get('source_sentence') and v.get('value_eligible') is not False
            and v['evidence']==c['evidence'] and c['start']<=v['start']<v['end']<=c['end']
            and {a['value'] for a in atoms(v['value']) if a['dimension']=='endpoint'}==
                {a['value'] for a in atoms(c['value']) if a['dimension']=='endpoint'}]
        if smaller:c['value_eligible']=False;c['selection_exclusion']='complete_quantitative_sentence_available'


def conditions(candidate,candidates):
    own=candidate.get('printed_pk_fact')
    if not own:return None
    result=[];seen=set()
    for a in own['printed_roles']:
        dim=a['dimension'];cid=a['source_id']
        if (dim,cid) in seen:continue
        seen.add((dim,cid));c=candidates[cid]
        result.append({'dimension':dim,'value':c['value'],'quote':c['quote'],'evidence':c['evidence']})
    return result


def relabel(item,candidate,candidates):
    own=candidate.get('printed_pk_fact')
    if not own:
        prose=candidate.get('printed_pk_prose_roles')
        if not prose:return
        # The whole original sentence already preserves population, units and
        # the printed correspondence. Nearby source IDs do not prove that a
        # dosing/method qualifier belongs to this result. Keep study identity
        # for the independent scope checks and retain removed fields for audit.
        item['removed_unbound_pk_qualifiers']=[c for c in item['conditions'] if c['dimension']!='study']
        item['conditions']=[c for c in item['conditions'] if c['dimension']=='study']
        item['model_source_label']=item['source_label']
        item['source_label']='원문 PK 문장 · '+' / '.join(prose['endpoints'])
        item['item']='whole quantitative PK source sentence'
        item['printed_pk_role_binding']={'version':VERSION,**deepcopy(prose)}
        return
    keep=[c for c in item['conditions'] if c['dimension'] not in ('endpoint','statistic','unit','dose','arm','row_header','column_header')]
    item['conditions']=[*keep,*conditions(candidate,candidates)]
    roles=own['printed_roles'];labels=[]
    for dim in ('arm','dose','endpoint','statistic'):
        values=list(dict.fromkeys(a['value'] for a in roles if a['dimension']==dim))
        if values:labels.append('/'.join(STAT_LABELS.get(v,v) for v in values))
    item['model_source_label']=item['source_label'];item['source_label']=' · '.join(labels) or '원문 PK 표'
    item['item']='printed descriptive PK cell'
    item['printed_pk_role_binding']={'version':VERSION,'basis':'own_row_column_or_closed_block','semantic_approval':False}


def unique_selections(candidates,evidence,question,u):
    req=request(question)
    if not req or not req['statistics']:return None
    if len(u.get('studies',[]))>1:return None
    study=next(iter(u.get('studies',[])),'none')
    from rag.source_selection import resolve
    from rag.answer_conditions import attribution_missing
    groups={}
    for cid,c in candidates.items():
        own=c.get('printed_pk_fact')
        if c.get('value_eligible') is False or not compatible(req,own):continue
        roles={d:{a['value'] for a in own['observations'] if a['dimension']==d} for d in ('endpoint','statistic','arm','dose')}
        if (req['per_arm'] or req['arms']) and len(roles['arm'])!=1:continue
        if any(len(roles[d])>1 for d in ('endpoint','arm','dose')):continue
        selection={'study':study,'item':'printed descriptive PK cell','source_label':'원문 PK 표','value_id':cid,'exact':True,'conditions':[]}
        proposal=resolve({'status':'partial','items':[selection]},candidates,evidence,question,u)['items'][0]
        relabel(proposal,c,candidates)
        if attribution_missing([proposal],evidence,u,[]):continue
        for stat in roles['statistic']&set(req['statistics']):
            slot=(next(iter(roles['endpoint'])),stat,next(iter(roles['arm']),None),next(iter(roles['dose']),None))
            groups.setdefault(slot,[]).append((selection,c['value']))
    if not groups:return None
    # Two different printed results for the same requested slot are unresolved.
    if any(len({key(v) for _,v in choices})!=1 for choices in groups.values()):return None
    arms=req['arms'] or sorted({s[2] for s in groups if s[2] is not None}) if req['per_arm'] or req['arms'] else [None]
    doses=req['doses'] or [None]
    if arms==[None] and any(s[2] for s in groups):
        observed={s[2] for s in groups}
        if len(observed)>1:return None
        arms=list(observed)
    if not req['doses'] and any(s[3] for s in groups):
        observed={s[3] for s in groups}
        if len(observed)>1:return None
        doses=list(observed)
    needed=set(product(req['endpoints'],req['statistics'],arms,doses))
    if not needed or not needed<=groups.keys():return None
    selections={}
    for slot in sorted(needed,key=str):
        selection,_=groups[slot][0];selections.setdefault(selection['value_id'],selection)
    return {'status':'answered','items':list(selections.values()),'premise_source_ids':[],
        '_proof':{'version':VERSION,'slots':len(needed),'source_ids':list(selections),'new_model_calls':0,'semantic_approval':False}}


def finalize(out,candidates,evidence,question,u):
    req=request(question)
    if not req:return False
    supported=unique_selections(candidates,evidence,question,u)
    selected={i.get('source_selection',{}).get('value_id') for i in out['items']}
    expected=set(supported['_proof']['source_ids']) if supported else set()
    missing=sorted(expected-selected)
    out['validation']['printed_descriptive_pk_coverage']={'version':VERSION,'source_ids':sorted(expected),
        'missing_source_ids':missing,'complete_source_slots':bool(supported),'semantic_approval':False}
    if not supported or missing or any(not i['exact'] for i in out['items']):out['status']='partial' if out['items'] else 'not_found'
    elif not out['validation'].get('missing_conditions') and not out['validation'].get('condition_conflicts'):
        out['status']='answered'
    return True


@lru_cache(maxsize=4096)
def _table_roles(text,product_id):
    if '<table' not in text.casefold():return ()
    from rag.source_selection import catalog
    evidence={'E':{'text':text,'product_id':product_id}};raw=catalog(evidence);graph=build(evidence['E'])
    return tuple(own for c in raw.values() if (own:=fact(c,raw,evidence,graph)))


def retrieval_score(req,chunk):
    """Source role preference only; retrieval metadata is never scope proof."""
    if not req or not req['statistics']:return 0
    fields=set()
    for own in _table_roles(chunk['text'],chunk.get('product_id')):
        if not compatible(req,own):continue
        endpoints={a['value'] for a in own['observations'] if a['dimension']=='endpoint'}
        stats={a['value'] for a in own['observations'] if a['dimension']=='statistic'}&set(req['statistics'])
        fields.update(product(endpoints,stats))
    return len(fields)
