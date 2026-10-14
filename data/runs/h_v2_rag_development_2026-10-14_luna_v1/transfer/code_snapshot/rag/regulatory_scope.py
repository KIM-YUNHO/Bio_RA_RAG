"""Conservative literal scope for requested regulatory events and indications."""
import re

VERSION='literal-regulatory-scope-4'
MARKETING=r'first\s+commercial\s+marketing'
INDICATION=r'\bindicated\s+(?:for|in)\b|\bindications?\s*(?:and usage|:)'

def requests_approval_letter(question):
    return bool(re.search(r'(?:허가|승인)\s*(?:서신|서한)|approval\s+letters?',question,re.I))

def approval_source(block):
    return (block.get('document_metadata') or {}).get('family')=='decision_letter' or bool(re.search(r'Approval Letter',block.get('document_type') or '',re.I))

def requests_product_information(question):
    return bool(re.search(r'제품\s*정보|제품\s*(?:모노그래프|설명서)|product\s+(?:information|monograph)|prescribing\s+information|\bSmPC\b',question,re.I))

def product_information_source(block):
    return (block.get('document_metadata') or {}).get('family')=='product_information' or bool(re.search(
        r'product[- ](?:information|monograph)|prescribing[- ]information|\bSmPC\b',block.get('document_type') or '',re.I))

def unfinished_value(value):
    return bool(re.search(r'\b(?:submit|indicated|conducted|administered|received|informing|recommended)\b',value,re.I) and re.search(r'\s(?:a|an|the|with|of|for|using|to)\s*$',value,re.I))

def requests_marketing_notification(question):
    return bool(re.search(r'first\s+commercial\s+(?:marketing|launch|sale)|(?:첫|최초).*상업.*시판',question,re.I|re.S)) and bool(re.search(r'통지|notif|inform',question,re.I))

def eligible(candidate,candidates,evidence,question):
    from rag.source_context import containing_sentence
    if requests_approval_letter(question) and not approval_source(evidence[candidate['evidence']]):return False
    if requests_product_information(question) and not product_information_source(evidence[candidate['evidence']]):return False
    if candidate['kind']=='source_line' and unfinished_value(candidate['value']):return False
    if requests_marketing_notification(question):
        if candidate['kind']=='source_line' and not candidate['value'].rstrip().endswith(('.',':')):return False
        sentence=containing_sentence(candidate,evidence)
        if candidate['kind'] in ('source_paragraph','source_line') and re.search(MARKETING,candidate['value'],re.I) and re.search(r'\bsubmit\b|notif|inform',candidate['value'],re.I):sentence=candidate['value']
        if not re.search(MARKETING,sentence,re.I) or not re.search(r'\bsubmit\b|notif|inform',sentence,re.I):return False
    if re.search(r'indication|적응증',question,re.I) and re.search(r'pediatric|paediatric|소아|children',question,re.I):
        block=evidence[candidate['evidence']]['text']
        if not re.search(INDICATION,block,re.I):return False
        own=' '.join([candidate['quote'],*(candidates[i]['value'] for i in candidate.get('context_ids',[]) if i in candidates)])
        if not re.search(r'pediatric|paediatric|children',own,re.I):return False
        if candidate['kind']=='source_line' and not re.search(r'\bindicated\b',candidate['value'],re.I):return False
    return True

def literal_item_eligible(item,evidence,question):
    """Apply the same requested source scopes to the direct-quotation service path."""
    from rag.source_selection import prose_ranges
    if unfinished_value(item['value']):return False
    event=requests_marketing_notification(question)
    child=bool(re.search(r'indication|적응증',question,re.I) and re.search(r'pediatric|paediatric|소아|children',question,re.I))
    dose=bool(re.search(r'\bdos(?:e|age|ing)\b|용량|투여량',question,re.I)) and not re.search(r'GMR|geometric.*ratio|기하평균비|\b[CT]max\b|\bAUC',question,re.I)
    numeric=dose and bool(re.fullmatch(r'[\d,.]+',item['value'].strip()))
    approval=requests_approval_letter(question)
    product_information=requests_product_information(question)
    if not (event or child or numeric or approval or product_information):return True
    for key in item['evidence']:
        block=evidence[key];text=block['text']
        if approval and not approval_source(block):continue
        if product_information and not product_information_source(block):continue
        if not item['quote']:continue
        if numeric and not re.search(r'(?<![\d.])'+re.escape(item['value'])+r'\s*(?:mg|mcg|µg|ug)\b',item['quote'],re.I):continue
        selected=item.get('source_selection',{}).get('source_range')
        located=False
        if selected and len(item['evidence'])==1 and len(selected)==2:
            a,b=selected
            located=type(a) is int and type(b) is int and 0<=a<b<=len(text) and text[a:b]==item['value']
        value_offset=item['quote'].find(item['value'])
        # Source-ID selections must validate their actual position. Direct
        # quotations can match repeated wording: accept only an occurrence
        # inside the requested printed scope, never the first match by default.
        positions=[(a,b)] if located else [(m.start()+max(0,value_offset),m.start()+max(0,value_offset)+len(item['value']) if value_offset>=0 else m.end())
            for m in re.finditer(re.escape(item['quote']),text)]
        for position,end in positions:
            if not (event or child):return True
            span=next(((a,b) for a,b in prose_ranges(text,0,len(text)) if a<=position and end<=b),None)
            context=text[span[0]:span[1]] if span else item['quote']
            candidate={'evidence':key,'start':position,'end':end,'kind':'literal_number' if value_offset>=0 and re.match(r'\d',item['value']) else 'source_paragraph','value':item['value'],'quote':context,'context_ids':[]}
            if eligible(candidate,{},evidence,question):return True
    return False

def enforce(out,evidence,question):
    rejected=[item for item in out['items'] if not literal_item_eligible(item,evidence,question)]
    out['validation']['requested_source_scope_rejections']=rejected
    if not rejected:return
    out['items']=[item for item in out['items'] if item not in rejected]
    out['status']='partial' if out['items'] else 'not_found'
    from rag.answer_conditions import label
    out['answer_ko']='요청한 사건·허가 대상·용량에 연결되지 않은 원문 값을 제외했습니다.\n'+'\n'.join(
        f"{label(item)}: {item['value']} [{', '.join(item['evidence'])}]" for item in out['items'])
