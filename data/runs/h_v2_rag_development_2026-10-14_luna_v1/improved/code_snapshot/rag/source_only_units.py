"""Whole source is the default; scalar answers need a printed numeric role.

The guard is independent of product, question ID, expected value and page.
Original candidates and model labels remain in the audit; no text is rewritten.
"""
import re

VERSION='source-only-units-2'


def printed_regimen_dose(candidate,evidence,sentence):
 """Bind a repeated prescribed dose to its own clause, never ingredients.

 A recommended dose may be repeated in a maintenance clause. Equal quantities
 alone do not establish the relationship: both the predicate and this literal's
 own position are required. Distinct mass groups stay whole.
 """
 from rag.requested_quantity import MASS
 masses={' '.join(m.group().casefold().split()) for m in MASS.finditer(sentence)}
 if len(masses)!=1 or not re.fullmatch(MASS,candidate['value'].strip()):return None
 text=evidence[candidate['evidence']]['text']
 for m in re.finditer(re.escape(sentence),text):
  if not m.start()<=candidate['start']<candidate['end']<=m.end():continue
  before=text[m.start():candidate['start']];after=text[candidate['end']:m.end()]
  direct=bool(re.search(r'\brecommended\s+(?:dose|dosage)\s+is\s*$',before,re.I))
  repeat=bool(re.search(r'\brecommended\s+(?:dose|dosage)\s+is\b',sentence,re.I) and
              re.search(r'\bfollowed\s+by\s*$',before,re.I) and re.match(r'\s+every\s+\d+\s+(?:weeks?|days?)\b',after,re.I))
  if direct or repeat:return {'quote':sentence,'evidence':candidate['evidence'],
   'source_range':[m.start(),m.end()],'basis':'own_recommended_dose_or_repeat_regimen_clause','semantic_approval':False}
 return None

def apply(candidates,evidence,question):
 from rag.source_context import containing_sentence
 from rag.requested_quantity import MASS
 for c in candidates.values():
  if c.get('value_eligible') is False:continue
  typed=bool(c.get('printed_pk_fact') or c.get('printed_pk_prose_roles') or c.get('population_count_role') or c.get('own_field_role'))
  if c['kind']=='literal_number' and not typed:
   roles=c.get('requested_quantity_role',{}).get('roles',[])
   sentence=containing_sentence(c,evidence)
   # These quantities have a requested, local role. Arbitrary ingredients or
   # numeric pieces of a range do not obtain a dose role merely from mg/kg.
   dose='dose' in roles and len(MASS.findall(sentence))==1 and re.search(r'\breceiv\w*\b|\badminister\w*\b|\binject\w*\b',sentence,re.I)
   regimen=printed_regimen_dose(c,evidence,sentence) if 'dose' in roles else None
   if regimen:c['printed_administered_dose']=regimen;dose=True
   interval='dosing_interval' in roles
   age=bool(re.search(r'소아|pediatric|paediatric',question,re.I) and re.search(r'적응증|indication',question,re.I) and re.search(r'최저|최소|minimum|연령|age',question,re.I)
       and re.fullmatch(r'\s*\d+\s*years?\s*',c['value'],re.I) and re.search(r'\bindicated\b',sentence,re.I) and re.search(r'\bpediatric|\bpaediatric',sentence,re.I))
   if age:c['printed_indication_age']={'quote':sentence,'evidence':c['evidence'],'basis':'own_indication_predicate_and_requested_age_quantity','semantic_approval':False}
   if not dose and not interval and not age:
    c['value_eligible']=False;c['selection_exclusion']='untyped_scalar_requires_whole_source'
  elif c['kind']=='source_line' and not typed:
   if not c['value'].lstrip().startswith(('•','●','▪')):
    c['value_eligible']=False;c['selection_exclusion']='wrapped_line_is_context_use_whole_source'
  elif c['kind']=='literal_term' and not typed:
   if 'route' not in c.get('requested_quantity_role',{}).get('roles',[]):
    c['value_eligible']=False;c['selection_exclusion']='unrequested_literal_term_is_context'
  elif c['kind']=='table_cell' and not typed:
   c.setdefault('whole_source_unit',{'version':VERSION,'basis':'whole_original_cell','semantic_approval':False})

def prompt_view(candidates):
 """Expose answer units and their literal context closure, using original IDs."""
 kept={cid for cid,c in candidates.items() if c.get('value_eligible') is not False or c['printed_studies']}
 pending=list(kept)
 while pending:
  cid=pending.pop()
  for other in candidates[cid].get('context_ids',[]):
   if other in candidates and other not in kept:kept.add(other);pending.append(other)
 return {cid:c for cid,c in candidates.items() if cid in kept}

def attach(item,candidate,candidates,evidence,question):
 typed=bool(candidate.get('printed_pk_fact') or candidate.get('printed_pk_prose_roles') or candidate.get('population_count_role') or candidate.get('own_field_role') or item.get('own_comparison_binding'))
 if typed:return
 roles=candidate.get('requested_quantity_role',{}).get('roles',[])
 if candidate.get('printed_administered_dose'):item['printed_administered_dose']=candidate['printed_administered_dose'].copy()
 scalar=candidate['kind'] in ('literal_number','literal_term')
 if candidate.get('printed_indication_age'):
  proof=candidate['printed_indication_age'];item['source_label']='원문 소아 적응증 연령';item['printed_indication_age']=proof.copy()
  item['conditions'].append({'dimension':'eligibility','value':proof['quote'],'quote':proof['quote'],'evidence':proof['evidence']})
  return
 if scalar and roles:return
 item.setdefault('model_source_label',item['source_label']);item.setdefault('model_item',item['item'])
 item['source_label']='원문 표 값' if candidate['kind']=='table_cell' else '원문 수치 문장' if candidate.get('whole_source_unit') else '원문 인용'
 item['item']='whole original source'
 item['source_only_rendering']={'version':VERSION,'basis':'literal_whole_source_without_model_fact_labels','semantic_approval':False}
 # Independent study/condition checks still use the full recorded conditions.
 # The display uses only aligned table headers and closed list introductions.
 item['source_display_conditions']=[dict(c) for c in item['conditions'] if c['dimension'] in ('row_header','column_header') and candidate['kind']=='table_cell']
 from rag.prose_list_context import intro
 proof=intro(candidate,evidence)
 if proof:item['source_list_intro']=proof

def deduplicate(out):
 kept=[];removed=[];seen=set()
 for item in out['items']:
  if item.get('source_only_rendering'):
   key=(item['study'],tuple(item['evidence']),item['source_selection']['value_id'])
   if key in seen:removed.append(item);continue
   seen.add(key)
  kept.append(item)
 out['items']=kept
 if removed:out['duplicate_whole_source_units']=removed
