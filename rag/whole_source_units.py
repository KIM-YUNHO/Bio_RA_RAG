"""Keep compound quantities and their own printed headers as source units.

No rewritten numbers, calculated results, inferred clinical labels or answer
values. IDs/offsets remain within the supplied immutable source context.
"""
import re

VERSION='whole-source-units-2'
RANGE=re.compile(r'(?<![\w.])[-−+]?\d+(?:\.\d+)?\s*(?:%|[°º]\s*C)?\s*(?:to|[–−-])\s*[-−+]?\d+(?:\.\d+)?\s*(?:%|[°º]\s*C)?',re.I)
COUNT_PERCENT=re.compile(r'^\s*\d+\s*\(\s*\d+(?:\.\d+)?\s*%\s*\)\s*$')
MASS=re.compile(r'\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug)(?:\s*/\s*kg)?\b',re.I)

def apply(candidates,evidence,question):
 for c in candidates.values():
  if c['kind']!='literal_number' or c.get('value_eligible') is False:continue
  parent=next((candidates[i] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell'),None)
  if parent and parent.get('value_eligible') is not False and not parent.get('printed_pk_role') and not parent.get('population_count_role'):
   ci_pair=bool(re.fullmatch(r'\s*\(?\s*[-−+]?\d+(?:\.\d+)?\s*[,;]\s*[-−+]?\d+(?:\.\d+)?\s*\)?\s*',parent['value']) and any(re.search(r'\bCI\b|confidence\s+interval',candidates[i]['value'],re.I) for i in parent.get('context_ids',[]) if candidates[i]['kind']=='table_cell'))
   if COUNT_PERCENT.fullmatch(parent['value']) or len(MASS.findall(parent['value']))>1 or ci_pair:
    c['value_eligible']=False;c['selection_exclusion']='compound_quantity_requires_whole_source_cell'
    parent['whole_source_unit']={'version':VERSION,'basis':'whole_compound_cell','semantic_approval':False}
   continue
  if parent:continue
  if not re.search(r'범위|\brange\b|신뢰\s*구간|confidence\s+interval|(?<![A-Za-z])CI(?![A-Za-z])',question,re.I):continue
  text=evidence[c['evidence']]['text'];parents=[p for p in candidates.values() if p['evidence']==c['evidence']
   and p['kind']=='source_paragraph' and p.get('value_eligible') is not False and p['start']<=c['start']<c['end']<=p['end']]
  if not parents:continue
  p=min(parents,key=lambda p:len(p['value']))
  range_here=any(p['start']+m.start()<=c['start']<c['end']<=p['start']+m.end() for m in RANGE.finditer(p['value']))
  ci_here=bool(re.search(r'\bCI\b|confidence\s+interval',p['value'],re.I) and re.search(r'\([^)]*\d[^)]*[,;][^)]*\d[^)]*\)',p['value']))
  if range_here or ci_here:
   c['value_eligible']=False;c['selection_exclusion']='range_or_CI_requires_complete_source_sentence'
   p['whole_source_unit']={'version':VERSION,'basis':'whole_contiguous_quantity_statement','semantic_approval':False}
 # Wrapped lines cannot bypass complete-quantity requirements by keeping one
 # bound of a supported parent sentence. The exact complete sentence remains.
 for c in candidates.values():
  if c['kind']!='source_line' or c.get('value_eligible') is False:continue
  parents=[p for p in candidates.values() if p['evidence']==c['evidence'] and p['kind']=='source_paragraph' and p.get('whole_source_unit') and p['start']<=c['start'] and c['end']<=p['end']]
  if parents and not any(p['start']==c['start'] and p['end']==c['end'] for p in parents):
   c['value_eligible']=False;c['selection_exclusion']='wrapped_quantity_line_requires_complete_source_sentence'

def attach(item,candidate,candidates,evidence):
 if candidate['kind']!='table_cell':return
 if not candidate.get('whole_source_unit') and not candidate.get('own_field_role'):return
 # The candidate catalog supplies only aligned physical row/column headers.
 # Missing/invalid source geometry has no context_ids and remains unresolved.
 headers=[candidates[i] for i in candidate.get('context_ids',[]) if candidates[i]['kind']=='table_cell']
 proof=[]
 from rag.contract_terms import atoms
 from rag.treatment_identity import printed_arm
 from rag.categorical_roles import weight_category
 for h in headers:
  d='column_header' if h.get('source_header') else 'row_header'
  dims=[d]
  if printed_arm(h['value'],evidence[candidate['evidence']].get('product_id')):dims.append('arm')
  if weight_category(h['value']):dims.append('population')
  from rag.known_source_constraints import strength_ranges
  own=atoms(h['value'])
  for role in ('route','dose'):
   values={a['value'] for a in own if a['dimension']==role}
   if len(values)==1 and (role!='dose' or not strength_ranges(h['value'])):dims.append(role)
  if re.search(r'\b(?:Day|Week)\s*\d+',h['value'],re.I):dims.append('timepoint')
  if re.search(r'\bprimary\b[\s\S]{0,70}\b(?:analys(?:is|es)|estimand)\b',h['value'],re.I):dims.append('analysis')
  for dimension in dict.fromkeys(dims):
   c={'dimension':dimension,'value':h['value'],'quote':h['quote'],'evidence':h['evidence']}
   if c not in item['conditions']:item['conditions'].append(c)
  proof.append({'source_id':h['id'],'source_range':[h['start'],h['end']],'kind':d,'evidence':h['evidence']})
 if proof:
  item['printed_whole_source_headers']={'version':VERSION,'headers':proof,'semantic_approval':False}
  item['model_source_label']=item.get('model_source_label',item['source_label'])
  # A model's 'middle/high/ADA-positive' labels cannot override a whole cell
  # that itself contains several groups. Display the actual cell and headers.
  if candidate.get('whole_source_unit'):item['source_label']='원문 표 값'
