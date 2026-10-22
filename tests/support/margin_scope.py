"""Preserve the complete printed margin predicate instead of isolated percents.

A confidence level and an equivalence margin share the percent unit but have
different roles. This guard retains a whole original statement; it does not
solve or certify respectively/clinical equivalence semantics.
"""
import re
import unicodedata
VERSION='printed-margin-predicate-4-field-scope'

CRITERION=r'(?:\b(?:bioequivalence|equivalence|biosimilarity|similarity)\s+(?:margins?|limits?|bounds?|criteria|criterion|range)|\bBE\s+criteria)'

def requested(question):return bool(re.search(r'(?:동등성|유사성)\s*(?:한계|마진)|(?:equivalence|similarity)\s+margin',question,re.I))
def predicate(text):return bool(re.search(CRITERION,unicodedata.normalize('NFKC',text),re.I)
    and re.search(r'(?:±|\\pm)\s*\d|\b\d+(?:\.\d+)?\s*%?\s*(?:to|[-–])\s*\d',text,re.I))

def apply(candidates,question,*,preserve_other_fields=False):
    if not requested(question):return
    # A margin is one field of a request. Other explicit fields retain their
    # existing candidates and guards; do not turn a mixed query into margin-only.
    mixed=preserve_other_fields or bool(re.search(r'용량|경로|군\s*구성|시험\s*설계|무작위|ADA|NAb|분석법|인원|군\s*간\s*차이|신뢰\s*구간|\b(?:GMR|CI|difference)\b|dos(?:e|age)|route|design|random|assay|subjects',question,re.I))
    for c in candidates.values():
        parents=[candidates[i] for i in c.get('context_ids',[]) if i in candidates]
        if c['kind']=='literal_number' and not any(p['kind']=='table_cell' for p in parents):
            if not mixed or '%' in c['value']:
                c['value_eligible']=False;c['selection_exclusion']='confidence_level_or_margin_percent_role_unbound'
        if c['kind'] in ('source_paragraph','source_line'):
            primary=bool(re.search(r'primary\s+(?:efficacy\s+)?endpoint\s+(?:was|is)',unicodedata.normalize('NFKC',c['value']),re.I))
            margin=predicate(c['value'])
            if not mixed:c['value_eligible']=bool(primary or margin)
            elif preserve_other_fields and margin and not c.get('selection_exclusion'):
                # A prior result-only lexical filter cannot hide an independently
                # requested, complete printed criterion. Later source/contract
                # guards still check its scope; explicit exclusions are retained.
                c['value_eligible']=True
            if margin:c['printed_margin_predicate']=True
            if primary:c['printed_primary_endpoint_predicate']=True
        if c['kind']=='literal_term' and not mixed:c['value_eligible']=False
        if c['kind']=='table_cell' or any(p['kind']=='table_cell' for p in parents):
            contexts=[candidates[i]['value'] for i in c.get('context_ids',[]) if i in candidates]
            margin_cell=any(re.search(CRITERION+r'|\bmargin\b',v,re.I) for v in [c['value'],*contexts])
            if not margin_cell and not mixed:c['value_eligible']=False
            if c.get('source_header') or margin_cell and c['kind']=='literal_number':c['value_eligible']=False

def relabel(item,candidate):
    if candidate.get('printed_margin_predicate'):item['source_label']='동등성 한계의 원문 대응'
    elif candidate.get('printed_primary_endpoint_predicate'):item['source_label']='일차 평가변수와 원문 시점'
    else:return
    # Statistical/design qualifiers from another paragraph cannot describe
    # this whole original predicate. Study linkage is independently checked.
    item['conditions']=[c for c in item['conditions'] if c['dimension']=='study']

def finalize(out,candidates,question):
    if not requested(question):return
    supplied=[cid for cid,c in candidates.items() if c.get('printed_margin_predicate') and c.get('value_eligible') is not False]
    # A table may supply its own margin row. This guard is for an available
    # prose predicate, without changing/guessing table statistic relations.
    if not supplied:return
    selected={i.get('source_selection',{}).get('value_id') for i in out['items']}
    covered=bool(selected.intersection(supplied))
    out['validation']['printed_margin_predicate']={'version':VERSION,'covered':covered,'source_ids':supplied,'semantic_approval':False}
    if not covered:out['status']='partial' if out['items'] else 'not_found'
