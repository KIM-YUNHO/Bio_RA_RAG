import unittest
from unittest.mock import patch
from rag import selection_candidate
from rag.source_fields import requirement
from rag.source_fields import eligibility_fields
from rag.treatment_identity import printed_arm
from rag.contract_relations import pairs
from rag.margin_scope import predicate


def evidence(text):
    return {'E1':{'text':text,'product_id':'SB17','document_id':'D','version_id':'V',
        'structure':{'section_id':'S','heading_anchors':[]},'source_spans':[]}}


class OwnSourceFieldsTests(unittest.TestCase):
    def test_catalog_identity_requires_own_region_and_product(self):
        with patch('rag.treatment_identity.references',return_value={'NEW':{'name':'BrandX','proper_name':'molecule-y'}}):
            for header,expected in [('molecule-y (EU) (N = 42) n (%)','EU-BrandX'),('US-licensed BrandX (N=73)','US-BrandX')]:
                self.assertEqual(printed_arm(header,'NEW'),expected)
            self.assertIsNone(printed_arm('molecule-y','NEW'))
            self.assertIsNone(printed_arm('molecule-y (EU)','OTHER'))
            self.assertIsNone(printed_arm('molecule-z (EU)','NEW'))
            self.assertIsNone(printed_arm('BrandX EU or US','NEW'))

    def test_catalog_pair_preserves_literal_region_order(self):
        with patch('rag.treatment_identity.references',return_value={'NEW':{'name':'BrandX','proper_name':'molecule-y'}}):
            p=pairs('Ratio molecule-y (US) / molecule-y (EU)','NEW')[0]
            self.assertEqual(p['values'],['US-BrandX','EU-BrandX'])
            self.assertEqual(p['operator'],'ordered_pair')
            self.assertEqual(p['quote'],'molecule-y (US) / molecule-y (EU)')
            self.assertFalse(pairs('mL/day/kg and mcg/mL','NEW'))

    def test_population_condition_is_not_the_requested_analyte(self):
        self.assertEqual(requirement('Day 77 NAb 양성률(ADA 양성자 중)은?')['analytes'],{'NAb'})
        self.assertEqual(requirement('ADA 및 NAb 양성률은?')['analytes'],{'ADA','NAb'})
        self.assertFalse(requirement('ADA와 NAb 분석법은?')['analytes'])

    def test_wrong_antibody_column_cannot_be_relabelled_by_model(self):
        t='<table><tr><th>Visit</th><th>ADA</th><th>NAb</th></tr><tr><td>Day 77</td><td>27.4%</td><td>8.1% (3/37)</td></tr></table>'
        c=selection_candidate.catalog(evidence(t),'Day 77 NAb 양성률은?')
        wrong=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='27.4%')
        right=next(x for x in c.values() if x['kind']=='table_cell' and x['value']=='8.1% (3/37)')
        self.assertFalse(wrong['value_eligible']);self.assertNotEqual(right.get('value_eligible'),False)
        self.assertEqual(right['own_field_role'],'NAb')
        self.assertTrue(all(x.get('value_eligible') is False for x in c.values() if x['kind']=='literal_number'))

    def test_baseline_boosted_cannot_answer_whole_period_including_baseline(self):
        t='<table><tr><th>Outcome</th><th>EU-Stelara</th></tr><tr><td>Binding antibody positive at baseline with a 5x postbaseline increase</td><td>3 (7.1)</td></tr></table>'
        for q in ['기저치 포함 전 기간 ADA 양성률은?','투여 후 ADA 양성률은?']:
            c=selection_candidate.catalog(evidence(t),q)
            self.assertFalse(next(x for x in c.values() if x['value']=='3 (7.1)')['value_eligible'])

    def test_wrong_visit_and_correct_equal_value_stay_distinct(self):
        t='<table><tr><th>Visit</th><th>NAb</th></tr><tr><td>Day 27</td><td>7.2%</td></tr><tr><td>Day 77</td><td>7.2%</td></tr></table>'
        c=selection_candidate.catalog(evidence(t),'Day 77 NAb 양성률은?')
        vals=[x for x in c.values() if x['kind']=='table_cell' and x['value']=='7.2%']
        self.assertEqual(len(vals),2);self.assertFalse(vals[0]['value_eligible']);self.assertNotEqual(vals[1].get('value_eligible'),False)
        self.assertNotEqual(vals[0]['id'],vals[1]['id'])

    def test_complete_eligibility_preserves_inequalities_tex_unit_and_subgroups(self):
        text='Healthy volunteers aged 21 to 48 years; weight ≥ 57 kg and ≤ 94 kg; BMI 19.2 to 28.7 kg/m $ ^{2} $ in subgroup A and 19.2 to 24.8 kg/m $ ^{2} $ in subgroup B.'
        c=selection_candidate.catalog(evidence(text),'선정 기준 연령 체중 BMI는?')
        scalars=[x for x in c.values() if x['kind']=='literal_number'];self.assertTrue(scalars)
        self.assertTrue(all(x['value_eligible'] is False for x in scalars))
        whole=next(x for x in c.values() if x['value']==text)
        self.assertEqual(whole['own_field_role'],'eligibility');self.assertNotEqual(whole.get('value_eligible'),False)

    def test_one_field_bounds_cannot_complete_a_truncated_second_field(self):
        self.assertEqual(eligibility_fields('Body weight between 57-94 kg and BMI between'),{'weight'})
        self.assertEqual(eligibility_fields('Age 21-48 years; body weight 57-94 kg; BMI 19.2-28.7 kg/m2'),{'age','weight','BMI'})
        self.assertFalse(eligibility_fields('BMI mean 24.7 with P-value0.973'))

    def test_closed_unique_inclusion_can_extract_whole_clauses_without_model(self):
        text='Study SB17-1001\n\nKey inclusion criteria\nSubjects must meet all criteria.\nHealthy adults aged21-48 years.\nBody weight57-94 kg and BMI19.2-28.7 kg/m2.\nKey exclusion criteria\nBMI below19.2 kg/m2.'
        text=text.replace('aged21','aged 21').replace('weight57','weight 57').replace('BMI19','BMI 19')
        e=evidence(text);q='SB17-1001 선정 기준 연령 체중 BMI는?';c=selection_candidate.catalog(e,q)
        chosen=selection_candidate.preselect(c,e,q,{'studies':['ST_SB17_1001']})
        self.assertIsNotNone(chosen)
        raw,proof=chosen;self.assertEqual(proof['fields'],['BMI','age','weight'])
        out=selection_candidate.resolve(raw,c,e,q,{'studies':['ST_SB17_1001']})
        self.assertTrue(any('57-94' in i['value'] and '19.2-28.7' in i['value'] for i in out['items']))
        self.assertFalse(any('below' in i['value'] for i in out['items']))

    def test_missing_field_two_inclusion_runs_and_unknown_study_do_not_autocomplete(self):
        text='Study SB17-1001\n\nKey inclusion criteria\nHealthy adults aged 21-48 years.\nBody weight57-94 kg.\nKey exclusion criteria\nBMI19.2-28.7 kg/m2.'
        e=evidence(text);q='SB17-1001 선정 기준 연령 체중 BMI는?';c=selection_candidate.catalog(e,q)
        self.assertIsNone(selection_candidate.preselect(c,e,q,{'studies':['ST_SB17_1001']}))
        text=text.replace('Key exclusion criteria','Key inclusion criteria').replace('weight57','weight 57').replace('BMI19','BMI 19')
        e=evidence(text);c=selection_candidate.catalog(e,q)
        self.assertIsNone(selection_candidate.preselect(c,e,q,{'studies':[]}))

    def test_eligibility_cannot_supply_design_arm_composition(self):
        t='<table><tr><th>Design</th><th>Population</th></tr><tr><td>Randomized double-blind 4-arm</td><td>Healthy subjects aged 21 to 48 years and weight 57 to 94 kg</td></tr></table>'
        c=selection_candidate.catalog(evidence(t),'시험 설계와 군 구성은?')
        pop=next(x for x in c.values() if 'Healthy subjects aged' in x['value'])
        self.assertFalse(pop['value_eligible'])
        self.assertNotEqual(next(x for x in c.values() if x['value']=='Randomized double-blind 4-arm').get('value_eligible'),False)

    def test_minimum_indication_age_is_not_a_composite_eligibility_request(self):
        self.assertFalse(requirement('허가 서신의 소아 적응증 최저 연령과 적응증은?')['eligibility'])
        c=selection_candidate.catalog(evidence('The product is indicated for pediatric patients aged 9 years and older with condition Y.'),'허가 소아 적응증의 최저 연령은?')
        self.assertNotEqual(next(x for x in c.values() if x['value']=='9 years').get('value_eligible'),False)

    def test_own_arm_count_suffix_and_analyte_qualifiers_added_without_values(self):
        t='<table><tr><th>Outcome</th><th>EU-Stelara (N=37)</th></tr><tr><td>At least one positive NAb after administration</td><td>8.1% (3/37)</td></tr></table>'
        e=evidence(t);q='투여 후 NAb 양성률(군별)은?';c=selection_candidate.catalog(e,q)
        v=next(x for x in c.values() if x['value']=='8.1% (3/37)')
        raw={'status':'answered','items':[{'study':'none','item':'ADA','source_label':'ADA','value_id':v['id'],'exact':True,'conditions':[]}]}
        item=selection_candidate.resolve(raw,c,e,q,{'studies':[]})['items'][0]
        self.assertEqual(item['value'],'8.1% (3/37)');self.assertIn('NAb',item['source_label'])
        self.assertTrue(any(x['dimension']=='arm' and x['value']=='EU-Stelara (N=37)' for x in item['conditions']))
        self.assertTrue(any(x['dimension']=='endpoint' and 'positive NAb' in x['value'] for x in item['conditions']))

    def test_be_criteria_limits_and_margin_are_not_confidence_levels(self):
        for text in ['The CIs must be within the BE criteria of 0.73 to 1.37.',
                     'Bioequivalence limits were 73-137%.',
                     'Equivalence margins of ±9% apply to the 87% CI.']:
            self.assertTrue(predicate(text))
        for text in ['The 87% confidence interval was estimated.', 'Response was73% to87%.',
                     'The equivalence margin uses an87% CI.']:
            self.assertFalse(predicate(text))

    def test_margin_field_does_not_discard_other_explicit_dose_or_route(self):
        c=selection_candidate.catalog(evidence('A single 37 mg subcutaneous dose was administered.\n\nThe equivalence limits were 73-137%.'),'투여 용량과 투여 경로 및 동등성 한계는?')
        self.assertNotEqual(next(x for x in c.values() if x['value']=='37 mg').get('value_eligible'),False)
        self.assertNotEqual(next(x for x in c.values() if x['kind']=='literal_term').get('value_eligible'),False)
