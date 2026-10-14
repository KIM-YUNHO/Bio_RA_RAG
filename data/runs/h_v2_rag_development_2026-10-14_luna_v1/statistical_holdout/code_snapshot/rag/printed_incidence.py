"""Unique flat per-arm incidence from printed headers, with no numeric inference."""
import re

VERSION='printed-incidence-1'

def request(question,u):
 from rag.source_fields import requirement
 from rag.study_registry import finditer
 if len(u.get('studies',[]))!=1 or not re.search(r'군별|각\s*군|per[ -]?(?:arm|group)|each\s+(?:arm|group)',question,re.I):return None
 req=requirement(question)
 if not req['analytes']:return None
 event='postdose' if re.search(r'투여\s*후|after\s+(?:administration|dosing)|post[ -]?dose',question,re.I) else 'overall' if re.search(r'전\s*기간|전체|overall',question,re.I) else None
 if event is None:return None
 text=question
 spans={(a,b) for pid in u.get('products',[]) for a,b,_ in finditer(text,pid)}
 if not spans:
  from rag.study_registry import products
  spans={(a,b) for pid in products() for a,b,_ in finditer(text,pid)}
 for a,b in sorted(spans,reverse=True):text=text[:a]+' '*(b-a)+text[b:]
 text=re.sub(r'(?i)\b(?:ADA|NAb)s?\b|투여\s*후|전\s*기간|전체|양성률|발생률|군별|각\s*군|시험|연구|에서|의|은|는|각각|알려줘|after\s+(?:administration|dosing)|post[ -]?dose|overall|incidence|positive\s+rate|per[ -]?(?:arm|group)|each\s+(?:arm|group)|study|and|및|와|과',' ',text)
 if re.search(r'[\w가-힣]',text):return None
 return {'analytes':req['analytes'],'event':event,'study':u['studies'][0]}

def event(headers):
 text=' '.join(headers)
 if re.search(r'after\s+(?:administration|dosing)|post[ -]?dose',text,re.I) and not re.search(r'\b(?:Day|Week)\s*\d+',text,re.I):return 'postdose'
 if re.search(r'overall|any\s*time',text,re.I) and not re.search(r'baseline|boosted|\b(?:Day|Week)\s*\d+',text,re.I):return 'overall'
 return None

def unique(candidates,evidence,question,u):
 req=request(question,u)
 if req is None:return None
 from rag.treatment_identity import printed_arm
 from rag.source_selection import resolve
 from rag.whole_source_units import attach
 from rag.answer_conditions import attribution_missing
 choices={};tables=set();universe={}
 for cid,c in candidates.items():
  if c['kind']!='table_cell' or c.get('source_header') or c.get('own_field_role') not in req['analytes']:continue
  own=[candidates[i] for i in c.get('context_ids',[]) if candidates[i]['kind']=='table_cell']
  observed=event([h['value'] for h in own])
  if observed!=req['event']:continue
  arms={printed_arm(h['value'],evidence[c['evidence']].get('product_id')) for h in own if h.get('source_header')};arms.discard(None)
  if len(arms)!=1:continue
  key=(c['evidence'],c['table_start']);universe.setdefault(key,set()).add((c['own_field_role'],next(iter(arms))))
  if c.get('value_eligible') is False:continue
  raw={'status':'answered','items':[{'study':req['study'],'item':'printed antibody incidence','source_label':'원문 양성률','value_id':cid,'exact':True,'conditions':[]}],'premise_source_ids':[]}
  probe=resolve(raw,candidates,evidence,question,u)['items'][0];attach(probe,c,candidates,evidence)
  if attribution_missing([probe],evidence,u,[]):continue
  tables.add(key);choices.setdefault((c['own_field_role'],next(iter(arms))),[]).append(raw['items'][0])
 if len(tables)!=1 or not choices or any(len(v)!=1 for v in choices.values()):return None
 key=next(iter(tables))
 if set(choices)!=universe[key] or {a for a,_ in choices}!=req['analytes']:return None
 # Include every printed treatment column, including a column whose requested
 # analyte is blank/unbound. Such a table cannot be declared complete.
 arms={printed_arm(c['value'],evidence[key[0]].get('product_id')) for c in candidates.values() if c['kind']=='table_cell' and c['evidence']==key[0] and c.get('table_start')==key[1] and c.get('source_header')};arms.discard(None)
 if any({arm for a,arm in choices if a==analyte}!=arms for analyte in req['analytes']):return None
 items=[v[0] for v in choices.values()]
 return {'status':'answered','items':items,'premise_source_ids':[], '_proof':{'version':VERSION,'basis':'complete_bounded_original_incidence_request_and_unique_own_analyte_event_arm_cells_with_checked_study','source_ids':[i['value_id'] for i in items],'event':req['event'],'semantic_approval':False,'new_model_calls':0}}
