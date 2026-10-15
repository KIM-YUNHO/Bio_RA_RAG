"""Question-derived answer obligations and pointers over already supplied text.

No expected answer, numerical value, evidence annotation or new search is used.
Pointers aid navigation; they neither prove support nor restrict final citations.
"""
import re

from rag.ra_models import request_items
from rag.ra_requirements import conditions, named_products, verified_study_key
from rag import ra_spans
from rag import study_registry

VERSION = 'ra-question-obligations-3-planner-facets'
LABELS = {
    'dose_route':'실제 투여 용량·경로와 요청한 시험 조건',
    'comparator':'대조약 전체 구성과 출처; 승인 상태는 근거가 있을 때만',
    'design':'시험 설계와 대상군·조건',
    'population':'시험 대상군',
    'follow_up':'해당 시험의 추적·채혈 기간 또는 시점; 전체 연구 기간과 구분',
    'eligibility':'질문에 요청한 연령·체중·BMI의 선정 기준과 실제 관찰 분포 구분',
    'table_statistics':'질문에 요청한 각 군의 통계값·통계량 종류·단위와 행/열 연결',
    'dosing_schedule':'요청한 유지 투여의 시작 시점·반복 간격과 용량·경로',
    'statistic_identity':'요청한 통계량의 의미·종류 구분; 요청하지 않은 통계값은 추가하지 않음',
    'stratification':'층화에 사용한 기준과 시점',
    'purpose':'해당 Part의 목적',
    'comparison':'제품별 확인된 시험 조건의 차이와 비교 한계',
    'reference_selection':'대조약 선택 요건과 적용 조건',
    'analytical_bridge':'분석 브리징 요건·필수 여부와 적용 조건',
    'clinical_bridge':'임상 PK/PD 브리징 요건·필수 여부와 적용 조건',
    'assay_targets':'질문에 요청한 PK·면역원성 측정·검출 대상',
    'document_status_scope':'설명에 사용한 지침의 이름·저장된 상태·날짜·적용 범위',
    'request':'해당 원질문 요청',
}
PATTERNS = {
    'dose_route':r'\d\s*mg\b|subcutaneous|intravenous',
    'comparator':r'(?:EU|US)[- ](?:approved|licensed|Stelara)|ustekinumab\s*\((?:EU|US)\)',
    'design':r'randomi[sz]ed|double.blind|parallel.group|single.dose',
    'population':r'healthy|subjects|patients',
    'follow_up':r'followed|follow.up|sampling period|\d[ -]*(?:days?|weeks?)',
    'eligibility':r'eligib|inclusion criteria|at screening|BMI|body weight',
    'table_statistics':r'Cmax|Tmax|t1/2|AUC|\bMean\b|Median|\bSD\b',
    'dosing_schedule':r'(?:first\s+subcutaneous|maintenance|every\s+(?:\d+|eight|twelve)\s+weeks)',
    'stratification':r'stratifi(?:ed|cation)|balanced weight',
    'purpose':r'objective|to demonstrate|to compare|initial safety|pilot study',
    'reference_selection':r'representative|reference product|non.EEA',
    'analytical_bridge':r'analytical studies|structural and functional|all three',
    'clinical_bridge':r'clinical PK|PD bridging|case.by.case',
    'assay_targets':r'\banalytes?\b|assay|concentration|anti.drug|\bADA\b|\bNAb\b|neutraliz|neutralis',
    'document_status_scope':r'coming into effect|consultation|adoption|\bscope\b|outlines|addresses|lays down',
}


CLINICAL={'dose_route','design','population','follow_up','eligibility','dosing_schedule','table_statistics',
          'statistic_identity','stratification','purpose','assay_targets'}


def planned(tasks):
    """True when every task carries the planner's answer facets (plans saved before them do not)."""
    return bool(tasks) and all('answer_facets' in t for t in tasks)


def slots_enabled():
    """Requirement slots (one claim per slot) are the rollback mode; the default is a checklist."""
    import os
    return os.environ.get('RA_REQUIREMENT_MODE', 'checklist') == 'slots'


def slot_rows(question, tasks=None):
    """Rows that become answer slots; empty unless RA_REQUIREMENT_MODE=slots."""
    return build(question, tasks) if slots_enabled() else []


def checklist(question, tasks):
    """What the planner read the question as asking, per product and item.

    Given to generation as a reminder and never as answer slots: the claim count stays
    free and the original question still governs, so a facet the planner missed can
    still be answered.
    """
    rows=from_plan(question,tasks) if planned(tasks) else legacy(question)
    return [{'product':r['product'],'need':r['label'],**({'item':r['item']} if r.get('item') else {})}
            for r in rows if r['facet']!='request']


def build(question, tasks=None):
    """Requirement rows: from the planner's answer facets, or legacy literal patterns for old plans."""
    rows=from_plan(question,tasks) if planned(tasks) else legacy(question)
    return admitted(rows,named_products(question))


def admitted(rows, all_products):
    from rag.ra_models import MAX_PROPOSED_CLAIMS
    # A clinical question using UI-selected products without a literal name
    # keeps the original path, whose schema admits those source products.
    if not all_products and any(r['facet'] in CLINICAL for r in rows):return []
    # Never truncate requirements beyond the bounded structured envelope.
    specialized=len(rows)==1 and rows[0]['facet'] in ('assay_targets','table_statistics')
    return rows if (2<=len(rows)<=MAX_PROPOSED_CLAIMS or specialized) and any(r['facet']!='request' for r in rows) else []


def row(rows, rid, product, facet, part, question, item=None):
    rows.append({'id':f'{rid}_F{len(rows)+1}','request_id':rid,'product':product,
        'facet':facet,'part':part if facet=='purpose' else None,
        **({'endpoint':part} if facet=='table_statistics' else {}),
        **({'item':item} if item else {}),
        **({'context_pattern':r'Crohn|크론'} if facet=='dosing_schedule' and re.search(r'Crohn|크론',question,re.I) else {}),
        'label':LABELS[facet]+(f' (Part {part})' if facet=='purpose' and part else
                               f' ({part})' if facet=='table_statistics' and part else '')+
                (f' — {item}' if item else '')})


def request_of(task, items):
    """The original request item a task answers: the item holding its verified quote."""
    from rag.ra_intent import _overlap
    quote=(task.get('intent') or {}).get('request_quote','')
    if quote.strip() and (task.get('intent_check') or {}).get('quote_verified', True):
        for rid,text in items.items():
            if quote in text or text in quote:return rid
        for rid,text in items.items():
            if _overlap(quote,text):return rid
    return None


def covers(task, text):
    """A task covers an original sentence its quote or self-contained request restates."""
    from rag.ra_intent import _overlap
    quote=(task.get('intent') or {}).get('request_quote','')
    return any(part.strip() and (text in part or part in text or _overlap(part,text))
               for part in (quote, task.get('request','')))


def literal_detail(question, facet, detail):
    """An endpoint or Part named by the planner counts only when the question prints it."""
    detail=(detail or '').strip()
    if facet=='purpose':
        found=re.findall(r'(?<![A-Za-z0-9])Part\s*(\d+)(?![A-Za-z0-9])',question,re.I)
        return detail if detail in found else None
    if facet=='table_statistics' and detail:
        return detail if re.search(r'(?<![A-Za-z0-9])'+re.escape(detail)+r'(?![A-Za-z0-9])',question,re.I) else None
    return None


def literal_items(question, items):
    """Planner items printed in the question; fewer than two need no separate rows."""
    from rag.ra_models import name_in_question
    found=tuple(dict.fromkeys(i.strip() for i in items or [] if name_in_question(question,i)))
    return found if len(found)>=2 else ()


def uncovered_sentences(question, tasks):
    """Original sentences no task quotes or restates (a single-task plan answers every sentence)."""
    if len(tasks) <= 1:
        return []
    items=request_items(question)
    covered={request_of(t,items) for t in tasks}
    return [text for rid,text in items.items()
            if rid not in covered and not any(covers(t,text) for t in tasks) and not answer_constraint_only(text)]


def from_plan(question, tasks):
    """Rows from each task's answer facets; product names are checked against the registry."""
    rows=[]
    items=request_items(question)
    all_products=named_products(question)
    covered=set()
    for task in tasks:
        rid=request_of(task,items) or next(iter(items))
        covered.add(rid)
        quote=(task.get('intent') or {}).get('request_quote','')
        products=named_products(quote) or named_products(task.get('request','')) or all_products
        facets=[]
        for f in task['answer_facets']:
            facet=f['facet']
            if facet=='calculation':continue
            if facet=='comparator' and not products:facet='reference_selection'
            facets.append((facet,literal_detail(question,facet,f.get('detail')),literal_items(question,f.get('items'))))
        facets=list(dict.fromkeys(facets)) or [('request',None,())]
        for product in products or [None]:
            for facet,part,separate in facets:
                # One requirement (one claim) per item the question asks to answer separately.
                for item in separate or [None]:
                    row(rows,rid,product,facet,part,question,item)
        # A comparison row accompanies requested content facets; alone it is an open request.
        if (len(products)>1 and (task.get('intent') or {}).get('intent')=='compare_products'
                and any(f!='request' for f,_,_ in facets)):
            rows.append({'id':f'{rid}_F{len(rows)+1}','request_id':rid,'product':None,
                'facet':'comparison','part':None,'label':LABELS['comparison']})
    # An original sentence no task quotes or restates stays a requirement of its own
    # (a single-task plan answers every sentence of the question).
    for rid,text in items.items():
        if (len(tasks)>1 and rid not in covered and not any(covers(t,text) for t in tasks)
                and not answer_constraint_only(text)):
            row(rows,rid,None,'request',None,question)
    return rows


def legacy(question):
    rows=[]
    all_products=named_products(question)
    for rid,text in request_items(question).items():
        if answer_constraint_only(text):continue
        products=named_products(text) or all_products
        wanted=conditions(text)['facets']
        facets=[]
        if 'dose' in wanted or 'route' in wanted: facets.append(('dose_route',None))
        if 'comparator' in wanted and 'table_statistics' not in wanted:
            facets.append(('comparator' if products else 'reference_selection',None))
        if 'design' in wanted or re.search(r'시험\s*조건|trial conditions',text,re.I): facets.append(('design',None))
        for facet in ('follow_up', 'eligibility', 'dosing_schedule'):
            if facet in wanted: facets.append((facet,None))
        endpoints = list(dict.fromkeys(m[0] for m in re.finditer(r'Cmax|Tmax|t1/2|AUC(?:inf|last)?',text,re.I)))
        if 'table_statistics' in wanted:
            if not endpoints and re.search(r'구분|차이|distinguish|different',text,re.I):
                facets.append(('statistic_identity',None))
            else:
                facets.extend(('table_statistics', endpoint) for endpoint in endpoints or [None])
        if re.search(r'대상군|대상자|study population|target population',text,re.I): facets.append(('population',None))
        if re.search(r'층화|stratifi',text,re.I): facets.append(('stratification',None))
        parts=list(dict.fromkeys(re.findall(r'(?<![A-Za-z0-9])Part\s*(\d+)(?![A-Za-z0-9])',text,re.I)))
        if parts and re.search(r'목적|purpose|objective',text,re.I): facets.extend(('purpose',p) for p in parts)
        if 'bridging' in wanted: facets.extend([('analytical_bridge',None),('clinical_bridge',None)])
        if re.search(r'(?<![A-Za-z])analytes?(?![A-Za-z])|측정.{0,15}대상|검출.{0,15}대상',text,re.I): facets.append(('assay_targets',None))
        if {'document_status','applicability'} & set(wanted): facets.append(('document_status_scope',None))
        facets=facets or [('request',None)]
        for product in products or [None]:
            for facet,part in facets:
                row(rows,rid,product,facet,part,question)
        if len(products)>1 and re.search(r'차이|differences',text,re.I):
            rows.append({'id':f'{rid}_F{len(rows)+1}','request_id':rid,'product':None,
                'facet':'comparison','part':None,'label':LABELS['comparison']})
    return rows


def pointers(question,evidence,rows):
    spans,_=ra_spans.catalog(evidence)
    healthy_pk=bool(re.search(r'건강인|건강한|healthy',question,re.I) and re.search(r'\bPK\b|pharmacokinetic',question,re.I))
    result={}
    for row in rows:
        pattern=PATTERNS.get(row['facet'])
        if row['facet']=='table_statistics' and row.get('endpoint'):
            pattern=re.escape(row['endpoint'])
        if not pattern: continue
        ranked=[]
        identifier_text=re.sub(r'(?<=[A-Za-z0-9])(?=[가-힣])',' ',question)
        requested_studies={study_registry.top(k) for _,_,k in study_registry.finditer(identifier_text,row['product'])} if row['product'] else set()
        for alias,span in spans.items():
            block=evidence[span['evidence']]
            if row['product'] is not None and block.get('product_id')!=row['product']:continue
            if row['product'] is None and row['facet']!='comparison' and block.get('product_id') is not None:continue
            study=verified_study_key(block)
            if requested_studies and study and study_registry.top(study[-1]) not in requested_studies:continue
            if not re.search(pattern,span['quote'],re.I):continue
            if row['facet']=='dosing_schedule' and not schedule_candidate(span['quote'],row.get('context_pattern')):continue
            # A heading locates a body paragraph but does not replace it.
            from rag.ra_answer import label_only_quote
            if label_only_quote(span['quote']):continue
            if row['part'] and not re.search(r'(?<![A-Za-z0-9])Part\s*'+re.escape(row['part'])+r'(?![A-Za-z0-9])',block['text'],re.I):continue
            value=1
            if row['facet']=='table_statistics' and '<table' in block['text'].lower():
                value+=6
                if re.search(r'산술평균|표준편차',question) and re.search(r'>Mean<|>SD<',block['text'],re.I):value+=6
                if re.search(r'중앙값',question) and re.search(r'Median',block['text'],re.I):value+=4
            # Prefer a complete requested relation over a short paragraph that
            # merely mentions one of its words. This remains navigation only.
            value += min(4, len({m[0].casefold() for m in re.finditer(pattern, span['quote'], re.I)}))
            if row['product'] is not None and verified_study_key(block):value+=2
            if row['facet']=='comparator' and re.search(r'EU.approved.*US.licensed|ustekinumab\s*\(EU\).*ustekinumab\s*\(US\)',span['quote'],re.I|re.S):value+=4
            if row['facet']=='dose_route' and re.search(r'received|administered',span['quote'],re.I):value+=2
            if row['facet']=='assay_targets' and re.search(r'assay|anti.drug|\bADA\b|\bNAb\b',span['quote'],re.I):value+=3
            if row['facet']=='population' and re.search(r'healthy|male|female|patients with|children',span['quote'],re.I):value+=4
            if healthy_pk and row['product'] is not None:
                if re.search(r'healthy',block['text'],re.I):value+=1
                if re.search(r'Phase\s*(?:III|3)|psoriasis patients',span['quote'],re.I):value-=5
            ranked.append((-value,len(span['quote']),alias))
        result[row['id']]=[alias for _,_,alias in sorted(ranked)[:3]]
    return result


def matches(row,claim):
    """Slot facts only, never keywords: the claim is about the slot's product, a comparison
    names two products, and a table-statistics slot states its endpoint with a value.
    Whether the content answers the requirement is the reviewer's semantic judgement."""
    text=claim['text']
    if row['product'] and claim['products']!=[row['product']]:return False
    if row['facet']=='comparison' and len(claim['products'])<2:return False
    if row['facet']=='table_statistics':
        if row.get('endpoint') and row['endpoint'].casefold() not in text.casefold():return False
        from rag.ra_quantities import quantities,numbers,units
        if not quantities(text) and not (numbers(text) and units(text)):return False
    return True


def answer_constraint_only(text):
    """A restriction on interpretation is not a request to prove a new fact."""
    return bool(re.fullmatch(r'.{1,80}(?:로|으로)\s*(?:해석|단정)하지\s*말고\s*'
                            r'적용\s*조건(?:을|만)?\s*(?:써줘|설명해줘|정리해줘)[.!?]?\s*',text) and
                not conditions(text)['facets'])


def schedule_candidate(text, context_pattern=None):
    """An actual temporal regimen, not a generic direction to another section."""
    if context_pattern:
        contexts=list(re.finditer(context_pattern,text,re.I))
        if contexts:text=text[contexts[0].start():]
    return bool(re.search(r'first\s+subcutaneous|maintenance|every',text,re.I) and
                re.search(r'(?:\d+|eight|twelve)\s*weeks?\b',text,re.I))


def final_coverage(rows, claims, proposals, evidence):
    """Expose supported proposed values lost during review; never restore facts."""
    from rag.ra_quantities import numbers, substantive_text
    checked=[]
    for row in rows:
        kept=[c for c in claims if row['id'] in c.get('requirement_ids',[])]
        if row['facet']=='table_statistics':
            from rag.ra_quantities import quantities,numbers,units
            kept=[c for c in kept if (quantities(c['text']) or numbers(c['text']) and units(c['text'])) and
                  (not row.get('endpoint') or row['endpoint'].casefold() in
                   (c['text']+'\n'+'\n'.join(s['quote'] for s in c['sources'])).casefold())]
        proposed=[c for c in proposals if row['id'] in c.get('requirement_ids',[])]
        wanted=set()
        for parent in proposed:
            # Only selected printed values matter. Uncited extra packaging or
            # sample counts are not obligations merely because a draft added them.
            wanted.update(numbers(substantive_text(parent,evidence)) &
                          numbers('\n'.join(s['quote'] for s in parent['sources'])))
        present=set().union(*(numbers(substantive_text(c,evidence)) for c in kept)) if kept else set()
        missing=sorted(wanted-present)
        checked.append({**row,'verified_claim_ids':[c['id'] for c in kept],
            'has_verified_claim':bool(kept),'proposed_supported_value_tokens_missing_from_final':missing,
            'observable_values_preserved':bool(kept) and not missing,
            'full_meaning_verified':False})
    return checked


def reading_cues(evidence,rows):
    """Exact runtime snippets of modality words, never expected-answer prose."""
    spans,_=ra_spans.catalog(evidence)
    patterns={'analytical_bridge':r'will always\s+include.{0,240}?analytical studies',
              'clinical_bridge':r'may also\s+include.{0,240}?clinical PK.{0,180}?products\.'}
    result={}
    for row in rows:
        pattern=patterns.get(row['facet'])
        if not pattern:continue
        hints=[]
        for alias,span in spans.items():
            if evidence[span['evidence']].get('product_id') is not None:continue
            match=re.search(pattern,span['quote'],re.I|re.S)
            if match:hints.append({'span_id':alias,'exact_reading_cue':match[0]})
        if hints:result[row['id']]=hints[:2]
    return result


def omit_unrequested_population_count(claim,row,evidence,request):
    """Weaken only an unsupported, unrequested count before semantic review."""
    from copy import deepcopy
    from rag.ra_quantities import numbers
    if row['facet']!='population' or re.search(r'인원|몇\s*명|대상자\s*수|피험자\s*수|sample size|number of subjects',request,re.I):return claim,[]
    own='\n'.join(s['quote'] for s in claim['sources'])
    unsupported=[m for m in re.finditer(r'\s*(\d+)\s*명',claim['text']) if m[1] not in numbers(own)]
    if not unsupported:return claim,[]
    item=deepcopy(claim)
    from rag.ra_assertions import CONNECTOR
    for connector in CONNECTOR.finditer(claim['text']):
        if (all(m.start(1)>=connector.end() for m in unsupported)
                and re.search(r'등록|모집|참여',claim['text'][connector.end():])):
            item['text']=claim['text'][:connector.start()]+connector[1]+'다.'
            return item,[{'claim_id':claim['id'],'original':claim['text'],'positive':item['text'],
                'withheld_text':claim['text'][connector.end():],'method':'unselected_unrequested_population_count_removed'}]
    for match in reversed(unsupported):
        stop=match.end()+int(item['text'][match.end():].startswith('의'))
        item['text']=item['text'][:match.start()]+item['text'][stop:]
    return item,[{'claim_id':claim['id'],'original':claim['text'],'positive':item['text'],
        'withheld_text':[m[0].strip() for m in unsupported],'method':'unselected_unrequested_population_count_removed'}]
