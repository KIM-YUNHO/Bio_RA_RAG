import unittest
from rag import selection_candidate as s
from rag.known_source_constraints import dose_mentions,strength_ranges,requested
from rag.literal_requests import compile_requests
from rag.answer_conditions import missing,explicit_conflicts
from tests.test_selection_candidate import evidence

class KnownSourceConstraintTests(unittest.TestCase):
 def test_partial_question_keeps_explicit_comparison_even_with_exclusion(self):
  q='CT-P43 1.1 Part 2 US 대조약군을 제외한 사후 EU Stelara 대비 Cmax GMR는?'
  self.assertIsNone(compile_requests(q))
  e=evidence('Table 21. ANCOVA\n<table><tr><th>Parameter</th><th>CT-P43/EU-Stelara GMR</th><th>CT-P43/US-Stelara GMR</th></tr><tr><td>Cmax</td><td>0.873</td><td>1.246</td></tr></table>')
  c=s.catalog(e,q)
  good=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='0.873')
  bad=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='1.246')
  self.assertNotEqual(good.get('value_eligible'),False)
  self.assertFalse(bad['value_eligible'])
  self.assertEqual(bad['known_original_constraints']['status'],'mismatch')
  self.assertFalse(requested(q)['complete_question_interpretation'])
  self.assertIn('제외',requested(q)['original_question'])

 def test_route_uses_own_row_not_another_row(self):
  e=evidence('<table><tr><th>Route</th><th>Dose</th></tr><tr><td>Subcutaneous</td><td>7, 22 and 61 mg/kg</td></tr><tr><td>Intravenous</td><td>9, 31 and 74 mg/kg</td></tr></table>')
  c=s.catalog(e,'반복 정맥 투여 시험의 용량은?')
  wrong=next(x for x in c.values() if x['kind']=='table_cell' and x['value'].startswith('7,'))
  self.assertFalse(wrong['value_eligible'])
  right=next(x for x in c.values() if x['kind']=='table_cell' and x['value'].startswith('9,'))
  self.assertNotEqual(right.get('value_eligible'),False)
  e=evidence('<table><tr><th>Route</th><th>Dose groups</th></tr><tr><td>Subcutaneous</td><td>Drug A low (7 mg/kg), medium (22 mg/kg) and high (61 mg/kg)</td></tr><tr><td>Intravenous</td><td>Drug A low (9 mg/kg), medium (31 mg/kg) and high (74 mg/kg)</td></tr></table>')
  c=s.catalog(e,'반복 정맥 투여 시험의 용량은?')
  wrong=next(x for x in c.values() if x['kind']=='table_cell' and 'low (7' in x['value'])
  self.assertFalse(wrong['value_eligible'])
  self.assertEqual(wrong['known_original_constraints']['status'],'mismatch')

 def test_unknown_lookup_formulation_is_related_not_silently_deleted(self):
  e=evidence('At the end of the storage period the product may be returned to the refrigerator once.')
  c=s.catalog(e,'프리필드 펜의 냉장 복귀 조건은?')
  whole=next(x for x in c.values() if x['kind']=='source_paragraph' and x['evidence']=='E1')
  self.assertNotEqual(whole.get('value_eligible'),False)
  self.assertEqual(whole['known_original_constraints']['status'],'unknown')

 def test_induction_and_maintenance_routes_are_not_flattened(self):
  self.assertFalse([c for c in requested('정맥 유도치료 후 피하 유지 용량은?')['constraints'] if c['dimension']=='route'])

 def test_complete_two_pair_requests_are_not_replaced_by_one_partial_pair(self):
  q='SB17-1001 사후 ANCOVA US Stelara 대비, EU Stelara와 US Stelara 간 Cmax GMR와 90% CI는?'
  self.assertIsNotNone(compile_requests(q))
  e=evidence('Table 33. Post-hoc ANCOVA\n<table><tr><th>Parameter</th><th>Ratio of SB17/US Stelara (90% CI)</th><th>Ratio of EU Stelara/US Stelara (90% CI)</th></tr><tr><td>Cmax</td><td>0.91 (0.83, 1.07)</td><td>1.12 (1.01, 1.24)</td></tr></table>')
  c=s.catalog(e,q)
  cells=[x for x in c.values() if x['kind']=='table_cell' and x['value'] in ('0.91 (0.83, 1.07)','1.12 (1.01, 1.24)')]
  self.assertEqual(len(cells),2);self.assertTrue(all(x.get('value_eligible') is not False for x in cells))

 def test_unsupported_formulation_claim_remains_unknown_not_false(self):
  c=s.catalog(evidence('The medicine is approved for children aged 7 to 16 years.'),'소아에게 프리필드 펜을 권장한다고 명시한 원문은?')
  whole=next(x for x in c.values() if x['kind']=='source_paragraph')
  self.assertEqual(whole['known_original_constraints']['status'],'unknown')
  self.assertFalse(whole['value_eligible'])
  self.assertEqual(whole['selection_exclusion'],'requested_literal_scope_not_established')

 def test_weight_point_cannot_borrow_higher_category_pen(self):
  from rag.known_source_constraints import weight_point
  self.assertEqual(weight_point('< 63 kg','51')['status'],'match')
  self.assertEqual(weight_point('≥ 63 kg to ≤ 104 kg','51')['status'],'mismatch')
  self.assertEqual(weight_point('Children aged 7 to 16 years','51')['status'],'unknown')
  self.assertEqual(weight_point('Dose ≤ 43 mg/kg','51')['status'],'unknown')
  self.assertEqual(weight_point('BMI ≤ 27 kg/m2','51')['status'],'unknown')
  self.assertEqual(weight_point('≥ 63 to ≤ 104 kg','51')['status'],'mismatch')
  self.assertEqual(weight_point('< 63 kg\n> 104 kg','51')['status'],'unknown')
  self.assertFalse([c for c in requested('체중 63 kg 이하 및 104 kg 초과 용량은?')['constraints'] if c['dimension']=='weight_point'])
  e=evidence('<table><tr><th>Weight</th><th>Form</th></tr><tr><td>&lt; 63 kg</td><td>vial</td></tr><tr><td>≥ 63 kg to ≤ 104 kg</td><td>pre-filled pen</td></tr></table>')
  c=s.catalog(e,'체중 51 kg 소아에게 프리필드 펜을 권장한 원문은?')
  pen=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='pre-filled pen')
  self.assertFalse(pen['value_eligible']);self.assertEqual(pen['known_original_constraints']['status'],'mismatch')

 def test_strength_and_actual_dose_with_korean_particles(self):
  self.assertTrue(strength_ranges('125 mg 정맥제제를 희석한 용액'))
  self.assertFalse(dose_mentions('125 mg 바이알 수는?'))
  self.assertFalse(dose_mentions('125 mg 정맥제제의 안정성은?'))
  self.assertEqual(dose_mentions('권장 용량이 137 mg이라고 명시한 원문')[0]['number'],'137')
  self.assertEqual(dose_mentions('125 mg vial과 371 mg 투여 용량')[0]['number'],'371')

 def test_opt_in_strength_does_not_require_administered_dose(self):
  item={'conditions':[],'numeric_question_role_contract':{'administered_doses':[]}}
  self.assertFalse(missing([item],'125 mg 바이알의 안정성은?',{'english_query':'125 mg vial stability'}))
  item['conditions']=[{'dimension':'dose','quote':'Dose is 371 mg','evidence':'E1'}]
  self.assertFalse(explicit_conflicts([item],'125 mg 바이알 수는?',{'english_query':'number of 125 mg vials'}))
  item['numeric_question_role_contract']['administered_doses']=[{'number':'137','unit':'mg'}]
  self.assertTrue(explicit_conflicts([item],'137 mg이라고 명시한 원문',{'english_query':''}))

if __name__=='__main__':unittest.main()
