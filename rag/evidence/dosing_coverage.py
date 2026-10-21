"""Printed weight-qualified regimen slots; never supply or rewrite answer values."""
import re
from rag.evidence.source_context import containing_sentence,fact_role

VERSION='source-dosing-coverage-3'
WEIGHT=re.compile(r'(?:patients?|subjects?) weighing (?:(more than|less than|over|under) )?(\d+(?:\.\d+)?)\s*kg(?: or (less|more))?',re.I)


def slots(candidates,evidence,question):
    if re.search(r'체중\s*(?:구간별|별)|(?:by|per)\s+(?:body\s*)?weight\s*(?:categor|band|group)',question,re.I) and re.search(r'용량|dosage|doses?',question,re.I):
        from rag.evidence.categorical_roles import weight_category,weight_category_key
        from rag.evidence.population_counts import table_caption
        grouped={};iv=bool(re.search(r'정맥|intravenous|\bIV\b',question,re.I))
        for cid,c in candidates.items():
            if c.get('value_eligible') is False or c['kind']!='table_cell' or c.get('source_header') or fact_role(c)!='dose':continue
            categories=[candidates[i] for i in c.get('context_ids',[]) if weight_category(candidates[i]['value'])]
            caption=table_caption(evidence[c['evidence']]['text'],c['table_start'])
            if iv and not (caption and re.search(r'intravenous|\bIV\b',caption['quote'],re.I)):continue
            for category in categories:
                key=(weight_category_key(category['value']),c['value'])
                slot=grouped.setdefault(key,{'population':category['value'],'fact':'dose','value_ids':[],
                    'basis':'printed_weight_category_and_dose_cell_on_own_row'})
                slot['value_ids'].append(cid)
        if grouped:return list(grouped.values())
    thresholds=set(re.findall(r'(\d+(?:\.\d+)?)\s*kg',question,re.I))
    if not thresholds:return []
    bands=set()
    if re.search(r'이하|or less|less than|under',question,re.I):bands.add('lower')
    if re.search(r'초과|more than|over|or more',question,re.I):bands.add('upper')
    if not bands:return []
    wants_dose=bool(re.search(r'용량|dosage|doses?',question,re.I))
    wants_interval=bool(re.search(r'간격|interval|schedule|frequency',question,re.I))
    grouped={}
    for cid,c in candidates.items():
        if c.get('value_eligible') is False or c['kind']!='literal_number':continue
        sentence=containing_sentence(c,evidence);weight=WEIGHT.search(sentence)
        if not weight or weight[2] not in thresholds:continue
        band='upper' if (weight[1] or weight[3] or '').casefold() in ('more than','over','more') else 'lower'
        if band not in bands:continue
        role=fact_role(c);fact=None
        if role=='dose' and wants_dose:fact='dose'
        if role=='dosing_interval' and wants_interval:
            text=evidence[c['evidence']]['text']
            before=text[max(0,c['start']-15):c['start']];after=text[c['end']:c['end']+12]
            if re.search(r'every\s*$',before,re.I):fact='maintenance_interval'
            elif re.match(r'\s*later\b',after,re.I):fact='initial_interval'
        if not fact:continue
        key=(weight[2],band,fact)
        slot=grouped.setdefault(key,{'population':weight[0],'band':band,'threshold_kg':weight[2],
            'fact':fact,'value_ids':[],'basis':'original_weight_regimen_sentence'})
        slot['value_ids'].append(cid)
    return list(grouped.values())


def enforce(out,candidates,evidence,question):
    required=slots(candidates,evidence,question)
    selected={i.get('source_selection',{}).get('value_id') for i in out['items']}
    def displayed_interval(slot):
        if slot['fact'] not in ('initial_interval','maintenance_interval'):return False
        for item in out['items']:
            own=candidates.get(item.get('source_selection',{}).get('value_id'))
            if not own or fact_role(own) not in ('dose','dosing_interval'):continue
            sentence=containing_sentence(own,evidence)
            for cid in slot['value_ids']:
                target=candidates[cid]
                if target['evidence']!=own['evidence'] or containing_sentence(target,evidence)!=sentence:continue
                if any(c['dimension']=='dosing_interval' and c['evidence']==own['evidence']
                       and c['quote']==sentence and c.get('value',c['quote'])==sentence
                       for c in item.get('conditions',[])):return True
        return False
    missing=[{k:s[k] for k in ('population','fact')} for s in required
             if not selected.intersection(s['value_ids']) and not displayed_interval(s)]
    global_fields=[]
    if re.search(r'투여\s*경로|route of administration|administration route',question,re.I):global_fields.append('route')
    if re.search(r'간격|dosing interval|dose interval|schedule|frequency',question,re.I):global_fields.append('dosing_interval')
    for dimension in global_fields:
        if not any(i.get('source_fact_role')==dimension or any(c['dimension']==dimension for c in i.get('conditions',[])) for i in out['items']):
            missing.append({'population':'all_requested','fact':dimension})
    if not required and not global_fields:return
    out['validation']['requested_dosing_coverage']={'version':VERSION,'required_count':len(required)+len(global_fields),
        'covered_count':len(required)+len(global_fields)-len(missing),'missing':missing,'semantic_approval':False,
        'basis':'question_scope_original_numeric_locations_and_displayed_conditions'}
    if missing:
        out['status']='partial' if out['items'] else 'not_found'
        out['answer_ko']='요청한 투여 정보 중 확인되지 않은 항목: '+', '.join(m['population']+' '+m['fact'] for m in missing)+'\n'+out['answer_ko']
