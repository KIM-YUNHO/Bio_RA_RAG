"""Question obligations checked against printed source positions, never gold values."""
import re
from rag.evidence.prose import ranges,sentences
from rag.retrieval.regulatory_scope import requests_marketing_notification,INDICATION

VERSION='requested-source-facts-3'
RETURN=r'(?:return\w*|plac\w*\s+back|put\w*\s+back|back\s+(?:in|to))[\s\S]{0,100}refrigerat'

def return_clause(text):
    return bool(re.search(RETURN,text,re.I)) and not re.match(r'\s*(?:Discard|Do not use)\b',text,re.I)

def slots(candidates,evidence,question):
    from rag.evidence.source_event_scope import slots as scoped_slots
    scoped=scoped_slots(candidates,evidence,question)
    if scoped is not None:return scoped
    eligible={k:c for k,c in candidates.items() if c.get('value_eligible',True)}
    result=[]
    def add(name,label,predicate):
        result.append({'name':name,'label':label,'value_ids':[k for k,c in eligible.items() if predicate(c)]})
    if requests_marketing_notification(question):
        if re.search(r'기한|deadline|within|몇\s*일',question,re.I):
            add('notification_deadline','요청한 사건 이후 통지 기한',lambda c:bool(re.search(r'\b\d+\s*days?\b',c['value'],re.I)))
        if re.search(r'내용|content|what|information',question,re.I):
            add('notification_content','통지해야 하는 내용',lambda c:bool(re.search(r'date\s+of\s+(?:the\s+)?first\s+commercial\s+marketing',c['value'],re.I)))
    child=bool(re.search(r'indication|적응증',question,re.I) and re.search(r'pediatric|paediatric|소아|children',question,re.I))
    if child:
        if re.search(r'연령|age|years|나이',question,re.I):
            add('pediatric_age','허가된 소아 최저 연령',lambda c:bool(re.search(r'\b\d+\s*years?\b',c['value'],re.I)))
        seen={}
        for key,block in evidence.items():
            text=block['text']
            if not re.search(INDICATION,text,re.I):continue
            for a,b in ranges(text,0,len(text)):
                if not re.search(r'pediatric|paediatric|children',text[a:b],re.I):continue
                if not any(c['evidence']==key and a<=c['start'] and c['end']<=b for c in eligible.values()):continue
                bullets=[(x,y) for x,y in sentences(text,a,b) if text[x:y].lstrip().startswith(('•','●','▪'))]
                for x,y in bullets:
                    while x<y and (text[x].isspace() or text[x] in '•●▪'):x+=1
                    while y>x and text[y-1].isspace():y-=1
                    identity=(block.get('document_id'),block.get('version_id'),' '.join(text[x:y].split()).casefold())
                    matches=[cid for cid,c in eligible.items() if c['evidence']==key and c['start']<=x and y<=c['end']]
                    if identity in seen:
                        seen[identity]['value_ids'].extend(cid for cid in matches if cid not in seen[identity]['value_ids'])
                        continue
                    slot={'name':'pediatric_indication_'+str(len(seen)+1),'label':f'원문 소아 적응증 항목 {len(seen)+1}','value_ids':matches}
                    seen[identity]=slot;result.append(slot)
        if not seen:
            add('pediatric_indication','허가된 소아 적응증',lambda c:c['kind']=='source_paragraph' and bool(re.search(r'indicated',c['value'],re.I)))
    storage=bool(re.search(r'stor(?:age|e)|room temperature|보관|냉장',question,re.I))
    if storage:
        def atomic(c):
            return c['kind']!='source_paragraph' or len(list(sentences(evidence[c['evidence']]['text'],c['start'],c['end'])))==1
        def storage_context(c):
            from rag.evidence.source_context import containing_sentence
            return containing_sentence(c,evidence)
        if re.search(r'온도|temperature',question,re.I):
            add('storage_temperature','실온 보관 최대 온도',lambda c:atomic(c) and bool(re.search(r'\d+\s*[°º]\s*C',c['value'],re.I)) and bool(re.search(r'room temperature|maximum single period',storage_context(c),re.I)))
        if re.search(r'기간|period|duration|days',question,re.I):
            add('storage_period','실온 보관 최대 기간',lambda c:atomic(c) and bool(re.search(r'\b\d+\s*days?\b',c['value'],re.I)) and bool(re.search(r'room temperature|maximum single period',storage_context(c),re.I)))
        if re.search(r'다시|돌려|복귀|return|refrigerat.*condition|냉장.*조건',question,re.I):
            add('storage_return','냉장 복귀의 온전한 조건',lambda c:c['kind']=='source_paragraph' and atomic(c) and return_clause(c['value']))
            # A local numeric duration or wrapped return line can be selected,
            # but only the complete sentence/enriched qualification covers the
            # obligation. Selection eligibility is not coverage certification.
            result[-1]['selectable_value_ids']=[cid for cid,c in eligible.items() if
                return_clause(storage_context(c)) and (
                    c['kind']=='literal_number' and re.fullmatch(r'\d+\s*days?',c['value'],re.I) or
                    c['kind']=='source_line' and return_clause(c['value']))]
        if re.search(r'폐기|discard|disposal|dispose',question,re.I):
            add('storage_disposal','보관 후 폐기 및 만료 기준',lambda c:c['kind']=='source_paragraph' and atomic(c) and bool(re.search(r'\b(?:Discard|Do not use)\b',c['value'],re.I)))
    statistics=[s for s,p in [('mean',r'\bmean\b|평균'),('median',r'\bmedian\b|중앙값')] if re.search(p,question,re.I)]
    endpoint=re.search(r'\b([Ct])[_\s]*max\b',question,re.I)
    doses=list(dict.fromkeys(re.findall(r'(?<![\d.])\d+(?:\.\d+)?\s*mg(?![A-Za-z])',question,re.I)))
    if len(statistics)==2 and endpoint and doses and not re.search(r'GMR|ratio|기하',question,re.I):
        parameter=endpoint[1].lower()+'max'
        def headers(c):
            return [candidates[k]['value'] for k in c.get('context_ids',[]) if k in candidates and candidates[k]['kind']=='table_cell']
        def matches(c,dose,stat):
            own=headers(c)
            return c['kind']=='table_cell' and not c.get('source_header') and any(re.search(re.escape(dose).replace(r'\ ',r'\s*'),h,re.I) for h in own) and any(re.search(r'\b'+stat+r'\b',h,re.I) for h in own) and any(parameter in re.sub(r'[\s$_{}\\]','',h).casefold() for h in own)
        for dose in doses:
            for stat in statistics:add('pk_'+dose+'_'+stat,dose+' '+parameter+' '+stat,lambda c,dose=dose,stat=stat:matches(c,dose,stat))
        if re.search(r'단위|\bunit',question,re.I):
            result.append({'name':'pk_unit','label':'요청한 PK 통계의 원문 단위','value_ids':[k for k,c in candidates.items() if c['kind']=='table_cell' and parameter in re.sub(r'[\s$_{}\\]','',c['value']).casefold() and re.search(r'\b(?:day|days|h|hours|ng|mcg|mL)\b',c['value'],re.I)]})
    return result

def item_range(item,key,evidence):
    if key not in item.get('evidence',[]):return None
    selected=item.get('source_selection',{}).get('source_range')
    if selected:return tuple(selected)
    quote=item.get('quote','');value=item.get('value','');start=evidence[key]['text'].find(quote);offset=quote.find(value)
    if start<0 or offset<0:return None
    return start+offset,start+offset+len(value)

def covers(item,candidate,evidence):
    if item.get('source_selection',{}).get('value_id')==candidate['id']:return True
    span=item_range(item,candidate['evidence'],evidence)
    return bool(span and span[0]<=candidate['start'] and candidate['end']<=span[1])

def restrict(out,candidates,evidence,question):
    obligations=slots(candidates,evidence,question)
    if not obligations or not all(s['name'].startswith('storage_') for s in obligations):return
    supported={cid for s in obligations for cid in [*s['value_ids'],*s.get('selectable_value_ids',[])]}
    rejected=[i for i in out['items'] if not any(covers(i,candidates[cid],evidence) for cid in supported)]
    out['validation']['requested_fact_scope_rejections']=rejected
    if rejected:
        out['items']=[i for i in out['items'] if i not in rejected]
        out['status']='partial' if out['items'] else 'not_found'
        from rag.evidence.answer_conditions import label
        out['answer_ko']='요청한 보관 항목의 원문 위치에 연결되지 않은 값을 제외했습니다.\n'+'\n'.join(
            f"{label(i)}: {i['value']} [{', '.join(i['evidence'])}]" for i in out['items'])

def enrich(out,candidates,evidence,question):
    if not re.search(r'다시|돌려|복귀|return|냉장.*조건',question,re.I):return
    if not re.search(r'보관|냉장|stor|refrigerat',question,re.I):return
    for item in out['items']:
        own=[]
        for c in candidates.values():
            span=item_range(item,c['evidence'],evidence)
            if span:
                from rag.evidence.prose import containing
                if not return_clause(containing(evidence[c['evidence']]['text'],*span)):continue
            if (span and c['kind']=='source_paragraph' and c['start']<=span[0] and span[1]<=c['end']
                    and return_clause(c['value'])):
                own.append(c)
        if own:
            c=min(own,key=lambda c:len(c['value']))
            condition={'dimension':'eligibility','value':c['value'],'quote':c['quote'],'evidence':c['evidence']}
            if condition not in item['conditions']:item['conditions'].append(condition)
            qualifications=[{'evidence':c['evidence'],'start':c['start'],'end':c['end'],'basis':'containing_literal_return_sentence'}]
            # A following discard/expiry sentence can qualify the same return
            # statement. Preserve it separately within the same source parent.
            parents=[p for p in candidates.values() if p['evidence']==c['evidence'] and p['kind']=='source_paragraph' and not p.get('source_sentence') and p['start']<=c['start'] and c['end']<=p['end']]
            if parents:
                parent=min(parents,key=lambda p:len(p['value']))
                following=[p for p in candidates.values() if p.get('source_sentence') and p['evidence']==c['evidence'] and c['end']<=p['start'] and p['end']<=parent['end']]
                if following:
                    after=min(following,key=lambda p:p['start'])
                    if re.match(r'\s*(?:Discard|Do not use)\b',after['value'],re.I) and re.search(r'expir|whichever',after['value'],re.I):
                        extra={'dimension':'eligibility','value':after['value'],'quote':after['quote'],'evidence':after['evidence']}
                        if extra not in item['conditions']:item['conditions'].append(extra)
                        qualifications.append({'evidence':after['evidence'],'start':after['start'],'end':after['end'],'basis':'immediately_following_disposal_sentence_in_same_parent'})
            item['source_storage_context']={'version':VERSION,'qualifications':qualifications,'selected_value_unchanged':True}

def enforce(out,candidates,evidence,question):
    obligations=slots(candidates,evidence,question)
    if not obligations:return
    missing=[]
    for slot in obligations:
        covered=any(covers(item,candidates[cid],evidence) for item in out['items'] for cid in slot['value_ids'])
        # A literal source qualification completes the same selected fact. It
        # cannot turn a different fact or evidence block into the requested one.
        if not covered and slot['name'] in ('storage_return','storage_disposal'):
            covered=any(q['evidence']==candidates[cid]['evidence'] and q['start']<=candidates[cid]['start'] and candidates[cid]['end']<=q['end'] and any(
                c['dimension']=='eligibility' and c['evidence']==q['evidence'] and
                c['quote']==evidence[q['evidence']]['text'][q['start']:q['end']] for c in i.get('conditions',[]))
                for i in out['items'] for q in i.get('source_storage_context',{}).get('qualifications',[]) for cid in slot['value_ids'])
        if not covered and slot['name']=='pk_unit':
            covered=bool(out['items']) and all(any(c['dimension']=='unit' and c['quote']==candidates[cid]['quote'] for c in i.get('conditions',[]) for cid in slot['value_ids']) for i in out['items'])
        if not covered:missing.append({'name':slot['name'],'label':slot['label'],'supported_in_context':bool(slot['value_ids'])})
    out['validation']['requested_fact_coverage']={'version':VERSION,'required_count':len(obligations),'covered_count':len(obligations)-len(missing),
        'missing':missing,'basis':'question_obligations_and_literal_source_ranges','semantic_approval':False}
    if missing:
        out['status']='partial' if out['items'] else 'not_found'
        out['answer_ko']='확보하지 못한 요청 항목: '+', '.join(m['label'] for m in missing)+'\n'+out['answer_ko']
