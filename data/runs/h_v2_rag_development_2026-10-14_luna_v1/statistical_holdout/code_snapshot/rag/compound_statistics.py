"""A compound statistic cell preserves its printed estimate/dispersion roles."""
import re
from rag.contract_terms import atoms
VERSION='whole-compound-statistic-cell-1'

def apply(candidates,question):
    if not any(a['dimension']=='endpoint' for a in atoms(question)):return
    if not re.search(r'mean|median|평균|중앙값|범위|\brange\b|표준\s*편차|\bSD\b',question,re.I):return
    for c in candidates.values():
        if c['kind']!='literal_number':continue
        parents=[candidates[i] for i in c.get('context_ids',[]) if i in candidates and candidates[i]['kind']=='table_cell']
        for p in parents:
            headers=[candidates[i]['value'] for i in p.get('context_ids',[]) if i in candidates and candidates[i]['kind']=='table_cell']
            paired=any(re.search(r'\bmean\b.*\bSD\b|\bmedian\b.*\brange\b|(?:geometric\s+mean|\bGM\b).*\bCV\b|\bminimum\b.*\bmaximum\b',h,re.I) for h in headers)
            if paired and len(re.findall(r'(?<![\w.])\d+(?:[.,]\d+)?',p['value']))>1:
                c['value_eligible']=False;c['selection_exclusion']='compound_statistic_requires_whole_original_cell'
