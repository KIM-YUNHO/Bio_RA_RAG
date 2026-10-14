import unittest
from rag import selection_candidate as s
from tests.test_selection_candidate import evidence

class RequestedQuantityTests(unittest.TestCase):
 def test_dose_header_and_other_row_cannot_be_numeric_answers(self):
  e=evidence('<table><tr><td>Route</td><td>Dose</td></tr><tr><td>Subcutaneous</td><td>Drug low (2 mg/kg), high (13 mg/kg)</td></tr><tr><td>Intravenous</td><td>Drug low (4 mg/kg), high (31 mg/kg)</td></tr></table>')
  c=s.catalog(e,'반복 정맥 투여의 저용량과 고용량은?')
  for value in ('Route','Dose','Subcutaneous','Drug low (2 mg/kg), high (13 mg/kg)'):
   self.assertFalse(next(x for x in c.values() if x['value']==value)['value_eligible'])
  self.assertTrue(next(x for x in c.values() if x['value']=='Drug low (4 mg/kg), high (31 mg/kg)').get('value_eligible',True))

 def test_general_trial_counts_and_unquantified_teae_text_are_not_rates(self):
  e=evidence('Study SB17-1001 randomised 191 participants.\n\nTEAE of malignancy was observed.\n\nTEAEs occurred in 17.6% of subjects.')
  c=s.catalog(e,'TEAE 발생 인원과 비율은?')
  for phrase in ('randomised 191','TEAE of malignancy'):
   self.assertTrue(all(x.get('value_eligible') is False for x in c.values() if phrase in x['value']))
  self.assertTrue(any(x.get('value_eligible',True) and '17.6%' in x['value'] for x in c.values()))

 def test_stratified_teae_uses_only_actual_stratum_event_cells(self):
  e=evidence('<table><tr><th>Outcome</th><th>ADA Positive</th><th>ADA Negative</th></tr><tr><td>TEAE</td><td>11 (24.4%)</td><td>19 (35.8%)</td></tr><tr><td>TEAE Grade 3 or higher</td><td>1 (2.2%)</td><td>2 (3.8%)</td></tr></table>')
  c=s.catalog(e,'ADA 양성·음성 집단별 TEAE 발생 인원과 비율은?')
  self.assertTrue(next(x for x in c.values() if x['value']=='11 (24.4%)').get('value_eligible',True))
  self.assertFalse(next(x for x in c.values() if x['value']=='1 (2.2%)')['value_eligible'])

 def test_ci_pair_is_whole_and_se_pair_is_not_a_ci(self):
  e=evidence('<table><tr><th>Difference (SE)</th><th>Difference 90% CI</th></tr><tr><td>-1.13 (0.54)</td><td>(-4.17, 2.36)</td></tr></table>')
  c=s.catalog(e,'군간 차이와 90% CI는?')
  ci=next(x for x in c.values() if x['value']=='(-4.17, 2.36)')
  self.assertTrue(ci.get('whole_source_unit'));self.assertTrue(all(x.get('value_eligible') is False for x in c.values() if x['kind']=='literal_number' and ci['id'] in x.get('context_ids',[])))
  se=next(x for x in c.values() if x['value']=='-1.13 (0.54)')
  self.assertNotEqual(se.get('whole_source_unit',{}).get('basis'),'whole_compound_cell')

 def test_primary_estimand_is_literal_own_analysis(self):
  e=evidence('According to the primary targeted estimand, the estimated difference was -1.13% (90% CI: -4.17%, 2.36%).')
  q='주 분석 차이와 90% CI는?';c=s.catalog(e,q);p=next(x for x in c.values() if x['kind']=='source_paragraph' and x.get('whole_source_unit'))
  raw={'status':'answered','items':[{'study':'none','item':'difference','source_label':'wrong','value_id':p['id'],'exact':True,'conditions':[]}],'premise_source_ids':[]}
  item=s.resolve(raw,c,e,q,{'studies':[]})['items'][0]
  self.assertTrue(any(v['dimension']=='analysis' and 'primary targeted estimand' in v['quote'] for v in item['conditions']))
  self.assertEqual(item['source_label'],'원문 수치 문장')

 def test_supportive_analysis_does_not_replace_explicit_primary(self):
  e=evidence('In the supportive estimand analysis the difference was -1.42% (90% CI: -4.82%, 2.04%).\n\nAccording to the primary targeted estimand, the difference was -1.13% (90% CI: -4.17%, 2.36%).')
  c=s.catalog(e,'주 분석 차이와 90% CI는?')
  self.assertTrue(all(x.get('value_eligible') is False for x in c.values() if 'supportive estimand' in x['value']))
  self.assertTrue(any(x.get('value_eligible',True) and 'primary targeted estimand' in x['value'] for x in c.values()))

 def test_dose_and_vial_count_are_separate_own_fields(self):
  e=evidence('<table><tr><th>Weight</th><th>Dose</th><th>Number of vials</th></tr><tr><td>≤57 kg</td><td>270 mg</td><td>3</td></tr></table>')
  c=s.catalog(e,'체중별 투여 용량 및 바이알 수는?')
  vial=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='3')
  self.assertTrue(vial.get('value_eligible',True));self.assertEqual(vial['requested_quantity_role']['roles'],['vial_count'])

 def test_dose_route_and_interval_keep_distinct_requested_types(self):
  e=evidence('Adults receive 73 mg by subcutaneous injection initially and 5 weeks later, then every 13 weeks.')
  c=s.catalog(e,'투여 용량, 투여 경로와 투여 간격은?')
  self.assertTrue(any(x.get('value_eligible',True) and x['value']=='73 mg' for x in c.values()))
  self.assertTrue(any(x.get('value_eligible',True) and x['value']=='subcutaneous' for x in c.values()))
  self.assertTrue(any(x.get('value_eligible',True) and x['value']=='13 weeks' for x in c.values()))
  e=evidence('Each participant receives a single 73 mg SC injection.');c=s.catalog(e,'투여 용량과 투여 경로는?')
  self.assertTrue(any(x.get('value_eligible',True) and x['value']=='SC injection' for x in c.values()))

 def test_own_sc_dose_narrative_in_table_is_not_an_iv_dose(self):
  e=evidence('<table><tr><th>Subject</th><th>Dose</th></tr><tr><td>Patients</td><td>Subjects receive 73 mg or 91 mg by subcutaneous injection.</td></tr></table>')
  c=s.catalog(e,'정맥 투여 용량은?')
  self.assertFalse(next(x for x in c.values() if x['kind']=='table_cell' and 'Subjects receive' in x['value'])['value_eligible'])

 def test_wrap_cannot_return_just_the_upper_ci_bound(self):
  e=evidence('The difference was -1.13% (90% CI: -4.17%,\n2.36%).')
  c=s.catalog(e,'군간 차이와 90% CI는?')
  self.assertFalse(next(x for x in c.values() if x['kind']=='source_line' and x['value']=='2.36%).')['value_eligible'])
  self.assertTrue(any(x.get('value_eligible',True) and '-4.17%,\n2.36%' in x['value'] for x in c.values()))

class PrintedIncidenceTests(unittest.TestCase):
 def context(self,extra=''):
  return evidence('<table><tr><th>Outcome</th><th>SB17</th><th>EU-Stelara</th><th>US-Stelara</th></tr><tr><td>At Least One Positive NAb After Administration</td><td>9.1% (2/22)</td><td>12.8% (3/24)</td><td>17.2% (4/23)</td></tr>'+extra+'</table>')
 def choose(self,e,q='SB17-1001 투여 후 NAb 양성률(군별)은?'):
  return s.preselect(s.catalog(e,q),e,q,{'studies':['ST_SB17_1001'],'products':['SB17']})
 def test_unique_event_returns_all_whole_printed_arm_cells(self):
  e=self.context();selected=self.choose(e);self.assertIsNotNone(selected)
  raw,proof=selected;self.assertEqual(len(raw['items']),3);self.assertEqual(proof['event'],'postdose')
  out=s.resolve(raw,s.catalog(e,'SB17-1001 투여 후 NAb 양성률(군별)은?'),e,'SB17-1001 투여 후 NAb 양성률(군별)은?',{'studies':['ST_SB17_1001']})
  self.assertEqual({i['value'] for i in out['items']},{'9.1% (2/22)','12.8% (3/24)','17.2% (4/23)'})
 def test_extra_condition_and_baseline_not_deleted_to_complete(self):
  e=self.context()
  for extra in ('ADA 양성자 중','Day 77','체중 보정','EU-Stelara 제외'):
   self.assertIsNone(self.choose(e,'SB17-1001 투여 후 NAb 양성률(군별) '+extra+'은?'))
 def test_conflicting_same_role_and_blank_arm_do_not_complete(self):
  e=self.context('<tr><td>At Least One Positive NAb After Administration</td><td>8.1% (2/22)</td><td>11.8% (3/24)</td><td>16.2% (4/23)</td></tr>');self.assertIsNone(self.choose(e))
  e=self.context();e['E1']['text']=e['E1']['text'].replace('17.2% (4/23)','Not reported');self.assertIsNone(self.choose(e))
 def test_unbound_trial_cannot_be_completed_by_numbers(self):
  e=self.context();e['E2']['text']='Study CT-P43 1.2';self.assertIsNone(self.choose(e))

if __name__=='__main__':unittest.main()
