import unittest
from rag import selection_candidate as s
from tests.test_selection_candidate import evidence

class WholeSourceUnitTests(unittest.TestCase):
 def test_percent_ranges_are_not_lower_bounds(self):
  e=evidence('The background risks are 3% to 7% and 11% to 24%, respectively.')
  c=s.catalog(e,'두 위험의 범위는?')
  fragments=[x for x in c.values() if x['kind']=='literal_number' and x['evidence']=='E1']
  self.assertTrue(fragments)
  self.assertTrue(all(x.get('value_eligible') is False for x in fragments))
  self.assertTrue(any(x.get('value_eligible',True) and '3% to 7%' in x['value'] and '11% to 24%' in x['value'] for x in c.values()))

 def test_ci_uses_complete_statement_without_computing(self):
  e=evidence('The estimated difference was -1.13%, with a 90% CI (-4.17%, 2.36%).')
  c=s.catalog(e,'군간 차이와 90% CI는?')
  self.assertTrue(all(x.get('value_eligible') is False for x in c.values() if x['kind']=='literal_number' and x['evidence']=='E1'))
  self.assertTrue(any(x.get('value_eligible',True) and '-4.17%, 2.36%' in x['value'] for x in c.values()))

 def test_whole_count_percent_keeps_direct_stratum_headers(self):
  t='<table><tr><th>Outcome</th><th colspan="2">CT-P43</th></tr><tr><th></th><th>ADA-positive</th><th>ADA-negative</th></tr><tr><td>TEAE</td><td>17 (48.6%)</td><td>113 (53.1%)</td></tr></table>'
  e=evidence(t);q='ADA 양성 음성 집단별 TEAE 인원과 비율은?';c=s.catalog(e,q)
  chosen=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='17 (48.6%)')
  children=[x for x in c.values() if x['kind']=='literal_number' and chosen['id'] in x.get('context_ids',[])]
  self.assertTrue(all(x.get('value_eligible') is False for x in children))
  raw={'status':'partial','items':[{'study':'none','item':'wrong model name','source_label':'ADA 음성 비율','value_id':chosen['id'],'exact':False,'conditions':[]}],'premise_source_ids':[]}
  item=s.resolve(raw,c,e,q,{'studies':[]})['items'][0]
  rendered=' '.join(x['value'] for x in item['conditions'])
  self.assertIn('ADA-positive',rendered);self.assertNotIn('ADA-negative',rendered);self.assertIn('TEAE',rendered)
  self.assertEqual(item['value'],'17 (48.6%)');self.assertEqual(item['source_label'],'원문 표 값')

 def test_distinct_equal_cells_are_not_deduplicated(self):
  t='<table><tr><th>Outcome</th><th>Group A</th><th>Group B</th></tr><tr><td>Events</td><td>13 (26.0%)</td><td>13 (26.0%)</td></tr></table>'
  e=evidence(t);c=s.catalog(e,'발생 인원과 비율은?');cells=[x for x in c.values() if x['kind']=='table_cell' and x['value']=='13 (26.0%)']
  raw={'status':'partial','items':[{'study':'none','item':'events','source_label':'원문값','value_id':x['id'],'exact':False,'conditions':[]} for x in cells],'premise_source_ids':[]}
  self.assertEqual(len(s.resolve(raw,c,e,'발생 인원과 비율은?',{'studies':[]})['items']),2)

 def test_no_range_bridge_across_gap(self):
  c=s.catalog(evidence('The lower bound is 3%.\n[UNSUPPLIED SOURCE GAP]\nThe upper bound is 7%.'),'위험 범위는?')
  self.assertFalse(any('whole_source_unit' in x for x in c.values()))

 def test_korean_dilution_inflections_share_the_same_scope(self):
  from rag.source_event_scope import requested
  for wording in ('희석한 정맥 주입액','희석 후 용액','희석된 용액','희석액'):
   self.assertEqual(requested(wording+'의 보관 시간은?')['formulation'],'diluted_infusion')

 def test_per_arm_antibody_response_cannot_complete_with_one_arm(self):
  from rag.preprocessed_evidence import grounded_answer
  e=evidence('<table><tr><th>Outcome</th><th>SB17</th><th>EU-Stelara</th><th>US-Stelara</th></tr><tr><td>At least one positive NAb after administration</td><td>9.1% (2/22)</td><td>12.8% (3/24)</td><td>17.2% (4/23)</td></tr></table>')
  q='SB17-1001 투여 후 NAb 양성률(군별)은?';u={'studies':['ST_SB17_1001']};c=s.catalog(e,q)
  first=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='9.1% (2/22)')
  raw={'status':'answered','items':[{'study':'ST_SB17_1001','item':'NAb','source_label':'NAb 양성률','value_id':first['id'],'exact':True,'conditions':[]}],'premise_source_ids':[]}
  out=grounded_answer(s.resolve(raw,c,e,q,u),e);s.finalize(out,c,e,q,u)
  self.assertEqual(out['status'],'partial');self.assertEqual(len(out['items']),1)
  self.assertEqual({r['arm'] for r in out['validation']['own_incidence_arm_coverage']['missing']},{'EU-Stelara','US-Stelara'})

 def test_mixed_diluted_fields_do_not_all_require_stability_word(self):
  from rag.requested_facts import slots
  e=evidence('After dilution\nChemical and physical stability of the diluted infusion solution is 16 hours at 27°C.\n\nFrom a microbiological point of view, the diluted infusion solution should be used immediately, normally within 9 hours unless validated aseptic preparation.\n\nThe diluted infusion solution concentration is between 1.27 and 2.43 mg/mL.')
  q='희석 후 용액의 화학적 안정성, 미생물학적 사용 지침 및 농도 범위는?';c=s.catalog(e,q)
  for part in ('16 hours','microbiological','1.27 and 2.43'):
   self.assertTrue(any(x.get('value_eligible',True) and part in x['value'] for x in c.values()))
  self.assertTrue({'storage_chemical_stability','storage_microbiology','storage_concentration'}<={r['name'] for r in slots(c,e,q)})

 def test_non_pk_range_is_not_reported_as_unresolved_pk_request(self):
  from rag.preprocessed_evidence import grounded_answer
  e=evidence('The risks are 3% to 7% and 11% to 24%, respectively.');q='두 위험의 범위는?';c=s.catalog(e,q)
  p=next(x for x in c.values() if x['kind']=='source_paragraph' and x['evidence']=='E1')
  raw={'status':'answered','items':[{'study':'none','item':'range','source_label':'위험 범위','value_id':p['id'],'exact':True,'conditions':[]}],'premise_source_ids':[]}
  out=grounded_answer(s.resolve(raw,c,e,q,{'studies':[]}),e);s.finalize(out,c,e,q,{'studies':[]})
  self.assertNotIn('unresolved_original_pk_requests',out['validation'])

if __name__=='__main__':unittest.main()
