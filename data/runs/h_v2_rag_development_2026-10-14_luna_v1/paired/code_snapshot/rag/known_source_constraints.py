"""Preserve explicit original constraints even when the full query is unparsed.

This is a rejection/uncertainty guard, never a complete request compiler. It
does not delete exclusions or manufacture a positive interpretation of them.
"""
import re
from rag.contract_match import condition
from rag.question_anchors import catalog
from rag.contract_terms import atoms

VERSION='known-source-constraints-3'
FORMULATIONS={
 'prefilled_pen':(r'프리필드\s*펜|사전\s*충전\s*펜|pre[ -]?filled\s+pen|autoinjector',r'pre[ -]?filled\s+pens?|autoinjector'),
 'prefilled_syringe':(r'프리필드\s*시린지|사전\s*충전\s*주사기|pre[ -]?filled\s+syringe',r'pre[ -]?filled\s+syringes?'),
}

def strength_ranges(text):
 """A printed container/form strength is not an administered dose."""
 return [(m.start(),m.end()) for m in re.finditer(
  r'\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug)\s*(?:(?:정맥|피하)\s*)?(?:바이알|제제|vials?|concentrate|solution\s+for\s+(?:injection|infusion))(?=\s|[은는이가을를의와과]|$)',text,re.I)]

def dose_mentions(question):
 spans=strength_ranges(question)
 return [{'dimension':'dose','number':m[1],'unit':m[2].casefold(),'value':m.group(),'start':m.start(),'end':m.end()}
  for m in re.finditer(r'(?<![\w.])(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug)(?![A-Za-z])',question,re.I)
  if not any(s<=m.start() and m.end()<=e for s,e in spans)]

def requested(question):
 entries=list(catalog(question).values());result=[]
 # One explicit route is unambiguous. IV induction followed by SC maintenance
 # has two contextual routes, so this guard does not flatten them into AND.
 routes={a['value'] for a in entries if a['dimension']=='route'}
 if len(routes)==1:result.append({'dimension':'route','operator':'eq','values':sorted(routes)})
 if re.search(r'주\s*분석|일차\s*분석|primary\s+(?:targeted\s+)?(?:analysis|estimand)',question,re.I) and not re.search(r'보조|민감도|supportive|sensitivity',question,re.I):
  result.append({'dimension':'analysis','operator':'eq','values':['primary']})
 from rag.literal_requests import compile_requests
 if compile_requests(question) is None and re.search(r'GMR|geometric.*ratio|기하평균비',question,re.I) and not re.search(r'두\s*대조약\s*간|Stelara\s*(?:와|과|및).*?Stelara\s*간|\bbetween\b',question,re.I):
  from rag.study_registry import products
  drugs={a['value'] for a in entries if a['dimension']=='comparison' and a['value'] in products()}
  refs={a['value'] for a in entries if a['dimension']=='comparison' and a['value'] in ('EU-Stelara','US-Stelara')
   and (re.match(r'\s*대비',question[a['end']:]) or re.search(r'(?:versus|against|compared\s+(?:to|with))\s*$',question[:a['start']],re.I))}
  if len(drugs)==1 and len(refs)==1:
   result.append({'dimension':'comparison','operator':'unordered_pair','values':[next(iter(drugs)),next(iter(refs))]})
 forms=[name for name,(query,_) in FORMULATIONS.items() if re.search(query,question,re.I)]
 if len(forms)==1:result.append({'dimension':'formulation','operator':'eq','values':forms})
 points=[m for m in re.finditer(r'(?:체중|body\s+weight|weighing)\s*(\d+(?:\.\d+)?)\s*kg',question,re.I)
  if not re.match(r'\s*(?:이하|초과|미만|이상|[~–−-]|to\b|or\s+(?:less|more)|and\b)',question[m.end():],re.I)]
 if len(points)==1:result.append({'dimension':'weight_point','operator':'eq','values':[points[0][1]]})
 return {'version':VERSION,'original_question':question,'constraints':result,'requires_asserted_quote':bool(re.search(r'원문|인용|quote|exact\s+wording',question,re.I)), 'complete_question_interpretation':False,
  'unparsed_original_conditions_preserved':True,'semantic_approval':False}

def check(candidate,candidates,evidence,requirements,graphs=None):
 from rag.contract_relations import table_fact,prose_fact
 from rag.grounded_table_graph import build
 c=candidate
 if c['kind']=='literal_number':
  cell=next((candidates[i] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell'),None)
  if cell:c=cell
  else:
   from rag.source_context import containing_sentence
   text=containing_sentence(c,evidence)
   c={**c,'kind':'source_paragraph','quote':text,'value':text}
 graph=None
 if c['kind']=='table_cell':
  if graphs is not None:
   if c['evidence'] not in graphs:graphs[c['evidence']]=build(evidence[c['evidence']])
   graph=graphs[c['evidence']]
  fact=table_fact(c,evidence,graph=graph)
  if fact is None and graph is not None:
   # Textual dose-group cells need the same own-row guard as numeric cells.
   # The generic numeric fact inventory does not include these cells.
   table=next((t for t in graph['tables'] if t['source_range'][0]<=c['start']<c['end']<=t['source_range'][1]),None)
   contexts=[candidates[i] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell']
   if table and not table['issues'] and contexts:
    from rag.contract_relations import pairs
    fact={'relationship_status':'structural_candidate','observations':[a for h in contexts for a in atoms(h['value'])],
     'explicit_relations':[p for h in contexts for p in pairs(h['value'],evidence[c['evidence']].get('product_id'))],
     'value_binding_status':'whole_cell','semantic_approval':False}
    fact['observations'].extend(a for a in atoms(c['value']) if a['dimension'] in ('route','dose'))
 else:fact=prose_fact(c,evidence)
 checks=[]
 for req in requirements['constraints']:
  if req['dimension']=='analysis':
   text=c['value'] if c['kind']!='table_cell' else '\n'.join(candidates[i]['value'] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell')
   primary=bool(re.search(r'\bprimary\b[\s\S]{0,70}\b(?:analys(?:is|es)|estimand)\b',text,re.I))
   other=bool(re.search(r'\b(?:supportive|sensitivity|secondary)\b[\s\S]{0,40}\b(?:analys(?:is|es)|estimand)\b',text,re.I))
   checks.append({'dimension':'analysis','status':'match' if primary and not other else 'mismatch' if other and not primary else 'unknown','expected':'primary','reason':'literal_own_analysis_scope'})
  elif req['dimension']=='weight_point':
   text=c['quote'] if c['kind']!='table_cell' else '\n'.join(candidates[i]['value'] for i in c.get('context_ids',[]))
   checks.append(weight_point(text,req['values'][0]))
  elif req['dimension']=='formulation':
   text=c['quote']
   if c['kind']=='table_cell':text+='\n'+'\n'.join(candidates[i]['value'] for i in c.get('context_ids',[]))
   wanted=req['values'][0];own=bool(re.search(FORMULATIONS[wanted][1],text,re.I))
   others={name for name,(_,pattern) in FORMULATIONS.items() if name!=wanted and re.search(pattern,text,re.I)}
   status='match' if own and not others else 'mismatch' if others and not own else 'unknown'
   checks.append({'dimension':'formulation','status':status,'expected':wanted,'reason':'own_source_formulation' if status=='match' else 'different_or_unresolved_source_formulation'})
  else:checks.append(condition(fact,req['dimension'],req['operator'],req['values']))
 return {'version':VERSION,'checks':checks,'status':'mismatch' if any(x['status']=='mismatch' for x in checks)
  else 'unknown' if any(x['status']=='unknown' for x in checks) else 'match','semantic_approval':False}

def apply(candidates,evidence,question):
 req=requested(question)
 if not req['constraints']:return
 graphs={}
 for c in candidates.values():
  if c.get('value_eligible') is False:continue
  proof=check(c,candidates,evidence,req,graphs);c['known_original_constraints']=proof
  if proof['status']=='mismatch':c['value_eligible']=False;c['selection_exclusion']='known_original_constraint_mismatch'
  elif req['requires_asserted_quote'] and any(x['dimension'] in ('formulation','weight_point') and x['status']=='unknown' for x in proof['checks']):
   # Withholding unsupported formulation attribution is not a negative fact
   # about the document. Keep an explicitly unknown proof for the audit.
   c['value_eligible']=False;c['selection_exclusion']='requested_literal_scope_not_established'

def weight_point(text,value):
 """Only explicit printed inequalities/intervals; no inferred dose or outcome."""
 from decimal import Decimal
 expected=Decimal(value);categories=[]
 # Each header line is one physical category. Multiple contrary categories
 # in a prose block remain ambiguous instead of borrowing one favourable row.
 for line in text.splitlines():
  if not re.search(r'(?<![/\w])kg(?![/A-Za-z])',line,re.I):continue
  limits=list(re.finditer(r'(≥|≤|>=|<=|>|<)\s*(\d+(?:\.\d+)?)\s*kg(?![/A-Za-z])',line))
  shared=re.search(r'(≥|≤|>=|<=|>|<)\s*(\d+(?:\.\d+)?)\s*(?:kg\s*)?(?:to|and)\s*(≥|≤|>=|<=|>|<)\s*(\d+(?:\.\d+)?)\s*kg(?![/A-Za-z])',line,re.I)
  if shared:
   pairs=[(shared[1],shared[2]),(shared[3],shared[4])]
   categories.append(all({'≥':expected>=Decimal(n),'>=':expected>=Decimal(n),
    '≤':expected<=Decimal(n),'<=':expected<=Decimal(n),'>':expected>Decimal(n),'<':expected<Decimal(n)}[op] for op,n in pairs))
   continue
  if limits:
   categories.append(all({'≥':expected>=Decimal(m[2]),'>=':expected>=Decimal(m[2]),
    '≤':expected<=Decimal(m[2]),'<=':expected<=Decimal(m[2]),'>':expected>Decimal(m[2]),'<':expected<Decimal(m[2])}[m[1]] for m in limits))
  else:
   point=re.search(r'(?:body\s+weight|weighing)\s*(\d+(?:\.\d+)?)\s*kg',line,re.I)
   if point:categories.append(expected==Decimal(point[1]))
 status='match' if categories and all(categories) else 'mismatch' if categories and not any(categories) else 'unknown'
 return {'dimension':'weight_point','status':status,'expected':value,'reason':'explicit_own_weight_category' if status!='unknown' else 'weight_scope_not_uniquely_resolved'}

def finalize(out):
 """Unknown source scope cannot retain a model's exact/answered assertion."""
 unknown=[]
 for item in out['items']:
  checks=item.get('known_original_constraints',{}).get('checks',[])
  unresolved=[c['dimension'] for c in checks if c['dimension'] in ('formulation','weight_point') and c['status']=='unknown']
  if unresolved:
   item['exact']=False
   item['unresolved_conditions']=list(dict.fromkeys([*item.get('unresolved_conditions',[]),*unresolved]))
   unknown.append(item['source_selection']['value_id'])
 if unknown:
  from rag.answer_conditions import label
  out['validation']['unknown_known_source_constraints']={'source_ids':unknown,'semantic_approval':False}
  out['status']='partial'
  out['answer_ko']='\n'.join(f'{label(i)}'+(' (관련 값)' if not i['exact'] else '')+f': {i["value"]} [{", ".join(i["evidence"])}]' for i in out['items'])
