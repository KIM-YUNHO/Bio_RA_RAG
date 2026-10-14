"""Compile a bounded, unambiguous PK list from the original question only.

LLM translation is a retrieval hint. Cartesian expansion and AND conditions do
not need another model call. Correlated/negated/alternative requests abstain
from this compiler instead of manufacturing a complete interpretation.
"""
from copy import deepcopy
from itertools import product
import re
from rag.question_anchors import catalog
from rag.contract_relations import ranges

VERSION='literal-pk-requests-4-field-scope'


def automatic_roles_complete(question):
    """Conservative grammar boundary for automatic whole-slot extraction.

    The ordinary subset compiler can still guide a model on richer questions.
    Automatic answers require every non-grammatical word to have an original
    role anchor. Unknown predicates, visits or extra fields never disappear.
    """
    from rag.study_registry import finditer,products
    entries=list(catalog(question).values())
    if any(a['dimension']=='attribute' and a['value']!='unit' for a in entries):return False
    # The legacy shortcut has no field-specific visit/analysis audit. New
    # vocabulary may guide the independent contract, but cannot widen this
    # automatic-answer boundary just by masking more words.
    if any(a['dimension'] in ('analysis_role','timepoint') for a in entries):return False
    if any(a['dimension']=='statistic' and a['value'] not in ('GMR','CI90','mean','median') for a in entries):return False
    masked=list(question)
    spans=[(a['start'],a['end']) for a in entries]+[(r['start'],r['end']) for r in ranges(question)]
    spans.extend(a['alias_source_range'] for a in entries if a.get('alias_source_range'))
    spans.extend((a,b) for pid in products() for a,b,_ in finditer(question,pid))
    for start,end in spans:masked[start:end]=' '*(end-start)
    residue=''.join(masked)
    residue=re.sub(r'\b(?:what|are|is|the|and|of|for|in|study|trial|analysis|results?|values?|please|report|show|provide|compared|to|with|versus|against|between|both|references?)\b',' ',residue,flags=re.I)
    residue=re.sub(r'알려\s*주세요|알려\s*줘|정리\s*해\s*주세요|정리\s*해\s*줘|무엇|시험|연구|분석|결과|값|조건|대조약|대비|비교|두|및|에서|으로|와|과|의|은|는|을|를|이|가|간',' ',residue)
    return not re.sub(r'[\s,./·&()\[\]{}?:]+','',residue)


def compile_requests(question):
    entries=list(catalog(question).values())
    def unique(dimension):
        return list({a['value']:a for a in entries if a['dimension']==dimension}.values())
    statistics=unique('statistic');endpoints=unique('endpoint')
    if not statistics or not endpoints:return None
    # This compiler remains the legacy PK subset, not the runtime field
    # interpreter. New endpoint/statistic vocabulary cannot implicitly turn
    # unrelated clinical-result questions into Cartesian PK requests.
    if any(a['value']=='PASI' for a in endpoints):return None
    if any(a['value'] not in ('GMR','CI90','mean','median') for a in statistics):return None
    # Repeated statistic words in different clauses can correlate requirements;
    # known parenthesized synonyms are the only safe repeated field form here.
    clauses=re.split(r';|\n|반면|whereas|respectively|각각',question,flags=re.I)
    if len(clauses)>1 or re.search(r'제외|보정\s*전|아닌|말고|except|exclude|rather than|either|또는|혹은',question,re.I):return None
    if re.search(r'(?:하위|분석)?집단별|각\s*(?:하위|분석)\s*집단|ADA\s*상태별|each\s+subgroup|all\s+subgroups',question,re.I):return None
    stats=[a['value'] for a in entries if a['dimension']=='statistic']
    if any(stats.count(v)>1 and v not in ('GMR',) for v in set(stats)):return None
    def term(a):return {'value':a['value'],'anchor':deepcopy(a['anchor'])}
    def eq(a):return {'dimension':a['dimension'],'operator':'eq','values':[term(a)]}
    shared=[]
    for dimension in ('population','route','analysis_set','covariate','analysis_model','analysis_timing','analysis_role','timepoint'):
        choices=unique(dimension)
        if len(choices)>1 and dimension!='covariate':return None
        shared.extend(eq(a) for a in choices)
    intervals=ranges(question)
    if len(intervals)>1:return None
    for interval in intervals:
        bounds=[a for a in unique('weight') if a['value'] in interval['values']]
        if len(bounds)!=2:return None
        shared.append({'dimension':'weight','operator':'range','values':[term(a) for a in bounds],'unit':'kg'})
    from rag.study_registry import finditer,load
    from rag.study_registry import products as product_catalog
    registry=load();studies=list(dict.fromkeys(s for pid in product_catalog() for s in finditer(question,pid)))
    # If the identifier scanner returns a parent and its explicit part, the
    # specific printed part is the request. No outcome/registry facts are read.
    studies=[s for s in studies if not any(registry[t[2]].get('parent')==s[2] for t in studies)]
    if len({s[2] for s in studies})>1:return None
    for start,end,key in studies:
        quote=question[start:end]
        shared.append({'dimension':'study','operator':'eq','values':[{'value':key,'anchor':{'quote':quote,'occurrence':question[:start].count(quote)}}]})
    comparison=[]
    arms=unique('comparison')
    references=[a for a in arms if a['value'] in ('EU-Stelara','US-Stelara')]
    drugs=[a for a in arms if a['value'] not in ('EU-Stelara','US-Stelara')]
    if references:
        if len(drugs)!=1:return None
        drug=drugs[0]
        between=bool(re.search(r'두\s*대조약\s*간|대조약\s*간|between|Stelara\s*간',question,re.I))
        against=bool(re.search(r'대비|compared\s+(?:to|with)|versus|against',question,re.I))
        if against:
            # A named reference immediately followed by 대비 applies to that
            # reference. A coordinated EU/US noun explicitly asks both.
            targets=list({a['value']:a for a in entries if a['dimension']=='comparison'
                and a['value'] in ('EU-Stelara','US-Stelara') and re.match(r'\s*대비',question[a['end']:])}.values())
            if re.search(r'\bEU\s*[·/&,]\s*US\s+Stelara\s*대비',question,re.I):targets=references
            if not targets and len(references)==1:targets=references
            if not targets:return None
            comparison.extend({'dimension':'comparison','operator':'unordered_pair','values':[term(drug),term(ref)]} for ref in targets)
        if between:
            if len(references)!=2:return None
            comparison.append({'dimension':'comparison','operator':'unordered_pair','values':[term(a) for a in references]})
        if not comparison:return None
    doses=unique('dose') or [None]
    fields=statistics+unique('attribute')
    fields=[a for a in fields if a['dimension']=='statistic' or a['value']=='unit']
    requests=[]
    for endpoint,dose,pair,field in product(endpoints,doses,comparison or [None],fields):
        conditions=[*deepcopy(shared),eq(endpoint)]
        if dose:conditions.append(eq(dose))
        if pair:conditions.append(deepcopy(pair))
        requests.append({'id':f'pk:{len(requests)+1}','field':{'dimension':field['dimension'],'value':field['value'],'anchor':deepcopy(field['anchor'])},'conditions':conditions})
    if len(requests)>64:return None
    return {'version':VERSION,'original_question':question,'requests':requests,
        'status':'literal_subset','complete_question_interpretation':False,
        'basis':'explicit_original_question_roles_only','semantic_approval':False}
