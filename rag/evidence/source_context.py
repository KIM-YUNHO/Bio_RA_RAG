"""Literal structural context for selected values; never replace selected values."""
import re

VERSION='literal-source-context-10'


def containing_sentence(candidate,evidence):
    text=evidence[candidate['evidence']]['text'];start,end=candidate['start'],candidate['end']
    from rag.evidence.prose import containing
    sentence=containing(text,start,end)
    return '' if '[UNSUPPLIED SOURCE GAP]' in sentence else sentence


def fact_role(candidate,sentence=None):
    if candidate['kind']=='literal_term' and re.search(r'subcutaneous|intravenous|intramuscular|\bSC\b|\bIV\b',candidate['value'],re.I):return 'route'
    if candidate['kind']=='literal_number' or candidate['kind']=='table_cell' and not candidate.get('source_header'):
        if re.fullmatch(r'\s*\(?\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug)\)?\s*',candidate['value'],re.I):return 'dose'
        if re.fullmatch(r'\s*\d+(?:\.\d+)?\s*(?:weeks?|days?|hours?)\s*',candidate['value'],re.I):
            if sentence is not None and not re.search(r'\bdos(?:e|age|ing)\b|\bgive\b|administer|inject|\btreating\b',sentence,re.I):return None
            return 'dosing_interval'
    return None


def _count_arm(candidate,candidates,question):
    """Identify a requested analysis row and its narrowest printed arm header."""
    if not re.search(r'군별|각\s*군|per[ -]?(?:arm|group)|each\s+(?:arm|group)|by\s+(?:arm|group)',question,re.I):return None
    if not re.search(r'\bPKS\b|(?:PK|pharmacokinetic)\s+(?:analysis\s+)?set',question,re.I):return None
    cell=candidate
    if cell['kind']=='literal_number':
        cell=next((candidates[i] for i in cell.get('context_ids',[]) if candidates[i]['kind']=='table_cell'),None)
    if not cell or cell['kind']!='table_cell' or cell.get('source_header') or candidate.get('value_eligible') is False:return None
    contexts=[candidates[i] for i in cell.get('context_ids',[]) if candidates[i]['kind']=='table_cell']
    analysis=next((c for c in contexts if re.search(r'\bPKS\b|(?:PK|pharmacokinetic)\s+analysis\s+set',c['value'],re.I)),None)
    if not analysis:return None
    headers=[c for c in contexts if c.get('source_header') and c['row']<cell['row'] and
        c['column']<=cell['column']<c['column']+c['col_span'] and
        not re.fullmatch(r'\s*(?:total|overall|all subjects|treatment|arm|group|N|N\s*\(%\)|number of subjects)\s*',c['value'],re.I)]
    if not headers:return None
    header=min(headers,key=lambda c:(-c['row'],c['col_span']))
    # A Total subcolumn must not inherit a broad treatment heading.
    if any(c.get('source_header') and c['row']>=header['row'] and
        re.fullmatch(r'\s*(?:total|overall|all subjects)\s*',c['value'],re.I) for c in contexts):return None
    return header,analysis


def count_arm_slots(candidates,question):
    """Source inventory only: no expected counts or model-written arm labels."""
    slots={}
    for cid,candidate in candidates.items():
        pair=_count_arm(candidate,candidates,question)
        if not pair:continue
        arm,analysis=pair
        key=(' '.join(arm['value'].split()).casefold(),' '.join(analysis['value'].split()).casefold())
        slot=slots.setdefault(key,{'arm':arm['value'],'analysis':analysis['value'],'value_ids':[]})
        slot['value_ids'].append(cid)
    return list(slots.values())


def enforce_arm_coverage(out,candidates,question):
    slots=count_arm_slots(candidates,question)
    if not slots:return
    selected={item.get('source_selection',{}).get('value_id') for item in out['items']}
    missing=[{'arm':s['arm'],'analysis':s['analysis']} for s in slots if not selected.intersection(s['value_ids'])]
    out['validation']['requested_arm_coverage']={'version':VERSION,
        'basis':'requested_analysis_row_and_original_treatment_column_headers',
        'required_count':len(slots),'covered_count':len(slots)-len(missing),'missing':missing,'semantic_approval':False}
    if missing:
        out['status']='partial' if out['items'] else 'not_found'
        out['answer_ko']='요청한 군별 인원 중 확인되지 않은 항목: '+', '.join(s['arm'] for s in missing)+'\n'+out['answer_ko']

def connected(block,context):
    if any(block.get(k)!=context.get(k) for k in ('document_id','version_id')):return False
    if block is context:return True
    table=block.get('structure',{}).get('table_id')
    other=context.get('structure',{}).get('table_id')
    if table and other and table!=other:return False
    section=block.get('structure',{}).get('section_id')
    if section and section==context.get('structure',{}).get('section_id'):return True
    headings={h['heading_location'].get('block_id') for h in block.get('structure',{}).get('heading_anchors',[])}
    ids={s['unit_id'] for s in context.get('source_spans',[])}
    return context.get('retrieval_role')=='source_heading' and bool(headings&ids)


def comparison_conflict(candidate,candidates,question):
    reference=re.search(r'\b((?:EU|US)\s+[A-Za-z][\w-]*)\s*대비',question,re.I)
    if not reference:reference=re.search(r'(?:compared to|versus|against)\s+((?:EU|US)\s+[A-Za-z][\w-]*)',question,re.I)
    if not reference:return None
    ids=list(candidate.get('context_ids',[]))
    if candidate['kind']=='literal_number':ids.extend(i for parent in list(ids) for i in candidates[parent].get('context_ids',[]) if i not in ids)
    contexts=[candidates[i] for i in ids if candidates[i]['kind']=='table_cell']
    ratios=[c for c in contexts if re.search(r'ratio',c['value'],re.I)]
    rows=[c for c in contexts if re.match(r'^(?:EU|US)\s+[A-Za-z]',c['value'],re.I)]
    if not ratios:return None
    printed=re.findall(r'\b(?:EU|US)\s+[A-Za-z][\w-]*',ratios[0]['value'],re.I)
    actual=printed[-1] if '/' in ratios[0]['value'] and printed else rows[0]['value'] if len(rows)==1 else None
    if actual and actual.casefold()!=reference[1].casefold():
        return {'dimension':'comparison','requested':reference[1],'actual_printed':actual,
            'source_quotes':[c['quote'] for c in [*ratios,*rows]],'evidence':candidate['evidence']}
    return None


def comparison_coverage(items,candidates,question,product):
    """Check an explicitly requested pair of comparisons, without supplying values.

    This deliberately recognizes only unambiguous reference/between syntax.
    Coverage comes from the selected cell's original grid headers, never its
    model-written label or a list of expected answers.
    """
    if not product or not re.search(r'GMR|geometric.*ratio|기하평균비',question,re.I):return None
    drug=r'(?:EU|US)\s+[A-Za-z][\w-]*'
    reference=re.search(r'('+drug+r')\s*대비',question,re.I)
    if not reference:reference=re.search(r'(?:compared to|versus|against)\s+('+drug+r')',question,re.I)
    between=re.search(r'('+drug+r')\s*(?:와|과|and)\s*('+drug+r')\s*간',question,re.I)
    if not between:between=re.search(r'between\s+('+drug+r')\s+and\s+('+drug+r')',question,re.I)
    if not reference or not between or not re.search(r'\b'+re.escape(product)+r'\b',question,re.I):return None
    norm=lambda s:' '.join(s.split()).casefold()
    ref=norm(reference[1]);arms=[norm(between[1]),norm(between[2])]
    if ref not in arms or arms[0]==arms[1]:return None
    endpoints=[name for name,pattern in [('AUCinf',r'AUC[_\s]*inf'),('Cmax',r'C[_\s]*max')]
        if re.search(pattern,question,re.I)]
    if not endpoints:return None
    interval=re.search(r'(\d+)\s*%\s*(?:CI|confidence)',question,re.I)
    want_ci=bool(re.search(r'\bCI(?![A-Za-z])|confidence interval|신뢰구간',question,re.I))
    statistics=['GMR']+(['CI'] if want_ci else [])
    pairs=[(norm(product),ref),(next(a for a in arms if a!=ref),ref)]
    required={(a,b,e,s) for a,b in pairs for e in endpoints for s in statistics}
    supplied=set()
    for item in items:
        candidate=candidates.get(item.get('source_selection',{}).get('value_id'))
        if not candidate:continue
        ids=list(candidate.get('context_ids',[]))
        if candidate['kind']=='literal_number':
            ids.extend(i for p in list(ids) for i in candidates[p].get('context_ids',[]) if i not in ids)
        headers=[candidates[i]['value'] for i in ids if candidates[i]['kind']=='table_cell']
        ratio=next((h for h in headers if re.search(r'\bratio\b',h,re.I)),None)
        if not ratio:continue
        arm=r'(?:(?:EU|US)\s+[A-Za-z][\w-]*|[A-Za-z][\w-]*)'
        printed=re.search(r'ratio\s+(?:of\s+)?('+arm+r')\s*/\s*('+arm+r')',ratio,re.I)
        if not printed:continue
        a,b=norm(printed[1]),norm(printed[2])
        if not re.match(r'^(?:eu|us)\s',b):
            rows=[norm(h) for h in headers if re.match(r'^(?:EU|US)\s+[A-Za-z]',h,re.I)]
            if len(rows)!=1 or rows[0].split(' ',1)[1]!=b:continue
            b=rows[0]
        if (a,b) not in pairs:continue
        actual_endpoints=[e for e in endpoints if any(
            e.casefold() in re.sub(r'[\s$_{}\\]','',h).casefold() for h in headers)]
        actual_statistics=[]
        if any(re.fullmatch(r'\s*Estimate\s*',h,re.I) for h in headers):actual_statistics.append('GMR')
        # A separately selected bound does not cover the complete interval.
        if want_ci and candidate['kind']=='table_cell' and not candidate.get('source_header') and any(re.search(r'\bCI\b',h,re.I) and
                (not interval or re.search(r'\b'+interval[1]+r'\s*%',h)) for h in headers):actual_statistics.append('CI')
        supplied.update((a,b,e,s) for e in actual_endpoints for s in actual_statistics)
    row=lambda slot:{'comparison':slot[0]+'/'+slot[1],'endpoint':slot[2],'statistic':slot[3]}
    return {'version':VERSION,'basis':'explicit_question_comparisons_and_original_grid_headers',
        'required_count':len(required),'covered_count':len(required&supplied),
        'missing':[row(slot) for slot in sorted(required-supplied)],'semantic_approval':False}


def enforce_comparison_coverage(out,candidates,question,product):
    coverage=comparison_coverage(out['items'],candidates,question,product)
    if coverage is None:return
    out['validation']['requested_comparison_coverage']=coverage
    if not coverage['missing']:return
    out['status']='partial' if out['items'] else 'not_found'
    missing='; '.join(f"{m['comparison']} {m['endpoint']} {m['statistic']}" for m in coverage['missing'])
    out['answer_ko']='요청한 비교 중 다음 항목은 답변을 확보하지 못했습니다: '+missing+'\n'+out['answer_ko']


def complete(item,candidate,candidates,evidence,question='',u=None):
    block=evidence[candidate['evidence']];u=u or {}
    removed=[c for c in item['conditions'] if not connected(block,evidence[c['evidence']])]
    item['conditions']=[c for c in item['conditions'] if c not in removed]
    added=[]
    def add(dimension,value,quote,key,basis):
        condition={'dimension':dimension,'value':value,'quote':quote,'evidence':key}
        if condition not in item['conditions']:
            item['conditions'].append(condition);added.append({'dimension':dimension,'evidence':key,'basis':basis})
    own=[c for c in candidates.values() if c['evidence']==candidate['evidence']]
    study=item['study']
    if study!='none':
        literal=[c for c in candidates.values() if study in c['printed_studies'] and connected(block,evidence[c['evidence']])]
        if literal:
            source=min(literal,key=lambda c:len(c['value']))
            add('study',source['value'],source['quote'],source['evidence'],'connected_printed_study')
    ids=list(candidate.get('context_ids',[]))
    if candidate['kind']=='literal_number':
        ids.extend(i for parent in list(ids) for i in candidates[parent].get('context_ids',[]) if i not in ids)
    contexts=[candidates[i] for i in ids if candidates[i]['kind']=='table_cell']
    count_pair=_count_arm(candidate,candidates,question)
    for condition in item['conditions']:
        if condition['dimension']=='row_header' and any(c.get('source_header') and c['quote']==condition['quote'] for c in contexts):
            condition['dimension']='column_header'
    if count_pair:
        arm,analysis=count_pair
        add('arm',arm['value'],arm['quote'],arm['evidence'],'original_treatment_column_header')
        add('analysis',analysis['value'],analysis['quote'],analysis['evidence'],'original_analysis_set_row_header')
    ratio_question=bool(re.search(r'GMR|geometric.*ratio|기하평균비',question,re.I))
    count_question=bool(re.search(r'군별|per[ -]?(?:arm|group)|number of subjects|인원',question,re.I))
    product=block.get('product_id')
    from rag.retrieval.study_registry import products
    names=[product or '',*products().get(product,{}).get('names',[])]
    for c in contexts:
        text=c['value']
        from rag.evidence.categorical_roles import weight_category
        if weight_category(text):add('population',text,c['quote'],c['evidence'],'printed_weight_category_on_own_table_row')
        if count_question:
            if c.get('source_header') and (text.casefold() in {n.casefold() for n in names if n}
                    or re.fullmatch(r'(?:EU|US)(?:[ -](?:approved|licensed|sourced))?\s+[A-Za-z][\w -]*|placebo|treatment [A-Z]',text,re.I)):
                add('arm',text,c['quote'],c['evidence'],'original_treatment_column_header')
            if re.search(r'analysis set|\bPKS\b',text,re.I):
                add('analysis',text,c['quote'],c['evidence'],'original_analysis_set_row_header')
        for dimension,pattern in [('endpoint',r'\bAUC|[Ct](?:\s|[$_{}\\])*max|\bt1/2\b|\bCL/F\b|\bVz/F\b'),
            ('statistic',r'\bMedian\b|\bMean\b|\bEstimate\b|\bCI\b|\bRatio\b'),
            ('dose',r'^\s*\d+(?:\.\d+)?\s*(?:mg|µg|mcg)\s*$'),
            ('unit',r'(?:mcg|µg|ng|mg).*(?:mL|/L)|ng·h/mL'),
            ('row_header',r'^(?:EU|US)(?:[ -](?:approved|licensed|sourced))?\s+[A-Za-z]')]:
            # Concentration/exposure units qualify the endpoint, not its ratio.
            if dimension=='unit' and ratio_question:continue
            if dimension=='row_header' and c.get('source_header'):continue
            if re.search(pattern,text,re.I):add(dimension,text,c['quote'],c['evidence'],'original_table_header_spans')
        if not ratio_question and re.search(r'\bday(?:s)?\b|\bhours?\b|\(h\)',text,re.I) and re.search(r'[Ct](?:\s|[$_{}\\])*max|AUC|t1/2',text,re.I):
            add('unit',text,c['quote'],c['evidence'],'printed_endpoint_unit_header')
    if contexts:
        for c in own:
            if c['kind'] not in ('source_paragraph','source_line'):continue
            if c['end']<=candidate.get('table_start',-1) and re.search(r'primary.*(?:pharmacokinetic|PK)|ANCOVA|ad[ -]?hoc',c['value'],re.I):
                add('analysis',c['value'],c['quote'],c['evidence'],'printed_table_caption')
            if re.search(r'\bA\s*=.*\bB\s*=',c['value']):
                add('comparison',c['value'],c['quote'],c['evidence'],'printed_arm_mapping')
    else:
        sentence=containing_sentence(candidate,evidence);role=fact_role(candidate,sentence)
        # A lexical match elsewhere on the page cannot qualify this prose fact.
        # In particular, "concentration" does not establish a ratio comparison.
        stray=[c for c in item['conditions'] if c['dimension']=='comparison' and not (
            c['evidence']==candidate['evidence'] and c['quote'] in sentence and
            re.search(r'\bratio\b|\bversus\b|\bvs\b|\bcompared\s+(?:to|with)\b|\bbetween\b.*\band\b',c['value'],re.I))]
        stray.extend(c for c in item['conditions'] if c['dimension']=='unit' and (
            c['quote'] not in sentence or re.search(r'\b(?:mg|mcg|ng|mL|days?|weeks?|hours?)\b|[°º]\s*C',candidate['value'],re.I)))
        item['conditions']=[c for c in item['conditions'] if c not in stray]
        removed.extend(stray)
        if role:item['source_fact_role']=role
        nonlocal_conditions=[]
        if role and sentence:
            for condition in item['conditions']:
                if condition['evidence']==candidate['evidence'] and condition['dimension'] in ('dose','population','dosing_interval') and (
                        role=='route' and condition['dimension'] in ('population','dosing_interval') or
                        condition['quote'] not in sentence):nonlocal_conditions.append(condition)
            item['conditions']=[c for c in item['conditions'] if c not in nonlocal_conditions]
        if sentence and role!='route':
            if re.search(r'\d+(?:\.\d+)?\s*mg\b',sentence,re.I):add('dose',sentence,sentence,candidate['evidence'],'containing_source_sentence')
            if re.search(r'weighing.*(?:less|more|greater|over)|weighing.*kg',sentence,re.I):
                add('population',sentence,sentence,candidate['evidence'],'containing_weight_qualification')
            if re.search(r'initial|every|weeks? later',sentence,re.I):add('dosing_interval',sentence,sentence,candidate['evidence'],'containing_interval_qualification')
        for c in own:
            if role in ('dose','dosing_interval','route') and c['kind']=='literal_term' and re.search(r'subcutaneous|intravenous|\bSC\b|\bIV\b',c['value'],re.I):
                add('route',c['value'],c['quote'],c['evidence'],'printed_route_in_same_block')
        # A printed primary-analysis qualification can distinguish multiple
        # endpoint/timepoint facts in the same supplied source block. It remains
        # a literal quotation, not an inferred analysis-set assignment.
        primary=[c for c in own if c['kind'] in ('source_paragraph','source_line') and re.search(
            r'\bprimary\b[\s\S]{0,100}\banalys(?:is|es)\b',c['value'],re.I)]
        if primary:
            c=min(primary,key=lambda c:len(c['value']))
            add('analysis',c['value'],c['quote'],c['evidence'],'printed_primary_analysis_in_same_block')
    conflict=comparison_conflict(candidate,candidates,question)
    if conflict:item['source_context_conflicts']=[conflict]
    item['source_context']={'version':VERSION,'added_conditions':added,'removed_unconnected_conditions':removed,
        'removed_nonlocal_conditions':nonlocal_conditions if not contexts else [],
        'semantic_approval':False,'selected_value_unchanged':True}
    return item


def dose_premise_sources(candidates,evidence,question,u):
    """Find a unique contradictory printed administration dose in connected evidence."""
    requested=set(re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*mg(?![A-Za-z])',question,re.I))
    if not requested or not u.get('studies'):return []
    from rag.retrieval.clinical_retrieval import cues
    doses=[]
    for c in candidates.values():
        if c['kind']!='literal_number' or not re.fullmatch(r'\d+(?:\.\d+)?\s*mg',c['value'],re.I):continue
        block=evidence[c['evidence']]
        administration=bool(re.search(r'received|administer|injection',c['quote'],re.I))
        design=bool(re.search(r'single.*\d+(?:\.\d+)?\s*mg.*(?:SC|subcutaneous).*injection',c['quote'],re.I|re.S))
        if not administration or not (cues(question,block,u['studies'],'dosing') or design):continue
        if c['value'].split()[0] not in requested:doses.append(c)
    if len({c['value'] for c in doses})!=1:return []
    for dose in doses:
        trials=[c for c in candidates.values() if set(c['printed_studies'])&set(u['studies']) and connected(evidence[dose['evidence']],evidence[c['evidence']])]
        if trials:return [dose,min(trials,key=lambda c:len(c['value']))]
    return []
