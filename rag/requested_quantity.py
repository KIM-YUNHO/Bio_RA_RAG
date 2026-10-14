"""Bounded numeric answer types from the original question and own source.

Headings remain available as context, but cannot answer a requested dose or
event rate. No source-label text is used as evidence of the numeric role.
"""
import re

VERSION='requested-quantity-1'
MASS=re.compile(r'(?<![\w.])\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug)(?:\s*/\s*kg)?\b',re.I)
COUNT_RATE=re.compile(r'^\s*\d+\s*\(\s*\d+(?:\.\d+)?\s*%?\s*\)\s*$')
RATE=re.compile(r'(?<![\w.])\d+(?:\.\d+)?\s*%')
VIAL_HEADER=re.compile(r'number\s+of[^;:]{0,80}\bvials?\b|vial\s+count',re.I)

def requested(question):
 from rag.contract_terms import atoms
 pk=any(a['dimension']=='endpoint' for a in atoms(question))
 dose=bool(not pk and re.search(r'용량|투여량|\bdos(?:e|age)s?\b',question,re.I))
 teae=bool(re.search(r'\bTEAEs?\b',question,re.I) and re.search(r'인원|비율|발생|count|rate|percent|number',question,re.I))
 # This is a type guard, not a compiler for a compound clinical question.
 if re.search(r'설계|분석법|시험\s*단계|무작위|design|assay|randomi[sz]',question,re.I):return {}
 vial=bool(re.search(r'바이알\s*(?:수|개수)|몇\s*(?:개|바이알)|vial\s+count|number\s+of\s+vials',question,re.I))
 interval=bool(re.search(r'투여\s*간격|dosing\s+interval|dose\s+interval|dosing\s+schedule|frequency',question,re.I))
 formulation=bool(re.search(r'제형|formulation',question,re.I))
 return {'dose':dose,'TEAE':teae,'vial_count':vial,'interval':interval,'formulation':formulation} if dose or teae else {}

def supplement(candidates,evidence,question):
 # Restore only the explicitly requested count field before later source,
 # condition and study guards. The legacy dose-only filter rejects a unitless
 # vial count even though its own column supplies a different numeric type.
 if not requested(question).get('vial_count'):return
 from rag.regulatory_scope import eligible
 for c in candidates.values():
  if c['kind']!='table_cell' or c.get('source_header') or not re.fullmatch(r'\s*\d+\s*',c['value']):continue
  own=[candidates[i]['value'] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell']
  if any(VIAL_HEADER.search(h) for h in own) and eligible(c,candidates,evidence,question):c['value_eligible']=True

def apply(candidates,evidence,question):
 req=requested(question)
 if not req:return
 for c in candidates.values():
  if c.get('value_eligible') is False:continue
  headers=[candidates[i]['value'] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell']
  own=c['value'];roles=[]
  if req.get('dose') and MASS.search(own):roles.append('dose')
  if req.get('vial_count') and re.fullmatch(r'\s*\d+\s*',own) and any(VIAL_HEADER.search(h) for h in headers):roles.append('vial_count')
  if req.get('interval'):
   from rag.source_context import containing_sentence
   sentence=own if c['kind']=='source_paragraph' else containing_sentence(c,evidence)
   if re.search(r'\b\d+\s*(?:weeks?|days?|hours?)\b',own,re.I) and re.search(r'\bevery\b|\blater\b|after\s+(?:the\s+)?(?:initial|intravenous)|dosing\s+interval',sentence,re.I):roles.append('dosing_interval')
  if req.get('formulation') and re.search(r'pre[ -]?filled\s+(?:syringe|pen)|\bvials?\b',own,re.I):roles.append('formulation')
  if req.get('TEAE'):
   if c['kind']=='table_cell':
    # A trial enrolment count or an unrelated adverse event is not this row.
    row=[candidates[i]['value'] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell' and not candidates[i].get('source_header')]
    plain=any(re.fullmatch(r'\s*TEAEs?\s*',h,re.I) for h in row)
    if COUNT_RATE.fullmatch(own) and plain:
     strata=not re.search(r'ADA\s*(?:양성|음성)|ADA.*positive|ADA.*negative',question,re.I) or any(re.search(r'\bADA[ -]+(?:Positive|Negative)\b',h,re.I) for h in headers)
     if strata:roles.append('TEAE')
   elif c['kind']=='source_paragraph' and re.search(r'\bTEAEs?\b',own,re.I) and RATE.search(own):roles.append('TEAE')
  if req.get('dose') and re.search(r'투여\s*경로|administration\s+route|route\s+of\s+administration',question,re.I):
   from rag.contract_terms import atoms
   if any(a['dimension']=='route' for a in atoms(own)):roles.append('route')
  if not roles:
   c['value_eligible']=False;c['selection_exclusion']='no_own_requested_quantity_type'
  else:
   c['requested_quantity_role']={'version':VERSION,'roles':roles,'basis':'literal_original_question_and_own_value_or_aligned_row','semantic_approval':False}

def attach(item,candidate,evidence):
 if candidate.get('requested_quantity_role'):item['requested_quantity_role']=candidate['requested_quantity_role'].copy()
 # Use the actual clause instead of a model-authored numeric fact title.
 if candidate['kind']=='source_paragraph' and candidate.get('whole_source_unit'):
  item['model_source_label']=item.get('model_source_label',item['source_label']);item['source_label']='원문 수치 문장'
 if candidate['kind']=='source_paragraph' and (candidate.get('whole_source_unit') or candidate.get('requested_quantity_role')):
  text=candidate['value']
  for dimension,pattern in [('analysis',r'\bprimary\b[\s\S]{0,70}\b(?:analys(?:is|es)|estimand)\b'),('timepoint',r'\b(?:Day|Week)\s*\d+')]:
   if re.search(pattern,text,re.I):
    c={'dimension':dimension,'value':text,'quote':candidate['quote'],'evidence':candidate['evidence']}
    if c not in item['conditions']:item['conditions'].append(c)

def hints(candidates):
 choices=[c for c in candidates.values() if c.get('value_eligible') is not False and (c.get('requested_quantity_role') or c.get('whole_source_unit'))]
 choices=[c for c in choices if not (c['kind']=='source_paragraph' and any(p is not c and p['kind']=='source_paragraph' and p['evidence']==c['evidence'] and c['start']<=p['start'] and p['end']<=c['end'] for p in choices))]
 result=[];used=0
 for c in choices:
  row={'source_id':c['id'],'value':c['value'][:350],'preview_only':len(c['value'])>350,'own_roles':c.get('requested_quantity_role',{}).get('roles',[]),'headers':[candidates[i]['value'][:120] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell']}
  size=len(str(row))
  if used+size>8000 or len(result)>=24:break
  result.append(row);used+=size
 return result
