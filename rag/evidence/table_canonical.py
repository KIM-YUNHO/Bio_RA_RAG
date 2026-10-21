"""Separate a printed column unit from a model's combined table value.

This does not join cells or infer numbers. Flat, unmerged table coordinates must
identify one exact data cell and its literal column unit. Ambiguity fails closed.
"""
from copy import deepcopy
import re
from rag.evidence.evidence import normalize, contains_quote
from rag.evidence.table_evidence import cells

UNIT = re.compile(r'\(((?:[μµunm]?g|kg|mg|mL|L|h|ng)(?:[·/^\-A-Za-z0-9μµ]+)?)\)')


def canonicalize(out, evidence):
    result=deepcopy(out)
    for item in result['items']:
        matches=[]
        for key in item['evidence']:
            if key not in evidence or not contains_quote(evidence[key]['text'],item['quote']):continue
            source=evidence[key]['text']; parsed=cells(source)
            if len(re.findall(r'<table\b',source,re.I))!=1:continue
            if any(c['rowspan']!='1' or c['colspan']!='1' for c in parsed):continue
            for cell in parsed:
                if cell['tag']!='td' or not contains_quote(item['quote'],cell['raw']):continue
                header=next((c for c in reversed(parsed) if c['tag']=='th' and c['column']==cell['column'] and c['row']<cell['row']),None)
                if header is None:continue
                unit=UNIT.search(header['value'])
                if not unit or normalize(item['value'])!=normalize(cell['value']+' '+unit[1]):continue
                row=next((c for c in parsed if c['row']==cell['row'] and c['column']==0),None)
                matches.append((key,cell,header,unit[1],row,source))
        if len(matches)!=1:continue
        key,cell,header,unit,row,source=matches[0]
        item['original_model_value']=item['value'];item['value']=cell['value']
        conditions=item.setdefault('conditions',[])
        for dimension,value,c in [('unit',unit,header),('column_header',header['value'],header),
                                   *([('row_header',row['value'],row)] if row else [])]:
            condition={'dimension':dimension,'value':value,'quote':source[c['start']:c['end']],'evidence':key}
            if condition not in conditions:conditions.append(condition)
        item['table_value_derivation']={'method':'separate_single_cell_value_and_printed_column_unit',
            'offset_basis':'evidence_block_text; source_spans map back to representation units',
            'evidence':key,'row':cell['row'],'column':cell['column'],
            'data_cell_range':[cell['start'],cell['end']],'header_cell_range':[header['start'],header['end']],
            'semantic_approval':False}
    return result
