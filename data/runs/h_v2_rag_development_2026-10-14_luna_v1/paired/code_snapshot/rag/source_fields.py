"""Own field roles and complete conditional values, independent of model labels.

These bounded guards neither assign outcomes by page proximity nor turn an
unknown temporal scope into an overall incidence. Whole source values remain
unchanged; no product, question, page or expected result values occur here.
"""
import re

VERSION='own-source-fields-3'
ADA=r'\bADAs?\b|anti[ -]?drug\s+antibod|binding\s+antibod'
NAB=r'\bNAbs?\b|neutrali[sz]ing\s+antibod'
ELIGIBILITY=r'(?<![A-Za-z])BMI(?![A-Za-z])|body\s+mass\s+index|\bage(?:d)?\b|years?\s+of\s+age|body\s+weight|weigh(?:ing|t)'
DESIGN=r'\bphase\s+[IVX\d]+\b|randomi[sz]ed|(?:double|single)[ -]blind|\d+[ -]arm|parallel[ -]group'


def requirement(question):
    age=bool(re.search(r'연령|나이|\bage\b',question,re.I))
    weight=bool(re.search(r'체중|\bweight\b',question,re.I))
    eligibility=bool(re.search(r'선정\s*기준|적격|BMI|body\s+mass|eligibility|inclusion',question,re.I)
        or age and weight or age and re.search(r'조건|criteria',question,re.I))
    design=bool(re.search(r'시험\s*설계|study\s+design|시험\s*단계|무작위\s*배정\s*여부|군\s*구성',question,re.I))
    analytes={n for n,p in [('ADA',ADA),('NAb',NAB)] if re.search(p,question,re.I)}
    fields={m[1].upper() for m in re.finditer(r'\b(ADA|NAb)s?\s*(?:양성률|발생률|(?:positive\s+)?(?:rate|incidence))',question,re.I)}
    if fields:analytes={'NAb' if a=='NAB' else 'ADA' for a in fields}
    if re.search(r'ADA\s*(?:와|및|and|[/·])\s*NAb\s*(?:양성률|발생률|incidence)',question,re.I):analytes={'ADA','NAb'}
    incidence=bool(analytes and re.search(r'양성률|발생률|incidence|positive.*rate',question,re.I)
        and not re.search(r'분석법|assay|method',question,re.I))
    return {'eligibility':eligibility,'design':design,'analytes':analytes if incidence else set()}


def _cell(candidate,candidates):
    if candidate['kind']=='table_cell':return candidate
    if candidate['kind']=='literal_number':
        return next((candidates[i] for i in candidate.get('context_ids',[]) if candidates[i]['kind']=='table_cell'),None)
    return None


def eligibility_fields(text):
    if re.search(r'\bmean\b|\bmedian\b|P[ -]value|standard\s+deviation|original|proposed|scientific\s+advice',text,re.I):return set()
    number=r'\d+(?:\.\d+)?'
    bounds=number+r'\s*(?:to|[-–−])\s*'+number+r'|[≥≤<>]\s*'+number
    labels=[(name,m) for name,p in [('age',r'\bage(?:d)?\b'),('weight',r'\b(?:body\s+)?weight\b|weighing'),
        ('BMI',r'(?<![A-Za-z])BMI(?![A-Za-z])|body\s+mass\s+index')] for m in re.finditer(p,text,re.I)]
    result=set()
    for name,m in labels:
        ends=[o.start() for n,o in labels if n!=name and o.start()>=m.end()]
        semicolon=text.find(';',m.end())
        if semicolon>=0:ends.append(semicolon)
        end=min(ends,default=min(len(text),m.end()+250))
        if re.search(bounds,text[m.end():end],re.I):result.add(name)
    if re.search(number+r'\s*(?:to|[-–−])\s*'+number+r'\s*years?\s+(?:of\s+age)?',text,re.I):result.add('age')
    return result


def requested_eligibility_fields(question):
    fields={name for name,pattern in [('age',r'연령|나이|\bage\b'),('weight',r'체중|\bweight\b'),('BMI',r'(?<![A-Za-z])BMI(?![A-Za-z])|body\s+mass')]
        if re.search(pattern,question,re.I)}
    if re.search(r'전체|모든|기타|추가|성별|흡연|\b(?:all|other|sex|smoking)\b|제외|아닌|except',question,re.I):return set()
    return fields


def inclusion_scope(candidate,evidence):
    text=evidence[candidate['evidence']]['text']
    headings=list(re.finditer(r'(?im)^\s*(?:Key\s+)?inclusion\s+criteria\s*$',text))
    for h in headings:
        tail=text[h.end():]
        stop=re.search(r'(?im)^\s*(?:Key\s+)?(?:exclusion\s+criteria|[A-Z][a-z]+\s+criteria)\s*$|\[UNSUPPLIED SOURCE GAP\]',tail)
        end=h.end()+(stop.start() if stop else len(tail))
        if h.end()<=candidate['start']<candidate['end']<=end:
            return {'evidence':candidate['evidence'],'heading_quote':h.group(),'heading_range':[h.start(),h.end()],
                'source_range':[h.end(),end],'basis':'printed_closed_inclusion_criteria','semantic_approval':False}
    return None


def apply(candidates,evidence,question):
    req=requirement(question)
    if not any(req.values()):return
    for c in candidates.values():
        cell=_cell(c,candidates)
        if req['eligibility']:
            # Inequalities, compound units and ethnic subgroups belong to the
            # complete value. A substring cannot satisfy a composite condition.
            if c['kind']=='literal_number':
                c['value_eligible']=False;c['selection_exclusion']='conditional_eligibility_requires_complete_source'
            elif c['kind'] in ('source_paragraph','source_line','table_cell'):
                fields=eligibility_fields(c['value'])
                if fields:
                    c['own_field_role']='eligibility';c['eligibility_fields']=sorted(fields)
                    scope=inclusion_scope(c,evidence)
                    if scope:c['closed_inclusion_scope']=scope
                else:
                    c['value_eligible']=False;c['selection_exclusion']='no_own_quantified_eligibility_condition'
            else:c['value_eligible']=False
        if req['design'] and not req['eligibility'] and re.search(ELIGIBILITY,c['value'],re.I) and not re.search(DESIGN,c['value'],re.I):
            c['value_eligible']=False;c['selection_exclusion']='population_eligibility_is_not_study_design'
        if not req['analytes']:continue
        if c['kind']=='literal_number':
            c['value_eligible']=False;c['selection_exclusion']='incidence_requires_whole_cell_or_source_sentence'
            continue
        if cell:
            if cell.get('source_header'):
                c['value_eligible']=False;c['selection_exclusion']='antibody_header_is_context_not_incidence';continue
            if not re.match(r'^\s*[(+−–-]*\s*\d',c['value']):
                c['value_eligible']=False;continue
            own=[candidates[i] for i in cell.get('context_ids',[]) if candidates[i]['kind']=='table_cell']
            fields=[h for h in own if re.search(ADA+'|'+NAB,h['value'],re.I)]
            observed={n for n,p in [('ADA',ADA),('NAb',NAB)] if any(re.search(p,h['value'],re.I) for h in fields)}
            if not observed or observed.isdisjoint(req['analytes']):
                c['value_eligible']=False;c['selection_exclusion']='requested_antibody_analyte_not_bound'
                continue
            if len(observed)>1:
                c['value_eligible']=False;c['selection_exclusion']='ambiguous_antibody_analyte_header'
                continue
            row_text=' '.join(h['value'] for h in own if not h.get('source_header'))
            # Baseline/treatment-boosted incidence is a distinct observation,
            # not an unqualified overall or post-dose incidence. Retain context.
            only_baseline=bool(re.search(r'기저|baseline',question,re.I) and not re.search(r'전\s*기간|전체|포함|any\s*time|overall|including',question,re.I))
            boosted=any(re.search(r'treatment[ -]boosted',h['value'],re.I) for h in own)
            baseline=bool(re.search(r'(?<!post-)(?<!post )(?<!post)\bbaseline\b',row_text,re.I))
            if (baseline and not only_baseline or boosted and not re.search(r'boosted|증폭',question,re.I)):
                c['value_eligible']=False;c['selection_exclusion']='baseline_or_boosted_is_not_requested_incidence'
                continue
            days=set(re.findall(r'\bDay\s*(\d+)\b',question,re.I))
            source_days=set(re.findall(r'\bDay\s*(\d+)\b',' '.join(h['value'] for h in own),re.I))
            if days and source_days and days.isdisjoint(source_days):
                c['value_eligible']=False;c['selection_exclusion']='different_printed_incidence_visit'
                continue
            c['own_field_role']=next(iter(observed))
            c['own_field_source_ids']=[h['id'] for h in fields]
        elif c['kind'] in ('source_paragraph','source_line'):
            observed={n for n,p in [('ADA',ADA),('NAb',NAB)] if re.search(p,c['value'],re.I)}
            if not observed.intersection(req['analytes']) or not re.search(r'\d',c['value']):
                c['value_eligible']=False;c['selection_exclusion']='no_requested_antibody_source_statement'
            else:c['own_field_role']='immunogenicity_statement'


def relabel(item,candidate,candidates,evidence,question):
    role=candidate.get('own_field_role')
    if role:
        item['model_source_label']=item.get('model_source_label',item['source_label'])
        item['source_label']={'eligibility':'원문 적격 조건','immunogenicity_statement':'원문 면역원성 문장',
            'ADA':'ADA 양성률 · 원문 셀','NAb':'NAb 양성률 · 원문 셀'}[role]
        item['source_field_role']={'version':VERSION,'role':role,'semantic_approval':False}
        if role=='immunogenicity_statement':
            # A complete quote can remain useful related evidence without
            # asserting an overall incidence from one printed visit, or from
            # a paragraph mixing distinct antibody outcomes.
            observed={n for n,p in [('ADA',ADA),('NAb',NAB)] if re.search(p,candidate['value'],re.I)}
            timed=bool(re.search(r'\b(?:Day|Week)\s*\d+',candidate['value'],re.I))
            requested_time=bool(re.search(r'\b(?:Day|Week)\s*\d+|\d+\s*(?:일|주)차',question,re.I))
            if len(observed)>1 or timed and not requested_time:
                item['exact']=False;item['source_field_role']['unresolved_temporal_or_analyte_correspondence']=True
        if role in ('ADA','NAb'):
            for cid in candidate.get('own_field_source_ids',[]):
                h=candidates[cid]
                condition={'dimension':'endpoint','value':h['value'],'quote':h['quote'],'evidence':h['evidence']}
                if condition not in item['conditions']:item['conditions'].append(condition)
    if not re.search(r'군별|각\s*군|per[ -]?(?:arm|group)|each\s+(?:arm|group)',question,re.I):return
    cell=_cell(candidate,candidates)
    if not cell:return
    from rag.treatment_identity import printed_arm
    product=evidence[candidate['evidence']].get('product_id')
    own=[candidates[i] for i in cell.get('context_ids',[]) if candidates[i]['kind']=='table_cell' and candidates[i].get('source_header')]
    arms=[(h,printed_arm(h['value'],product)) for h in own]
    arms=[(h,a) for h,a in arms if a]
    if len({a for _,a in arms})!=1:return
    h,_=min(arms,key=lambda p:(p[0].get('col_span',1),-p[0]['row']))
    condition={'dimension':'arm','value':h['value'],'quote':h['quote'],'evidence':h['evidence']}
    if condition not in item['conditions']:item['conditions'].append(condition)


def unique_eligibility(candidates,evidence,question,u):
    if not requirement(question)['eligibility']:return None
    requested=requested_eligibility_fields(question)
    if not requested or len(u.get('studies',[]))!=1:return None
    from rag.source_selection import resolve
    from rag.answer_conditions import attribution_missing
    target=u['studies'][0];valid=[]
    for cid,c in candidates.items():
        if c.get('value_eligible') is False or not c.get('closed_inclusion_scope') or c['kind']=='source_line':continue
        fields=set(c.get('eligibility_fields',[]))&requested
        if not fields:continue
        raw={'status':'partial','items':[{'study':target,'item':'whole eligibility clause','source_label':'원문 적격 조건',
            'value_id':cid,'exact':True,'conditions':[]}]}
        item=resolve(raw,candidates,evidence,question,u)['items'][0]
        if attribution_missing([item],evidence,u,[]):continue
        valid.append((cid,fields,c))
    # All clauses must belong to one actual printed criteria run. Neither
    # trial metadata nor another inclusion section can fill a missing field.
    scopes={(c['evidence'],tuple(c['closed_inclusion_scope']['source_range'])) for _,_,c in valid}
    if len(scopes)!=1 or not requested<=set().union(*(f for _,f,_ in valid)):return None
    selected=[];covered=set()
    for cid,fields,c in sorted(valid,key=lambda x:(len(x[2]['value']),x[2]['start'])):
        if fields-covered:
            selected.append({'study':target,'item':'whole eligibility clause','source_label':'원문 적격 조건','value_id':cid,'exact':True,'conditions':[]})
            covered.update(fields)
        if requested<=covered:break
    return {'status':'answered','items':selected,'premise_source_ids':[],'_proof':{'version':VERSION,
        'basis':'complete_requested_fields_in_one_printed_inclusion_run_and_independent_study_link',
        'source_ids':[i['value_id'] for i in selected],'fields':sorted(covered),'semantic_approval':False}}


def finalize(out,candidates,question,evidence=None,u=None):
    req=requirement(question)
    if req['analytes'] and evidence is not None and re.search(r'군별|각\s*군|per[ -]?(?:arm|group)|each\s+(?:arm|group)',question,re.I):
        from rag.treatment_identity import printed_arm
        from rag.answer_conditions import attribution_missing
        from rag.source_selection import resolve
        from rag.whole_source_units import attach
        roles={}
        for cid,c in candidates.items():
            if c['kind']!='table_cell' or c.get('value_eligible') is False or c.get('own_field_role') not in req['analytes']:continue
            own=[candidates[i] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell']
            arms={printed_arm(h['value'],evidence[c['evidence']].get('product_id')) for h in own if h.get('source_header')}
            arms.discard(None)
            if len(arms)!=1:continue
            target=(u or {}).get('studies',[])
            raw={'status':'partial','items':[{'study':target[0] if len(target)==1 else 'none','item':'printed incidence',
                'source_label':'원문 양성률','value_id':cid,'exact':False,'conditions':[]}],'premise_source_ids':[]}
            probe=resolve(raw,candidates,evidence,question,u or {})['items'][0]
            attach(probe,c,candidates,evidence)
            if target and attribution_missing([probe],evidence,u,[]):continue
            roles.setdefault((c['own_field_role'],next(iter(arms))),set()).add(cid)
        selected={i.get('source_selection',{}).get('value_id') for i in out['items']}
        missing=[{'analyte':a,'arm':arm,'source_ids':sorted(ids)} for (a,arm),ids in roles.items() if not ids&selected]
        out['validation']['own_incidence_arm_coverage']={'version':VERSION,'required_roles':[
            {'analyte':a,'arm':arm,'source_ids':sorted(ids)} for (a,arm),ids in roles.items()],
            'missing':missing,'basis':'own_analyte_header_and_printed_arm_with_independent_trial_link','semantic_approval':False}
        if missing:
            out['status']='partial' if out['items'] else 'not_found'
            out['answer_ko']='확인되지 않은 군별 양성률: '+', '.join(m['analyte']+' / '+m['arm'] for m in missing)+'\n'+out['answer_ko']
    if not req['eligibility']:return
    requested=requested_eligibility_fields(question)
    if not requested:return
    covered=set().union(*(set(candidates.get(i.get('source_selection',{}).get('value_id'),{}).get('eligibility_fields',[])) for i in out['items']))
    missing=requested-covered
    out['validation']['own_eligibility_field_coverage']={'version':VERSION,'requested':sorted(requested),'covered':sorted(covered&requested),'missing':sorted(missing),'semantic_approval':False}
    if missing:out['status']='partial' if out['items'] else 'not_found'
