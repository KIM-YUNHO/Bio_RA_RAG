"""Printed population-count roles, distinct from endpoint-specific sample sizes.

Only an explicit analysis-set sentence or a table caption plus its own N header
can qualify a literal. Product codes, totals and percentages remain context.
"""
import re

VERSION='printed-population-counts-1'
PK=r'\bPKS\b|(?:PK|pharmacokinetic)\s+(?:analysis\s+)?set'
COUNT_QUERY=r'number\s+of\s+(?:subjects|participants|patients)|counts?|sample\s+size|인원|몇\s*명'


def requested(question):
    return bool(re.search(PK,question,re.I) and re.search(COUNT_QUERY,question,re.I))


def role_key(role):
    from rag.evidence.contract_relations import arm
    from rag.evidence.contract_terms import atoms
    name=re.sub(r'\(?\s*N\s*=\s*\d+\s*\)?','',role['arm'].replace('\\n','\n')).strip()
    name=arm(' '.join(name.split())).casefold()
    qualifiers=tuple(sorted({(a['dimension'],a['value']) for a in atoms(role['analysis'])
        if a['dimension'] in ('analysis_set','covariate','analysis_timing','population','dose','route')}))
    return name,qualifiers


def table_caption(text,start):
    prefix=text[:start]
    boundary=max(prefix.rfind('</table>')+8 if '</table>' in prefix else 0,
                 prefix.rfind('[UNSUPPLIED SOURCE GAP]')+len('[UNSUPPLIED SOURCE GAP]')
                 if '[UNSUPPLIED SOURCE GAP]' in prefix else 0)
    local=prefix[boundary:]
    captions=list(re.finditer(r'(?im)^Table\s+\d+[^\n]*(?:\n(?!\s*\n)[^\n]+)*',local))
    if not captions:return None
    m=captions[-1]
    return {'quote':m[0],'start':boundary+m.start(),'end':boundary+m.end()}


def printed_cues(chunk):
    text=chunk['text']
    result=[]
    for m in re.finditer(r'<table\b[^>]*>.*?</table\s*>',text,re.I|re.S):
        caption=table_caption(text,m.start())
        if caption and re.search(PK,caption['quote'],re.I) and re.search(r'\bN\s*=\s*\d+',m[0]):
            result.append('printed_pk_set_caption_with_N_headers')
    # An explicit prose PK-set count is equally useful as a tabular count.
    from rag.evidence.prose import sentences
    for a,b in sentences(text,0,len(text)):
        sentence=text[a:b]
        if '<table' in sentence.lower():continue
        if re.search(PK,sentence,re.I) and re.search(r'\b(?:n\s*=\s*\d+|\d+\s+(?:subjects|participants|patients))\b',sentence,re.I):
            result.append('printed_pk_set_count_sentence')
    return list(dict.fromkeys(result))


def annotate(candidates,evidence,question):
    """Make qualified header N values selectable; never create a new number."""
    if not requested(question):return candidates
    from rag.evidence.prose import containing
    # The legacy generic numeric tokenizer can include a closing parenthesis.
    # A typed N header provides a narrower original span, without editing any
    # returned value or the frozen source/cell candidate.
    for cell in list(candidates.values()):
        if cell['kind']!='table_cell' or not cell.get('source_header'):continue
        text=evidence[cell['evidence']]['text']
        for m in re.finditer(r'\bN\s*=\s*(\d+)\b',text[cell['start']:cell['end']]):
            start,end=cell['start']+m.start(1),cell['start']+m.end(1)
            if any(c['start']==start and c['end']==end and c['evidence']==cell['evidence'] for c in candidates.values()):continue
            cid='S'+str(max(int(k[1:]) for k in candidates)+1)
            candidates[cid]={'id':cid,'evidence':cell['evidence'],'value':text[start:end],'quote':cell['quote'],
                'start':start,'end':end,'quote_start':cell['quote_start'],'quote_end':cell['quote_end'],
                'kind':'literal_number','context_ids':[cell['id']],'printed_studies':[],
                'value_eligible':False,'typed_numeric_span':'printed_N_header'}
    for c in candidates.values():
        if c['kind']!='literal_number':continue
        block=evidence[c['evidence']]
        parents=[candidates[i] for i in c.get('context_ids',[]) if i in candidates]
        cell=next((p for p in parents if p['kind']=='table_cell'),None)
        if cell:
            if not cell.get('source_header'):
                from rag.evidence.source_context import _count_arm
                pair=_count_arm(c,candidates,question)
                if pair and c.get('value_eligible') is not False:
                    header,analysis=pair
                    c['population_count_role']={'basis':'own_pk_set_row_and_treatment_header',
                        'arm':header['value'],'arm_quote':header['quote'],'analysis':analysis['value'],
                        'analysis_quote':analysis['quote'],'evidence':c['evidence']}
                continue
            caption=table_caption(block['text'],cell['table_start'])
            if not caption or not re.search(PK,caption['quote'],re.I):continue
            matches=list(re.finditer(r'\bN\s*=\s*(\d+)\b',block['text'][cell['start']:cell['end']]))
            if not any(cell['start']+m.start(1)==c['start'] and cell['start']+m.end(1)==c['end'] for m in matches):continue
            # A broad/overall column is not one of the treatment arms.
            arm=re.sub(r'\(?\s*N\s*=\s*\d+\s*\)?','',cell['value']).strip()
            if not arm or re.fullmatch(r'total|overall|all subjects',arm,re.I):continue
            c['value_eligible']=True
            c['population_count_role']={'basis':'own_treatment_N_header_and_local_pk_set_caption',
                'arm':cell['value'],'arm_quote':cell['quote'],'analysis':caption['quote'],
                'analysis_quote':caption['quote'],'evidence':c['evidence']}
        else:
            sentence=containing(block['text'],c['start'],c['end'])
            if not re.search(PK,sentence,re.I):
                c['value_eligible']=False
                continue
            # The number itself must be syntactically a count. This excludes a
            # drug identifier such as ABP 654 even in a PK-set table caption.
            before=block['text'][max(0,c['start']-12):c['start']]
            after=block['text'][c['end']:c['end']+25]
            c['value_eligible']=bool(re.search(r'\bn\s*=\s*$',before,re.I)
                or re.match(r'\s+(?:subjects|participants|patients)\b',after,re.I))
            arm=re.match(r'\s+in\s+(?:the\s+)?(.+?)\s+(?:treatment\s+)?group\b',block['text'][c['end']:],re.I|re.S)
            if c['value_eligible'] and arm and len(arm[0])<120:
                c['population_count_role']={'basis':'own_explicit_pk_set_n_in_treatment_group_clause',
                    'arm':arm[1],'arm_quote':arm[0].strip(),'analysis':sentence,'analysis_quote':sentence,'evidence':c['evidence']}
    return candidates
