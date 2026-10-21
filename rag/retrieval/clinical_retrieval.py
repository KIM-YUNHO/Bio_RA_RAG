"""Printed clinical qualifiers supplement broad retrieval, never exclude sources."""
import re

VERSION='clinical-source-cues-6'

def requested_priority_cue(query):
    from rag.retrieval.regulatory_scope import requests_marketing_notification,requests_approval_letter
    if requests_marketing_notification(query):return 'printed_requested_marketing_notification'
    if re.search(r'indication|적응증',query,re.I) and re.search(r'pediatric|paediatric|children|소아',query,re.I):return 'printed_requested_indications'
    if requests_approval_letter(query) and re.search(r'date|날짜|일자',query,re.I):return 'printed_approval_dates'
    if re.search(r'infus|주입|정맥',query,re.I) and re.search(r'minimum|at least|duration|time|최소|시간',query,re.I):return 'printed_infusion_duration'
    return None

def cues(query,chunk,studies=(),topic=None):
    text=chunk['text']+'\n'+(chunk.get('structure',{}).get('caption') or '')
    path=' '.join(chunk.get('structure',{}).get('section_path',[]))
    result=[]
    gmr=bool(re.search(r'GMR|geometric.*ratio|기하평균비',query,re.I))
    requested_pk=re.findall(r'\b([Ct])(?:[\s$_{}\\]|<[^>]+>)*max\b',query,re.I)
    storage=bool(re.search(r'stor(?:e|age)|room temperature|refrigerat|보관|냉장',query,re.I))
    dose=(topic=='dosing' or bool(re.search(r'\bdos(?:e|ing|age)\b|용량|투여량',query,re.I))) and not storage and (
        not gmr and not requested_pk or gmr and topic=='dosing')
    adjusted=bool(re.search(r'ANCOVA|ad[ -]?hoc|post[ -]?hoc|weight.*(?:adjust|covariat)|사후|공변량',query,re.I))
    if studies:
        from rag.retrieval import study_registry as sr
        printed={key for _,_,key in sr.finditer(text,chunk.get('product_id'))}
        anchors=set(chunk.get('structure',{}).get('heading_studies',[]))
        if printed and not printed.intersection(studies):return []
        if not printed and not anchors.intersection(studies):return []
    # Literal qualifiers supplement ranking; they never approve an answer.
    if dose and re.search(r'\badults?\b|성인',query,re.I):
        if re.search(r'\badults?\b',path+' '+text,re.I) and not re.search(r'\b(?:pediatric|paediatric)\b',path+' '+text[:140],re.I):
            if re.search(r'\b\d+(?:\.\d+)?\s*mg\b',text,re.I):
                result.append('printed_requested_adult_dosage')
    if re.search(r'eligib|inclusion|enrol|age|연령|나이|선정',query,re.I) and re.search(
            r'(?:aged?\s*(?:between\s*)?\d+|\d+\s*(?:to|[-–])\s*\d+\s*years|inclusion criteria)',text,re.I):
        result.append('printed_eligibility_clause')
    from rag.retrieval.regulatory_scope import INDICATION
    if re.search(r'indication|적응증',query,re.I) and re.search(INDICATION,text,re.I):
        if not re.search(r'pediatric|paediatric|소아',query,re.I) or re.search(r'pediatric|paediatric',text,re.I):
            result.append('printed_requested_indications')
    if storage and re.search(r'room temperature|refrigerat|storage',path+' '+text,re.I):
        result.append('printed_requested_storage_conditions')
        if re.search(r'pre[ -]?filled.*pens?|프리필드.*펜',query,re.I) and re.search(r'pre[ -]?filled.*pens?',text,re.I):
            result.append('printed_requested_pen_storage')
    from rag.retrieval.regulatory_scope import requests_marketing_notification,MARKETING
    if requests_marketing_notification(query) and re.search(MARKETING,text,re.I) and re.search(r'\bsubmit\b|notif|inform',text,re.I):
        result.append('printed_requested_marketing_notification')
    if re.search(r'equivalence.*(?:margin|bound)|(?:margin|bound).*equivalence|동등성.*(?:범위|한계)|한계',query,re.I) and re.search(
            r'(?:equivalence|bioequivalence).{0,90}(?:margin|bound|limit)|(?:margin|bound|limit).{0,90}(?:equivalence|bioequivalence)',text,re.I|re.S):
        if re.search(r'\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*(?:to|[-–])\s*\d+',text):
            result.append('printed_equivalence_bounds')
    pk_set=r'\bPKS\b|(?:PK|pharmacokinetic)\s+(?:analysis\s+)?set'
    endpoint=r'\bAUC|C(?:[\s$_{}\\]|<[^>]+>)*max|T(?:[\s$_{}\\]|<[^>]+>)*max'
    if (topic=='n_subjects' or re.search(r'analysis set|population|subjects|participants|분석.*(?:대상|집단|세트)|몇 명|인원',query,re.I)) and re.search(
            pk_set,query,re.I) and re.search(pk_set,text,re.I):
        # N/n in an endpoint result table is not a direct population-count table.
        # Keep it in broad retrieval, but do not award the count-specific cue
        # unless the question actually asks for that endpoint's sample size.
        if (re.search(r'(?:number|subjects|population|patients|\bN\b|\bn\b)',text,re.I)
                and chunk.get('structure',{}).get('kind')=='table_candidate'
                and (not re.search(endpoint,text,re.I) or re.search(endpoint,query,re.I))):
            result.append('printed_requested_analysis_set_counts')
    if dose and re.search(r'\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg)\b',text,re.I):
        if re.search(r'administer|received|injection|subcutaneous|\bSC\b|intravenous',text,re.I):
            result.append('printed_dose_and_administration')
            if re.search(r'treatments|dosage|dose regimen',path.split(' / ')[-1],re.I):result.append('intervention_section')
    if gmr or adjusted:
        if adjusted and re.search(r'ANCOVA|covariance',text,re.I) and re.search(r'weight|covariat|ad[ -]?hoc|post[ -]?hoc',text,re.I):
            result.append('printed_requested_adjusted_analysis')
            if chunk.get('structure',{}).get('kind')=='table_candidate' and re.search(endpoint,text,re.I) and re.search(r'ratio|GMR|geometric',text,re.I):
                result.append('printed_adjusted_result_table')
        subgroup=bool(re.search(r'ADA status|by post-dose anti-drug|subgroup|sensitivity analys',text,re.I))
        requested_subgroup=bool(re.search(r'ADA|anti-drug|subgroup|sensitivity|항체|하위집단|민감도',query,re.I))
        if not adjusted and (not subgroup or requested_subgroup) and not re.search(r'ANCOVA|ad[ -]?hoc|post[ -]?hoc|weight.*(?:adjust|covariat)',text,re.I) and re.search(r'primary.*pharmacokinetic|primary.*PK|primary.*analys',text,re.I) and re.search(r'ratio|geometric|GMR',text,re.I):
            result.append('printed_primary_analysis')
    if not gmr and chunk.get('structure',{}).get('kind')=='table_candidate':
        stats=[s for s in ('median','mean') if re.search(r'\b'+s+r'\b',query,re.I)]
        if stats and requested_pk and any(re.search(letter+r'(?:\s|<[^>]*>|[$_{}\\])*max',text,re.I) for letter in requested_pk) and all(re.search(r'\b'+s+r'\b',text,re.I) for s in stats):
            result.append('printed_endpoint_and_requested_statistics')
    if re.search(r'infus|주입|정맥',query,re.I) and re.search(r'minimum|at least|duration|time|최소|시간',query,re.I):
        if re.search(r'administer|infus',text,re.I) and re.search(r'(?:at least|over|minimum)[\s\S]{0,50}\b(?:hours?|minutes?)\b',text,re.I):
            result.append('printed_infusion_duration')
    from rag.retrieval.regulatory_scope import requests_approval_letter,approval_source
    if requests_approval_letter(query) and approval_source(chunk) and re.search(r'date|날짜|일자',query,re.I):
        if re.search(r'Approval Date\s*:',text,re.I) and re.search(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}',text,re.I):
            result.append('printed_approval_dates')
    return result

def rank(query,idx,chunks,studies=(),topic=None):
    """Inspect all already-filtered source chunks so an exact qualifier can enter the pool."""
    from rag.evidence.budgeted_evidence import relevance
    result=[]
    for i in idx:
        found=cues(query,chunks[i],studies,topic)
        if found:result.append((relevance(chunks[i],query)+4*len(found),int(i)))
    return sorted(result,reverse=True)
