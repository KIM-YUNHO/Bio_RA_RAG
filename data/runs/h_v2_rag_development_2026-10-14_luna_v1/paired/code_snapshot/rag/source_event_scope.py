"""Printed formulation/event scope, independent of translated retrieval queries.

Only literal source context is used. Missing scope is unknown; a different
formulation is a contradiction. This does not certify clinical semantics.
"""
import re
VERSION='literal-source-event-4'
GAP='[UNSUPPLIED SOURCE GAP]'
STORAGE=re.compile(r'\bstor(?:e[ds]?|age|ing)\b|stability|shelf[ -]life|refrigerat|discard|expir|freez|infusion[\s\S]{0,45}complet|administered\s+within',re.I)
PFS=re.compile(r'pre[ -]?filled\s+syringes?|\bPFS\b|individual\s+syringes?',re.I)
OTHER=re.compile(r'pre[ -]?filled\s+pens?|autoinjector|individual\s+vials?|unopened\s+vials?',re.I)
DILUTED=re.compile(r'\bdilut(?:ed|ion)\b|infusion\s+solution',re.I)

def requested(question):
    if re.search(r'금기|contraindications?',question,re.I):return {'kind':'contraindication'}
    if not re.search(r'보관|냉장|실온|폐기|만료|안정성|주입\s*완료|stor(?:age|e)|stability|infusion.*complet',question,re.I):return None
    formulation='diluted_infusion' if re.search(r'희석(?:\s*(?:한|된|후|하여|해서))?\s*(?:(?:정맥|주입)\s*)?(?:주입액|용액|액)|희석\s*후[\s\S]{0,40}주입|diluted|infusion\s+solution',question,re.I) else 'prefilled_syringe' if re.search(r'프리필드\s*시린지|사전\s*충전\s*주사기|pre[ -]?filled\s+syringe|\bPFS\b',question,re.I) else None
    if formulation is None:return None
    fields=[]
    if re.search(r'안정성|화학|물리|stability',question,re.I):fields.append('chemical_stability')
    if re.search(r'미생물|microbiolog|aseptic',question,re.I):fields.append('microbiology')
    if re.search(r'농도|concentration',question,re.I):fields.append('concentration')
    return {'kind':'storage','formulation':formulation,'stability':'chemical_stability' in fields,'fields':fields}

def field_roles(text):
    roles=set()
    if re.search(r'microbiolog|aseptic',text,re.I):roles.add('microbiology')
    if re.search(r'chemical\s+and\s+physical[\s\S]{0,70}stability|(?:in[ -]use\s+)?stability',text,re.I):roles.add('chemical_stability')
    if re.search(r'concentrations?',text,re.I) and re.search(r'\d+(?:\.\d+)?[\s\S]{0,100}(?:mg|mcg|µg)\s*/\s*mL',text,re.I):roles.add('concentration')
    return roles

def local(candidate,candidates,evidence):
    c=candidate;text=evidence[c['evidence']]['text']
    a,b=c['quote_start'],c['quote_end']
    parents=[candidates[i] for i in c.get('context_ids',[]) if i in candidates and candidates[i]['kind']=='source_paragraph']
    if parents:
        p=min(parents,key=lambda p:len(p['value']))
        a,b=p['quote_start'],p['quote_end']
    value=text[a:b]
    if GAP in value:return None
    return {'evidence':c['evidence'],'source_range':[a,b],'quote':value}

def audit(candidate,candidates,evidence,requirement):
    ctx=local(candidate,candidates,evidence)
    if not ctx:return {'status':'unknown','reason':'source_context_not_contiguous'}
    text=ctx['quote'];kind=requirement['kind']
    if kind=='contraindication':
        # A cross-reference to Contraindications inside a warning is not a
        # contraindication statement. Require a literal predicate or heading.
        positive=bool(re.search(r'\bcontraindicated\b',candidate['value'],re.I))
        heading=re.search(r'(?im)^\s*(?:\d+(?:\.\d+)?[.)]?\s*)?[-–]*\s*CONTRAINDICATIONS\s*[-–]*\s*$',text)
        if heading:
            tail=text[heading.end():]
            stop=re.search(r'(?im)^\s*(?:[-–]+|\d+(?:\.\d+)?\s+)?(?:WARNINGS|PRECAUTIONS|ADVERSE|DOSAGE|INTERACTIONS)\b',tail)
            end=heading.end()+(stop.start() if stop else len(tail))
            ca,cb=candidate['start']-ctx['source_range'][0],candidate['end']-ctx['source_range'][0]
            positive=positive or heading.end()<=ca<cb<=end
        warning=bool(re.search(r'(?:have\s+(?:occurred|been\s+reported)|were\s+reported|reported\s+with)[\s\S]{0,180}|(?:see|Contraindications)\s*\(4\)',candidate['value'],re.I))
        status='match' if positive else 'mismatch' if warning else 'unknown'
    else:
        diluted=bool(DILUTED.search(text));pfs=bool(PFS.search(text));other=bool(OTHER.search(text))
        expected=requirement['formulation']
        own=diluted if expected=='diluted_infusion' else pfs
        contrary=(pfs or other) if expected=='diluted_infusion' else (diluted or other)
        required=set(requirement.get('fields',[]));roles=field_roles(candidate['value'])
        predicate=bool(STORAGE.search(text)) or bool(required&roles)
        status='unknown' if own and contrary else 'match' if own and predicate else 'mismatch' if contrary else 'unknown'
        # Different requested fields share the formulation scope but do not
        # all require a chemical-stability predicate. Keep AND facets separate.
        if required and status=='match' and not roles&required:status='unknown'
    return {'version':VERSION,'status':status,'requirement':requirement,'source_context':ctx,'semantic_approval':False}

def apply(candidates,evidence,question):
    requirement=requested(question)
    if requirement is None:return candidates
    from rag.regulatory_scope import eligible
    checked={cid:audit(c,candidates,evidence,requirement) for cid,c in candidates.items()}
    supported=any(a['status']=='match' and c['kind'] in ('source_paragraph','source_line') for cid,c in candidates.items() if (a:=checked[cid]))
    for cid,c in candidates.items():
        a=checked[cid];c['requested_source_event']=a
        if a['status']=='mismatch' or supported and a['status']!='match':
            c['value_eligible']=False;c['selection_exclusion']='requested_source_event_not_established'
        elif a['status']=='match' and eligible(c,candidates,evidence,question):
            # Whole statements preserve temperature/duration, preparation and
            # expiry relations. An isolated number cannot represent this scope.
            c['value_eligible']=c['kind'] in ('source_paragraph','source_line')
            if requirement['kind']=='storage' and not STORAGE.search(c['value']) and not set(requirement.get('fields',[]))&field_roles(c['value']):c['value_eligible']=False
    return candidates

def finalize(out,candidates,question):
    if requested(question) is None:return
    unresolved=[]
    for item in out['items']:
        c=candidates.get(item.get('source_selection',{}).get('value_id'),{})
        proof=c.get('requested_source_event',{})
        item['source_event_scope']=proof
        # A strength line or a methods heading does not qualify a storage or
        # contraindication predicate. Preserve the whole literal statement.
        irrelevant={'statistic','eligibility','dosing_interval','analysis'}
        if not re.search(r'용량|dos(?:e|age)',question,re.I):irrelevant.add('dose')
        if not re.search(r'투여\s*경로|administration\s+route|route\s+of\s+administration',question,re.I):irrelevant.add('route')
        removed=[condition for condition in item.get('conditions',[]) if condition['dimension'] in irrelevant]
        item['conditions']=[condition for condition in item.get('conditions',[]) if condition not in removed]
        if removed:item['removed_unbound_event_qualifiers']=removed
        if proof.get('status')!='match':item['exact']=False;unresolved.append(c.get('id'))
    out['validation']['requested_source_event_coverage']={'version':VERSION,'requirement':requested(question),'unresolved_source_ids':unresolved,'semantic_approval':False}
    if unresolved:out['status']='partial' if out['items'] else 'not_found'

def slots(candidates,evidence,question):
    requirement=requested(question)
    if not requirement or requirement['kind']!='storage':return None
    eligible={cid:c for cid,c in candidates.items() if c.get('value_eligible') is not False
        and c['kind'] in ('source_paragraph','source_line') and audit(c,candidates,evidence,requirement)['status']=='match'}
    result=[]
    def add(name,label,predicate):result.append({'name':'storage_'+name,'label':label,'value_ids':[cid for cid,c in eligible.items() if predicate(c['value'])]})
    if re.search(r'온도|temperature',question,re.I):
        add('temperature','요청한 제형·사건의 보관 온도',lambda t:bool(re.search(r'\d+\s*[°º]\s*C',t,re.I)))
    period=bool(re.search(r'기간|보관\s*시간|period|duration|stability|안정성',question,re.I))
    if period:
        add('period','요청한 제형·사건의 보관/안정성 기간',lambda t:bool(re.search(r'\b\d+(?:\.\d+)?\s*(?:hours?|days?|months?|minutes?|weeks?)\b',t,re.I)) and bool(re.search(r'stor|stability|refrigerat',t,re.I)))
    for field,label in [('chemical_stability','화학·물리적 안정성의 온전한 원문'),('microbiology','미생물학적 사용 지침의 원문'),('concentration','희석 후 농도 범위의 원문')]:
        if field in requirement.get('fields',[]):add(field,label,lambda t,field=field:field in field_roles(t))
    if re.search(r'주입\s*완료|infusion.*(?:complet|deadline)',question,re.I):
        add('infusion_deadline','희석 후 주입 완료 기한',lambda t:bool(re.search(r'(?:infusion|infused|administered)[\s\S]{0,100}(?:complet|within)|completed[\s\S]{0,50}dilution',t,re.I)))
    if re.search(r'동결|freez',question,re.I):add('freeze','요청 제형의 동결 지침',lambda t:bool(re.search(r'freez',t,re.I)))
    if re.search(r'폐기|남은\s*용액|discard|unused',question,re.I):add('disposal','요청 제형의 잔여 용액/폐기 지침',lambda t:bool(re.search(r'discard|unused|do not use',t,re.I)))
    if re.search(r'재보관|다시|돌려|복귀|return',question,re.I):
        add('return','요청 제형의 냉장 재보관 조건',lambda t:bool(re.search(r'(?:return\w*|plac\w*\s+back|put\w*\s+back|back\s+(?:in|to))[\s\S]{0,100}refrigerat',t,re.I)))
    return result

def cues(question,chunk):
    requirement=requested(question)
    if not requirement:return []
    text=chunk['text']
    if requirement['kind']=='contraindication':
        return ['printed_contraindication_statement'] if re.search(r'\bcontraindicated\b',text,re.I) else []
    marker=DILUTED if requirement['formulation']=='diluted_infusion' else PFS
    if marker.search(text) and STORAGE.search(text):
        if requirement.get('fields') and not set(requirement['fields'])&field_roles(text):return []
        return ['printed_requested_formulation_storage']
    return []
