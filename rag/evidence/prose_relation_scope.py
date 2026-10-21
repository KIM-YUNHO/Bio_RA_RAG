"""Bounded printed antecedents for a selected sentence, never page metadata.

Only a unique explicit Study/Trial subject in the same contiguous paragraph
can supply trial identity. Endpoint/timepoint can come from the immediately
preceding explicit endpoint definition, not another analysis or table.
"""
import re
from rag.evidence.contract_terms import atoms
from rag.retrieval.study_registry import finditer,load

VERSION='printed-prose-antecedent-1'
GAP='[UNSUPPLIED SOURCE GAP]'

def linked(candidate,block,requested):
    text=block['text'];start=candidate.get('quote_start',candidate.get('start',-1))
    end=candidate.get('quote_end',candidate.get('end',-1))
    if start<0 or end<start or text[start:end]!=candidate['quote']:return None
    prefix=text[:start]
    cuts=list(re.finditer(r'\n\s*\n|\[UNSUPPLIED SOURCE GAP\]|</?table\b[^>]*>',prefix,re.I))
    begin=cuts[-1].end() if cuts else 0
    prefix=text[begin:start]
    if len(prefix)>1600 or GAP in candidate['quote'] or '<table' in candidate['quote']:return None
    registry=load();ids=list(finditer(prefix,block.get('product_id')))
    distinct={k for _,_,k in ids}
    # A component needs its own printed part relationship; a parent paragraph
    # must not lend component-specific outcomes to another part.
    if len(distinct)!=1 or any(registry[k]['kind']=='study_part' for k in distinct) or re.search(r'\bPart\s+\d+\b',prefix,re.I):return None
    governors=[(a,b,k) for a,b,k in ids if re.search(r'\b(?:study|trial)\s*[:#]?\s*$',prefix[:a],re.I)]
    if not governors:return None
    a,b,key=governors[-1]
    if requested and key not in requested:return None
    proof={'version':VERSION,'study':key,'quote':prefix[a:b],'source_range':[begin+a,begin+b],
           'paragraph_range':[begin,end],'basis':'unique_printed_trial_subject_in_contiguous_paragraph','semantic_approval':False}
    observations=[]
    # A preceding definition is only connected to an explicit analysis result.
    # Criteria, margins, supportive unrelated prose and headings do not inherit
    # the result's endpoint or visit from elsewhere in the paragraph.
    previous=re.split(r'(?<=[.!?])\s+(?=[A-Z])',prefix.rstrip())[-1]
    own=atoms(candidate['quote'])
    if re.search(r'\b(?:analysis|estimand)\b',candidate['quote'],re.I) and re.search(
            r'\b(?:primary\s+)?endpoint\s+(?:was|is)\b',previous,re.I):
        offset=begin+prefix.rfind(previous)
        for dimension in ('endpoint','timepoint'):
            prior=[x for x in atoms(previous) if x['dimension']==dimension]
            if any(x['dimension']==dimension for x in own) or len({x['value'] for x in prior})!=1:continue
            observations.extend({**x,'basis':'immediately_preceding_printed_endpoint_definition',
                 'source_range':[offset+x['start'],offset+x['end']],'semantic_status':'unverified'} for x in prior)
    return {'proof':proof,'observations':observations}

def measure_bundle(text):
    """A single whole difference+CI assertion; no extraction or arithmetic."""
    n=r'[+−–-]?\d+(?:\.\d+)?\s*%?'
    pattern=r'\bdifference\b[^.!?]{0,140}?\b(?:was|is)\s*'+n+r'\s*\(\s*(90|95|99)\s*%\s*(?:CI|confidence\s+interval(?:\s*\[CI\])?)\s*:\s*'+n+r'\s*[,;]\s*'+n+r'\s*\)'
    found=list(re.finditer(pattern,text,re.I))
    if len(found)!=1:return None
    match=found[0]
    # Do not approve a second outcome elsewhere in the same quotation.
    if sum(x['dimension']=='statistic' and x['value']=='difference' for x in atoms(text))!=1:return None
    return {'roles':['difference','CI'+match[1]],'quote':match.group(),'source_range':[match.start(),match.end()],
            'basis':'single_printed_difference_and_complete_confidence_interval_assertion','semantic_approval':False}
