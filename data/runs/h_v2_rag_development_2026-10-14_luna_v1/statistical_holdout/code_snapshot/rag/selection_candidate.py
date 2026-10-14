"""Opt-in source selector with literal PK coverage and printed count roles."""
from copy import deepcopy
import json
from rag import source_selection as base
from rag.population_counts import annotate
from rag.literal_requests import compile_requests
from rag.contract_relations import table_fact,prose_fact
from rag.contract_match import match_request
from rag.grounded_table_graph import build
from rag.component_scope import establish
from rag.request_coverage import classify, coverage

VERSION='selection-candidate-28-per-request-source-map'
value_ids=base.value_ids
premise_conflicts=base.premise_conflicts
coverage_correction=base.coverage_correction
merge_coverage_correction=base.merge_coverage_correction


def independent_contract(question,u):
    contract=(u or {}).get('request_contract')
    if contract is None:return None
    import hashlib
    if contract.get('question')!=question or contract.get('question_sha256')!=hashlib.sha256(question.encode()).hexdigest():
        raise ValueError('Question contract does not belong to the original question')
    return contract


def audits(candidates,evidence,question,contract=None,u=None):
    if contract is not None:
        from rag.contract_candidate import inventory
        _,records=inventory(question,{'requirements':contract,'understanding':u},
                            {},evidence,candidates=candidates)
        return contract,{cid:{'checks':a['requests'],'fact':a['fact']} for cid,a in records.items()}
    contract=compile_requests(question)
    if contract is None:return None,{}
    graphs={eid:build(block) for eid,block in evidence.items()}
    result={}
    for cid in base.value_ids(candidates):
        c=candidates[cid]
        fact=table_fact(c,evidence,graph=graphs[c['evidence']])
        if fact is None:fact=prose_fact(c,evidence)
        if fact is None:continue
        requests=deepcopy(contract['requests'])
        for r in requests:
            r['normalization_status']='grounded'
            # Legacy independent scope validation still checks supplied study
            # quotes. Table role coverage must not infer trial attribution.
            r['conditions']=[c for c in r['conditions'] if c['dimension']!='study']
        checks=[match_request(contract,r,fact) for r in requests]
        result[cid]={'checks':checks,'fact':fact}
    return contract,result


def catalog(evidence,question='',*,request_contract=None,understanding=None):
    from rag.contract_candidate import catalog as expanded_catalog
    candidates=annotate(expanded_catalog(question,evidence),evidence,question)
    from rag.requested_quantity import supplement as supplement_quantities
    supplement_quantities(candidates,evidence,question)
    from rag.printed_pk import supplement
    supplement(candidates,evidence,question)
    from rag.source_event_scope import apply
    candidates=apply(candidates,evidence,question)
    contract=compile_requests(question)
    from rag.contract_terms import atoms
    question_roles=atoms(question)
    pk_units=bool(any(a['dimension']=='endpoint' for a in question_roles) and any(a['dimension']=='attribute' and a['value']=='unit' for a in question_roles))
    if contract or pk_units:
        from rag.regulatory_scope import eligible
        from rag.contract_match import condition
        endpoints={a['value'] for a in question_roles if a['dimension']=='endpoint'}
        fields={r['field']['value'] for r in (contract or {}).get('requests',[]) if r['field']['dimension']=='statistic'}
        for c in candidates.values():
            fact=prose_fact(c,evidence)
            if fact and eligible(c,candidates,evidence,question):
                if any(condition(fact,'endpoint','eq',[e])['status']=='match' for e in endpoints) and (
                        any(condition(fact,'statistic','eq',[f])['status']=='match' for f in fields) or
                        not fields and pk_units and any(a['dimension']=='unit' for a in fact['observations'])):
                    c['value_eligible']=True
                    c['value_binding']='whole_contiguous_source_text'
            elif c['kind']=='literal_number' and not any(candidates[i]['kind']=='table_cell' for i in c.get('context_ids',[])):
                # A sentence may mention a dose, duration and outcome. Lexical
                # co-occurrence cannot assign its every number to the endpoint.
                c['value_eligible']=False
                c['selection_exclusion']='prose_scalar_role_not_bound_use_whole_source_text'
    contract,checked=(None,{}) if request_contract is not None else audits(candidates,evidence,question)
    for cid,a in checked.items():
        if a['checks'] and all(c['status']=='mismatch' for c in a['checks']):candidates[cid]['value_eligible']=False
    # A weaker row of the SAME table cannot displace a row that establishes a
    # strict superset of the requested AND conditions for this very request.
    # Unknown is not false: weaker rows remain context, and unrelated tables or
    # trials cannot dominate one another by a score.
    for cid,a in checked.items():
        if not a['fact'].get('table_id'):continue
        viable=[(n,c) for n,c in enumerate(a['checks']) if c['status']!='mismatch' and c['checks'][0]['status']=='match']
        if not viable:continue
        def matched(check):return {n for n,x in enumerate(check['checks']) if x['status']=='match'}
        dominated=[]
        for n,c in viable:
            competitors=[oid for oid,b in checked.items() if oid!=cid and b['fact'].get('table_id')==a['fact']['table_id']
                and candidates[oid]['evidence']==candidates[cid]['evidence'] and candidates[oid].get('value_eligible') is not False
                and b['checks'][n]['status']!='mismatch' and b['checks'][n]['checks'][0]['status']=='match'
                and matched(c)<matched(b['checks'][n])]
            dominated.append(bool(competitors))
        if dominated and all(dominated):
            candidates[cid]['value_eligible']=False;candidates[cid]['selection_exclusion']='weaker_AND_match_in_same_table'
    from rag.study_registry import products,finditer
    targets={k for pid in products() for _,_,k in finditer(question,pid)}
    for c in candidates.values():
        if c['kind']=='literal_number':
            parent=next((candidates[i] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell'),None)
            if parent:c['parent_table_start']=parent['table_start']
        import re
        facet='design' if re.search(r'시험\s*설계|study\s+design',question,re.I) else None
        scopes=[proof for target in targets if (proof:=establish(c,evidence,target,facet))]
        if len(scopes)==1:c['requested_component_scope']=scopes[0]
    from rag.source_context import fact_role,containing_sentence
    requested_roles=set()
    import re
    if re.search(r'투여\s*용량|\bdos(?:e|age|ing)\b',question,re.I):requested_roles.add('dose')
    if re.search(r'투여\s*경로|administration\s+route|route\s+of\s+administration',question,re.I):requested_roles.add('route')
    scoped_roles={fact_role(c,containing_sentence(c,evidence)) for c in candidates.values() if c.get('requested_component_scope') and c.get('value_eligible') is not False}
    for c in candidates.values():
        role=fact_role(c,containing_sentence(c,evidence))
        if role in requested_roles&scoped_roles and not c.get('requested_component_scope'):
            c['value_eligible']=False;c['selection_exclusion']='requested_component_has_stronger_own_source_role'
    from rag.margin_scope import apply as apply_margin
    apply_margin(candidates,question,preserve_other_fields=request_contract is not None)
    from rag.compound_statistics import apply as apply_compound
    apply_compound(candidates,question)
    from rag.printed_pk import apply as apply_printed
    apply_printed(candidates,evidence,question)
    if targets:
        from rag.study_registry import family
        related=set().union(*(family(target) for target in targets))
        for c in candidates.values():
            if c['kind'] not in ('source_paragraph','source_line','literal_term','literal_number'):continue
            parents=[candidates[i] for i in c.get('context_ids',[]) if i in candidates and candidates[i]['kind']=='source_paragraph']
            own=set(c['printed_studies'])|{s for p in parents for s in p['printed_studies']}
            if own and not own&related:
                c['value_eligible']=False;c['selection_exclusion']='different_study_printed_in_own_prose_block'
    from rag.source_fields import apply as apply_fields
    apply_fields(candidates,evidence,question)
    from rag.whole_source_units import apply as apply_units
    apply_units(candidates,evidence,question)
    from rag.known_source_constraints import apply as apply_known
    if request_contract is None:apply_known(candidates,evidence,question)
    from rag.requested_quantity import apply as apply_quantity
    apply_quantity(candidates,evidence,question)
    from rag.source_only_units import apply as apply_whole_default
    apply_whole_default(candidates,evidence,question)
    from rag.local_trial_scope import apply as apply_local_trial
    apply_local_trial(candidates,evidence,question)
    if request_contract is not None:
        _,checked=audits(candidates,evidence,question,request_contract,understanding)
        for cid,a in checked.items():
            c=candidates[cid]
            c['independent_request_audit']={'question_sha256':request_contract['question_sha256'],
                'checks':deepcopy(a['checks']),'semantic_approval':False}
            if classify(a['checks'])['status']=='mismatch':
                c['value_eligible']=False;c['selection_exclusion']='independent_field_contract_mismatch'
    return candidates


def catalog_for_query(evidence,question,u):
    return catalog(evidence,question,request_contract=independent_contract(question,u),understanding=u)


UNCONFIRMED='__unconfirmed__'

def request_options(candidates):
    records=[c['independent_request_audit']['checks'] for c in candidates.values() if c.get('independent_request_audit')]
    if not records:return None
    ids=list(dict.fromkeys(r['request_id'] for record in records for r in record))
    result={rid:{'supported':[],'related':[]} for rid in ids}
    for cid in base.value_ids(candidates):
        for check in candidates[cid].get('independent_request_audit',{}).get('checks',[]):
            state=classify([check])
            if state['supported']:result[check['request_id']]['supported'].append(cid)
            elif state['viable'] and check['checks'] and check['checks'][0]['status']=='match':
                result[check['request_id']]['related'].append(cid)
    return {rid:[*(choices['supported'] or choices['related']),UNCONFIRMED] for rid,choices in result.items()}


def schema(candidates):
    options=request_options(candidates)
    if options is not None:
        from rag.chunk_schema import obj
        return {'name':'source_selection_by_request','schema':obj({'request_sources':obj({
            rid:{'type':'string','enum':choices} for rid,choices in options.items()})})}
    result=base.schema(candidates)
    studies=result['schema']['properties']['items']['items']['properties']['study']['enum']
    studies.extend(sorted({c['requested_component_scope']['target'] for c in candidates.values() if c.get('requested_component_scope')}-set(studies)))
    return result


def prompt(question,u,unit,evidence,candidates=None):
    if candidates is None:candidates=catalog_for_query(evidence,question,u)
    from rag.source_only_units import prompt_view
    view=prompt_view(candidates)
    messages=base.prompt(question,u,unit,evidence,candidates=view)
    messages[0]['content']+='\nUse whole source sentences and cells. A numeric fragment is allowed only for a printed bound role. Your proposed Korean fact labels and conditions are not source proof and will not override original wording. Prefer the smallest complete source statement that preserves the requested relationships. Context-only IDs cannot be answer values.'
    options=request_options(candidates)
    if options is not None:
        messages[0]['content']+='\nFor this contract query return ONLY request_sources from the supplied schema, not an items list. Address EVERY request ID separately. Choose its smallest complete supported source ID; reuse the SAME source ID if one whole statement supplies multiple fields. Choose __unconfirmed__ when the requested field has no usable source. Source conditions and final values are copied and checked in code, never supplied or rewritten by you. Related options retain unresolved conditions.'
        messages[1]['content']+='\nPer-request allowed source IDs (complete local support takes precedence over related scope): '+json.dumps(options,separators=(',',':'))
    messages[1]['content']+='\nAllowed ANSWER value IDs (other visible IDs are literal context only): '+json.dumps(base.value_ids(candidates),separators=(',',':'))
    contract,checked=audits(candidates,evidence,question,independent_contract(question,u),u)
    messages[0]['content']+='\nThe original Korean question controls ALL requested fields. Treat conditions inside each request as AND. Never select a drug-code number as a population count. An explicitly supplied parent heading and the value\'s own single Part label can establish a component; no other parent/nearby value can. Report every supported requested field and explicitly leave missing fields incomplete.'
    messages[0]['content']+='\nPreserve compound source quantities as complete cells or complete source sentences: count with percentage, multiple dose groups, a range and both confidence-interval bounds must not become isolated scalar answers. Printed row and column headings control the field meaning; a generated fact label cannot assert a weight, formulation, route or event absent from its own source. A container/formulation strength is distinct from an administered dose. Keep the complete original preparation/storage or dosing clause with its qualifications.'
    from rag.requested_quantity import hints
    quantities=hints(candidates)
    if quantities:messages[1]['content']+='\nWhole source quantity choices (source evidence only; satisfy every requested arm/stratum with its own cell, or use one complete sentence containing all requested quantities): '+json.dumps(quantities,ensure_ascii=False,separators=(',',':'))
    if contract:
        obligations=[{'id':r['id'],'field':[r['field']['dimension'],r['field']['value']],
            'AND':[[c['dimension'],c['operator'],[v['value'] for v in c['values']]] for c in r['conditions']]} for r in contract['requests']]
        choices=[{'source_id':cid,'requests':[{'id':c['request_id'],
            'matched':sorted({x['dimension'] for x in c['checks'] if x['status']=='match'}),
            'unresolved':sorted({x['dimension'] for x in c['checks'] if x['status']=='unknown'})}
            for c in a['checks'] if c['status']!='mismatch' and c['checks'][0]['status']=='match']}
            for cid,a in checked.items() if any(c['status']!='mismatch' and c['checks'][0]['status']=='match' for c in a['checks'])]
        messages[1]['content']+='\nLiteral QUESTION obligations (subset, no document answers): '+json.dumps(obligations,ensure_ascii=False,separators=(',',':'))
        messages[1]['content']+='\nCompatible own-grid roles (prefer a candidate with more original AND conditions actually matched; never treat unresolved as matched): '+json.dumps(choices,ensure_ascii=False,separators=(',',':'))
    roles=[{'source_id':cid,**c['population_count_role']} for cid,c in candidates.items() if c.get('population_count_role')]
    if roles:messages[1]['content']+='\nPrinted PK population header counts, one item per treatment: '+json.dumps(roles,ensure_ascii=False,separators=(',',':'))
    scopes=[{'source_id':cid,**c['requested_component_scope']} for cid,c in candidates.items() if c.get('requested_component_scope') and c.get('value_eligible') is not False]
    if scopes:messages[1]['content']+='\nExplicit local component links: '+json.dumps(scopes,ensure_ascii=False,separators=(',',':'))
    from rag.local_trial_scope import hints as local_trial_hints
    local_trials=local_trial_hints(candidates,question)
    if local_trials:messages[1]['content']+='\nOriginal QUESTION local trial scope (each named trial needs its own source; a different table caption is a conflict, not an answer): '+json.dumps(local_trials,ensure_ascii=False,separators=(',',':'))
    return messages


def prepare_prompt(question,u,unit,evidence):
    """Use one identical catalog for the prompt and its source-ID schema."""
    candidates=catalog_for_query(evidence,question,u)
    return prompt(question,u,unit,evidence,candidates=candidates),schema(candidates)


def context_score(question,chunk):
    """Use the same bounded own-role signal before allocating context tokens."""
    from rag.request_role_retrieval import coverage_score
    return coverage_score(compile_requests(question),chunk)


def cues(query,chunk,studies=(),topic=None):
    """Retrieval preference for literal parent context; never proof of a part."""
    from rag.clinical_retrieval import cues as legacy_cues
    from rag.study_registry import load
    from rag.population_counts import requested,printed_cues
    registry=load();parents=[registry[s]['parent'] for s in studies if s in registry and registry[s].get('parent')]
    found=legacy_cues(query,chunk,[*studies,*parents],topic)
    from rag.source_event_scope import cues as event_cues
    found.extend(event_cues(query,chunk))
    if requested(query):found.extend(printed_cues(chunk))
    return list(dict.fromkeys(found))


def resolve(output,candidates,evidence,question='',u=None,_complete_counts=True):
    assignments=None
    if isinstance(output,dict) and 'request_sources' in output:
        options=request_options(candidates)
        contract=independent_contract(question,u)
        if set(output)!={'request_sources'} or options is None or contract is None or \
                set(output['request_sources'])!=set(options):
            raise ValueError('Source map must address every original request exactly once')
        items={};assignments={};complete=True
        for rid,cid in output['request_sources'].items():
            if cid not in options[rid]:raise ValueError('Source ID is not permitted for this request')
            if cid==UNCONFIRMED:
                assignments[rid]={'source_id':None,'status':'unconfirmed'};complete=False;continue
            checks=candidates[cid]['independent_request_audit']['checks']
            own=next(c for c in checks if c['request_id']==rid)
            state=classify([own]);supported=bool(state['supported'])
            assignments[rid]={'source_id':cid,'status':'supported' if supported else 'related'}
            complete=complete and supported
            scope=[s for c in own['checks'] if c['dimension']=='study' and c['status']=='match'
                   for s in c.get('source_scope',[]) if s['status']=='explicit_candidate']
            studies={s['study'] for s in scope}
            study=next(iter(studies)) if len(studies)==1 else 'none'
            if cid not in items:
                items[cid]={'study':study,'item':'whole original source','source_label':'원문 인용',
                            'value_id':cid,'exact':supported,'conditions':[]}
            else:items[cid]['exact']=items[cid]['exact'] or supported
        output={'status':'answered' if complete else 'partial' if items else 'not_found',
                'items':list(items.values()),'premise_source_ids':[]}
    result=base.resolve(output,candidates,evidence,question,u)
    if assignments is not None:result['request_source_assignments']=assignments
    graphs={}
    for item in result['items']:
        c=candidates[item['source_selection']['value_id']]
        if c.get('independent_request_audit'):
            item['independent_request_audit']=deepcopy(c['independent_request_audit'])
        from rag.printed_pk import relabel
        relabel(item,c,candidates)
        from rag.margin_scope import relabel as relabel_margin
        relabel_margin(item,c)
        from rag.source_fields import relabel as relabel_fields
        relabel_fields(item,c,candidates,evidence,question)
        from rag.whole_source_units import attach as attach_whole_headers
        attach_whole_headers(item,c,candidates,evidence)
        from rag.requested_quantity import attach as attach_quantity
        attach_quantity(item,c,evidence)
        if c.get('known_original_constraints'):item['known_original_constraints']=deepcopy(c['known_original_constraints'])
        from rag.known_source_constraints import dose_mentions,strength_ranges
        item['numeric_question_role_contract']={'basis':'literal_original_question_only',
            'administered_doses':dose_mentions(question),'strength_ranges':strength_ranges(question),'semantic_approval':False}
        import re
        literal=compile_requests(question)
        ratio_request=literal and any(r['field']['dimension']=='statistic' and r['field']['value'] in ('GMR','CI90') for r in literal['requests'])
        if ratio_request or re.search(r'GMR|geometric.*ratio|기하평균비',question,re.I):
            from rag.contract_relations import attach_comparison
            if c['evidence'] not in graphs:graphs[c['evidence']]=build(evidence[c['evidence']])
            attach_comparison(item,c,evidence,graphs[c['evidence']])
        role=c.get('population_count_role')
        if role:
            for dimension,name in (('arm','arm'),('analysis','analysis')):
                item['conditions'].append({'dimension':dimension,'value':role[name],'quote':role[name+'_quote'],'evidence':role['evidence']})
        proof=c.get('requested_component_scope')
        if proof and item['study'] in (proof['parent'],proof['target'],'none'):
            item['study']=proof['target'];item['source_component_scope']=deepcopy(proof)
            item['conditions'].append({'dimension':'study','value':proof['parent_quote'],'quote':proof['parent_quote'],'evidence':proof['parent_evidence']})
    # Simple count extraction is deterministic. Fill only unique, originally
    # printed treatment/count roles with independently checked study linkage.
    # Conflicting population counts never trigger an automatic completion.
    from rag.population_counts import requested,role_key
    from rag.answer_conditions import attribution_missing
    u=u or {};targets=u.get('studies',[])
    if requested(question) and len(targets)==1 and _complete_counts:
        proposals=[]
        for cid,c in candidates.items():
            if not c.get('population_count_role') or c.get('value_eligible') is False:continue
            raw={'status':'partial','premise_source_ids':[],'items':[{
                'study':targets[0],'item':'PK analysis set count','source_label':c['population_count_role']['arm']+' PK 분석군 인원',
                'value_id':cid,'exact':True,'conditions':[]}]}
            # Qualifiers/proof are attached by this wrapper using an internal
            # switch, never a model-supplied flag or an extra API call.
            proposed=resolve(raw,candidates,evidence,question,u,_complete_counts=False)
            item=proposed['items'][0]
            if not attribution_missing([item],evidence,u,[]):proposals.append(item)
        groups={}
        for item in proposals:
            role=candidates[item['source_selection']['value_id']]['population_count_role']
            groups.setdefault(role_key(role),[]).append(item)
        selected={i['source_selection']['value_id'] for i in result['items']}
        additions=[]
        for own in groups.values():
            if len({i['value'] for i in own})!=1:continue
            if any(i['source_selection']['value_id'] in selected for i in own):continue
            additions.append(own[0])
        result['items'].extend(additions)
        if additions and result['status']=='not_found':result['status']='partial'
        result['typed_count_completion']={'version':VERSION,'added_source_ids':[i['source_selection']['value_id'] for i in additions],
            'basis':'unique_printed_count_role_and_checked_study_link','new_model_calls':0,'conflicting_arms':[a[0] for a,own in groups.items() if len({i['value'] for i in own})>1]}
        result['typed_count_completion']['required_source_roles']=[{'arm':a[0],'source_ids':[i['source_selection']['value_id'] for i in own]} for a,own in groups.items()]
    from rag.source_only_units import attach as attach_source_only, deduplicate
    for item in result['items']:
        attach_source_only(item,candidates[item['source_selection']['value_id']],candidates,evidence,question)
        from rag.local_trial_scope import attach as attach_local_trial
        attach_local_trial(item,candidates[item['source_selection']['value_id']])
    deduplicate(result)
    return result


def preselect(candidates,evidence,question,u):
    # Existing deterministic shortcuts cover bounded legacy subsets. They must
    # not skip an independently requested field in the new contract path.
    if independent_contract(question,u) is not None:
        options=request_options(candidates)
        if options and all(choices==[UNCONFIRMED] for choices in options.values()):
            return ({'request_sources':{rid:UNCONFIRMED for rid in options}},
                    {'basis':'no_verified_requested_field_source','semantic_approval':False})
        return None
    from rag.printed_pk import unique_selections
    result=unique_selections(candidates,evidence,question,u)
    if result is None:
        from rag.literal_source_slots import unique
        result=unique(candidates,evidence,question,u)
    if result is None:
        from rag.source_fields import unique_eligibility
        result=unique_eligibility(candidates,evidence,question,u)
    if result is None:
        from rag.printed_incidence import unique as unique_incidence
        result=unique_incidence(candidates,evidence,question,u)
    if result is None:return None
    result=deepcopy(result);proof=result.pop('_proof')
    return result,proof


def finalize(out,candidates,evidence,question,u):
    _finalize_source_contracts(out,candidates,evidence,question,u)
    _finalize_independent_contract(out,candidates,evidence,question,u)
    from rag.local_trial_scope import finalize as finalize_local_trial
    finalize_local_trial(out,candidates,question)


def _finalize_independent_contract(out,candidates,evidence,question,u):
    contract=independent_contract(question,u)
    if contract is None:return
    _,checked=audits(candidates,evidence,question,contract,u)
    kept=[];rejected=[];selected={}
    for item in out['items']:
        cid=item.get('source_selection',{}).get('value_id')
        checks=checked.get(cid,{}).get('checks',[])
        result=classify(checks)
        if result['status']=='mismatch':
            rejected.append({'item':item,'checks':checks});continue
        selected[cid]=checks
        item['independent_request_audit']={'question_sha256':contract['question_sha256'],
            'checks':deepcopy(checks),'semantic_approval':False}
        if result['status']!='supported':
            item['exact']=False
            item['unresolved_request_ids']=[c['request_id'] for c in result['viable']]
        kept.append(item)
    proof=coverage(contract,selected)
    proof.update(contract_status=contract['status'],requests=deepcopy(contract['requests']),
                 missing_known_mentions=deepcopy(contract.get('missing_known_mentions',[])),
                 rejected=rejected)
    out['items']=kept
    out['validation']['independent_question_coverage']=proof
    # Extra related quotations are not proof, but they cannot erase independent
    # complete support for every required field. Keep their exact=False display.
    unresolved=proof['missing'] or rejected or contract['status']!='anchored_candidate'
    if unresolved:
        out['status']='partial' if kept else 'not_found'
    from rag.answer_conditions import label
    out['answer_ko']=('원질문의 요청 항목·조건 중 근거가 확정되지 않은 부분이 있습니다.\n' if unresolved else '')+'\n'.join(
        f'{label(i)}'+(' (조건 미확정)' if not i['exact'] else '')+f': {i["value"]} [{", ".join(i["evidence"])}]' for i in kept)


def _finalize_source_contracts(out,candidates,evidence,question,u):
    from rag.source_fields import finalize as finalize_fields
    finalize_fields(out,candidates,question,evidence,u)
    from rag.source_event_scope import finalize as finalize_event
    finalize_event(out,candidates,question)
    from rag.known_source_constraints import finalize as finalize_known
    finalize_known(out)
    if any(i.get('removed_unbound_event_qualifiers') for i in out['items']):
        from rag.answer_conditions import label
        out['answer_ko']='\n'.join(f'{label(i)}'+(' (관련 값)' if not i['exact'] else '')+f': {i["value"]} [{", ".join(i["evidence"])}]' for i in out['items'])
    from rag.margin_scope import finalize as finalize_margin
    finalize_margin(out,candidates,question)
    verified={cid for r in out.get('typed_count_completion',{}).get('required_source_roles',[]) for cid in r['source_ids']}
    roles=[(cid,c['population_count_role']) for cid,c in candidates.items() if cid in verified and c.get('population_count_role') and c.get('value_eligible') is not False]
    if roles:
        selected={i.get('source_selection',{}).get('value_id') for i in out['items']}
        by_arm={}
        from rag.population_counts import role_key
        for cid,role in roles:by_arm.setdefault(role_key(role)[0],set()).add(cid)
        missing_arms=[a for a,ids in by_arm.items() if not ids&selected]
        out['validation']['printed_population_count_coverage']={'required_arms':sorted(by_arm),'missing_arms':missing_arms,'basis':'source_count_roles','semantic_approval':False}
        if missing_arms:
            out['status']='partial' if out['items'] else 'not_found'
            out['answer_ko']='확인되지 않은 군별 PK 인원: '+', '.join(missing_arms)+'\n'+out['answer_ko']
    from rag.printed_pk import finalize as finalize_printed
    if finalize_printed(out,candidates,evidence,question,u):
        from rag.answer_conditions import label
        out['answer_ko']='\n'.join(f'{label(i)}'+(' (조건 미확정)' if not i['exact'] else '')+f': {i["value"]} [{", ".join(i["evidence"])}]' for i in out['items'])
        return
    if independent_contract(question,u) is not None:return
    contract,checked=audits(candidates,evidence,question)
    from rag.contract_terms import atoms
    import re
    original=atoms(question)
    pk_question=any(a['dimension']=='endpoint' for a in original)
    incomplete=not contract and any(a['dimension']=='endpoint' for a in original) and any(a['dimension']=='statistic' for a in original)
    extras=[]
    for name,pattern in [('SD',r'\bSD\b|표준\s*편차'),('minimum',r'최솟값|최소값|\bminimum\b'),('maximum',r'최댓값|최대값|\bmaximum\b')]:
        if pk_question and re.search(pattern,question,re.I):extras.append(name)
    if pk_question and re.search(r'범위|\brange\b',question,re.I) and not any(a['value']=='CI90' for a in original):extras.append('range')
    if incomplete or extras:
        out['validation']['unresolved_original_pk_requests']={'contract_missing':bool(incomplete),'uncompiled_roles':extras,'source':'original_question','semantic_approval':False}
        out['status']='partial' if out['items'] else 'not_found'
        for i in out['items']:i['exact']=False
        from rag.answer_conditions import label
        out['answer_ko']='원질문의 요청 항목·조건 중 근거가 확정되지 않은 부분이 있습니다.\n'+'\n'.join(
            f'{label(i)} (조건 미확정): {i["value"]} [{", ".join(i["evidence"])}]' for i in out['items'])
    if not contract:return
    rejected=[];kept=[];unknown=[];selected_checks={}
    for item in out['items']:
        cid=item.get('source_selection',{}).get('value_id');a=checked.get(cid)
        if not a:
            kept.append(item);continue
        classification=classify(a['checks'])
        viable=classification['viable']
        if not viable:
            rejected.append({'item':item,'checks':a['checks']});continue
        matches=classification['supported']
        selected_checks[cid]=a['checks']
        if not matches:
            item['exact']=False
            item['unresolved_literal_conditions']=sorted({x['dimension'] for c in viable for x in c['checks'] if x['status']=='unknown'})
            unknown.append(cid)
        kept.append(item)
    common_coverage=coverage(contract,selected_checks)
    covered=common_coverage['covered'];missing=common_coverage['missing']
    out['items']=kept
    out['validation']['literal_question_coverage']={'version':contract['version'],'requested':len(contract['requests']),
        'covered':sorted(covered),'missing':missing,'requests':contract['requests'],'rejected':rejected,
        'unknown_selected_ids':unknown,'complete_question_interpretation':False,'semantic_approval':False,
        'kernel_version':common_coverage['version'],'source_support':common_coverage['source_support']}
    if missing or rejected or unknown:
        out['status']='partial' if kept else 'not_found'
        from rag.answer_conditions import label
        out['answer_ko']='원질문의 요청 항목·조건 중 근거가 확정되지 않은 부분이 있습니다.\n'+'\n'.join(
            f'{label(i)}'+(' (조건 미확정)' if not i['exact'] else '')+f': {i["value"]} [{", ".join(i["evidence"])}]' for i in kept)
