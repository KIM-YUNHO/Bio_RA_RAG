"""Document-local trial identifiers from the original question and own source.

This narrow scope contract does not infer disease, registry identity or outcomes.
No product, expected number, question ID or page controls its behavior.
"""
import re
from copy import deepcopy

VERSION='local-trial-scope-1'
LABEL=re.compile(r'(?<![A-Za-z0-9])([A-Z]{1,8}[-–−]\d{1,6})(?![A-Za-z0-9-])')
GAP='[UNSUPPLIED SOURCE GAP]'
PHASE=r'(?:induction|maintenance|유도|유지)'


def key(value):
    return value.replace('–','-').replace('−','-')


def anchored(text):
    """Only literal trial/study labels, not all hyphenated numeric tokens."""
    from rag.study_registry import products
    product_keys={re.sub(r'[\s-]','',name).casefold() for pid,p in products().items()
                  for name in (pid,*p['names'],*p.get('ko_names',[]))}
    result=[]
    for m in LABEL.finditer(text):
        if key(m[1]).replace('-','').casefold() in product_keys:continue
        before=text[max(0,m.start()-35):m.start()]
        after=text[m.end():m.end()+40]
        head=bool(re.search(r'\b(?:study|trial)s?\s*\(?\s*$',before,re.I))
        tail=bool(re.match(r'\s*(?:'+PHASE+r'\s*)?(?:시험|trials?\b|stud(?:y|ies)\b)',after,re.I))
        phase=bool(re.match(r'\s*'+PHASE+r'\b',after,re.I) and re.search(r'시험|\btrials?\b|\bstud(?:y|ies)\b',text,re.I))
        if head or tail or phase:result.append({'value':key(m[1]),'quote':m[1],'source_range':[m.start(),m.end()]})
    return result


def requested(question):
    anchors=anchored(question)
    if not anchors:return None
    # A list of named trials is a finite scope. Negation, alternatives and
    # comparisons need a fuller request compiler; never flatten them to AND.
    if re.search(r'제외|아니(?:라|고|다)|아닌|말고|대신|또는|혹은|\b(?:not|except|excluding|rather|instead|either|or)\b',question,re.I):return None
    return {'version':VERSION,'identifiers':list(dict.fromkeys(a['value'] for a in anchors)),
            'original_question_anchors':anchors,'original_question':question,
            'complete_question_interpretation':False,'semantic_approval':False}


def own(candidate,candidates,evidence,requirement):
    c=candidate;block=evidence[c['evidence']];text=block['text'];anchors=[]
    if c['kind']=='literal_number':
        parent=next((candidates[i] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell'),None)
        if parent:c=parent
    if c['kind']=='table_cell':
        # Aligned headers may explicitly name a trial. A distant section title
        # or model-supplied condition is never the table's trial scope.
        for h in [c,*[candidates[i] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell']]:
            named=anchored(h['value'])
            named.extend({'value':key(m[1]),'quote':m[1]} for m in LABEL.finditer(h['value']) if key(m[1]) in requirement['identifiers'])
            if named:anchors.append({'quote':h['quote'],'source_range':[h['start'],h['end']],
                'basis':'own_aligned_trial_header','identifiers':sorted({a['value'] for a in named})})
        from rag.population_counts import table_caption
        caption=table_caption(text,c['table_start'])
        if caption and not text[caption['end']:c['table_start']].strip() and GAP not in caption['quote']:
            named=anchored(caption['quote'])
            if named:anchors.append({'quote':caption['quote'],'source_range':[caption['start'],caption['end']],
                'basis':'literal_contiguous_table_caption','identifiers':sorted({a['value'] for a in named})})
    else:
        if c['kind'] in ('literal_number','literal_term'):
            from rag.source_context import containing_sentence
            quote=containing_sentence(c,evidence)
            # Recover the one containing occurrence, not a same-word sentence
            # in another source position.
            start=text.rfind(quote,0,c['end']+len(quote)) if quote else -1
            if start<0 or not start<=c['start']<c['end']<=start+len(quote):quote=''
        else:quote=c['value'];start=c['start']
        if quote and GAP not in quote:
            named=anchored(quote)
            named.extend({'value':key(m[1]),'quote':m[1]} for m in LABEL.finditer(quote) if key(m[1]) in requirement['identifiers'])
            if named:anchors.append({'quote':quote,'source_range':[start,start+len(quote)],
                'basis':'own_literal_source_statement','identifiers':sorted({a['value'] for a in named})})
    present={v for a in anchors for v in a['identifiers']};wanted=set(requirement['identifiers'])
    matched=present&wanted
    # Different identifiers in the same factual unit remain ambiguous.
    status='unknown' if matched and present-wanted else 'match' if matched else 'mismatch' if present else 'unknown'
    return {'version':VERSION,'status':status,'requested_identifiers':requirement['identifiers'],
            'matched_identifiers':sorted(matched) if status=='match' else [],'present_identifiers':sorted(present),
            'evidence':c['evidence'],'anchors':anchors,'semantic_approval':False}


def apply(candidates,evidence,question):
    requirement=requested(question)
    if requirement is None:return
    for c in candidates.values():
        if c.get('value_eligible') is False:continue
        proof=own(c,candidates,evidence,requirement);c['local_trial_scope']=proof
        if proof['status']=='mismatch':
            c['value_eligible']=False;c['selection_exclusion']='different_printed_local_trial'


def attach(item,candidate):
    proof=candidate.get('local_trial_scope')
    if not proof:return
    item['local_trial_scope']=deepcopy(proof)
    caption=next((a for a in proof['anchors'] if a['basis']=='literal_contiguous_table_caption'),None)
    if caption:item['source_trial_caption']={'quote':caption['quote'],'evidence':proof['evidence'],'source_range':caption['source_range']}


def hints(candidates,question):
    req=requested(question)
    if not req:return None
    choices=[{'source_id':cid,'status':p['status'],'identifiers':p['matched_identifiers']}
             for cid,c in candidates.items() if c.get('value_eligible') is not False and (p:=c.get('local_trial_scope')) and p['status']=='match']
    return {'requested_trial_identifiers':req['identifiers'],'own_source_choices':choices[:40],
            'unknown_is_not_verified':True,'complete_question_interpretation':False}


def finalize(out,candidates,question):
    req=requested(question)
    if not req:return
    covered=set();unknown=[];rejected=[];kept=[]
    for item in out['items']:
        p=candidates.get(item.get('source_selection',{}).get('value_id'),{}).get('local_trial_scope',{})
        if p.get('status')=='mismatch':rejected.append(item);continue
        if p.get('status')=='match':covered.update(p['matched_identifiers'])
        else:
            item['exact']=False
            item['unresolved_conditions']=list(dict.fromkeys([*item.get('unresolved_conditions',[]),'local_trial']))
            unknown.append(item.get('source_selection',{}).get('value_id'))
        kept.append(item)
    missing=[v for v in req['identifiers'] if v not in covered];out['items']=kept
    out['validation']['local_trial_coverage']={'version':VERSION,'requested':req['identifiers'],'covered':sorted(covered),
        'missing':missing,'unknown_source_ids':unknown,'rejected':rejected,'complete_question_interpretation':False,'semantic_approval':False}
    if missing or unknown or rejected:
        out['status']='partial' if kept else 'not_found'
        from rag.answer_conditions import label
        out['answer_ko']='원질문에 명시된 시험 범위 중 확인되지 않은 항목: '+', '.join(missing or req['identifiers'])+'\n'+'\n'.join(
            f'{label(i)}'+(' (관련 원문)' if not i['exact'] else '')+f': {i["value"]} [{", ".join(i["evidence"])}]' for i in kept)
