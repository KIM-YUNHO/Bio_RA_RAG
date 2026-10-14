import unittest
from rag.source_selection import catalog,resolve
from rag.source_context import connected, comparison_coverage, enforce_comparison_coverage
from rag.answer_conditions import missing,explicit_conflicts

class LiteralContext(unittest.TestCase):
    def evidence(self,text):return {'E1':{'text':text,'document_id':'doc','version_id':'v','product_id':'SB17',
        'structure':{'section_id':'s','heading_anchors':[]},'source_spans':[]}}
    def select(self,evidence,value,question,study='none'):
        candidates=catalog(evidence,question,restrict_comparator=False);c=next(c for c in candidates.values() if c['value']==value)
        out=resolve({'status':'answered','items':[{'study':study,'item':'fact','source_label':'원문 값','value_id':c['id'],'exact':True,'conditions':[]}]},candidates,evidence,question,{'studies':[]})
        return out['items'][0]
    def test_original_mean_header_is_attached_without_changing_model_value(self):
        evidence=self.evidence('<table><tr><th>Endpoint</th><th>Median</th><th>Mean</th></tr><tr><td>Cmax (ng/mL)</td><td>3.1</td><td>3.7</td></tr></table>')
        item=self.select(evidence,'3.7','Cmax median mean')
        self.assertEqual(item['value'],'3.7');self.assertIn('Mean',[c['value'] for c in item['conditions'] if c['dimension']=='statistic'])
        self.assertNotIn('Median',[c['value'] for c in item['conditions'] if c['dimension']=='statistic'])
    def test_weight_sentence_keeps_inequality_and_entire_interval(self):
        text='Subcutaneous Adult Dosage Regimen\nFor patients weighing 80 kg or less, give 30 mg initially and 6 weeks later, then every 10 weeks.\nFor patients weighing more than 80 kg, give 60 mg.'
        item=self.select(self.evidence(text),'30 mg','Adults weighing 80 kg or less dose route and dosing interval')
        population=[c['value'] for c in item['conditions'] if c['dimension']=='population']
        self.assertTrue(any('80 kg or less' in s for s in population));self.assertFalse(any('more than' in s for s in population))
        self.assertTrue(any('every 10 weeks' in c['value'] for c in item['conditions'] if c['dimension']=='dosing_interval'))
    def test_wrong_literal_comparator_is_flagged_not_replaced(self):
        text='<table><tr><th>Arm</th><th>Ratio Drug/Reference</th></tr><tr><td>EU Refdrug</td><td>1.12</td></tr></table>'
        item=self.select(self.evidence(text),'1.12','US Refdrug 대비 GMR')
        self.assertEqual(item['value'],'1.12');self.assertEqual(item['source_context_conflicts'][0]['actual_printed'],'EU Refdrug')
    def test_gmr_mean_and_weight_covariate_are_not_independent_mean_or_weight_groups(self):
        item={'conditions':[{'dimension':d,'quote':'ANCOVA with weight covariate'} for d in ('comparison','endpoint','analysis')]}
        self.assertFalse(missing([item],'사후 ANCOVA(체중 공변량) Cmax 기하평균비',{'english_query':'Geometric mean ratio','studies':[]}))
        self.assertFalse(explicit_conflicts([item],'사후 ANCOVA Cmax GMR',{'english_query':''}))

    def test_endpoint_concentration_unit_does_not_become_ratio_unit(self):
        text='<table><tr><th>Endpoint</th><th>Ratio A/B</th></tr><tr><td>Cmax (ng/mL)</td><td>1.12</td></tr></table>'
        item=self.select(self.evidence(text),'1.12','Cmax GMR')
        self.assertFalse(any(c['dimension']=='unit' for c in item['conditions']))
        self.assertTrue(any(c['dimension']=='endpoint' for c in item['conditions']))

    def test_different_table_id_in_same_leaf_section_does_not_supply_conditions(self):
        a=self.evidence('Primary table')['E1'];b=self.evidence('Adjusted table')['E1']
        a['structure']['table_id']='primary';b['structure']['table_id']='adjusted'
        self.assertFalse(connected(a,b))

    def test_requested_reference_filters_only_unambiguous_wrong_data_cells(self):
        text='<table><tr><th>Arm</th><th>Ratio Drug/Reference</th></tr><tr><td>EU Refdrug</td><td>1.12</td></tr><tr><td>US Refdrug</td><td>1.23</td></tr></table>'
        values=[c['value'] for c in catalog(self.evidence(text),'US Refdrug 대비 GMR').values()]
        self.assertNotIn('1.12',values);self.assertIn('1.23',values);self.assertIn('EU Refdrug',values)

    def test_explicit_ratio_denominator_takes_priority_over_treatment_row(self):
        text='<table><tr><th>Arm</th><th>Ratio EU Refdrug /US Refdrug</th></tr><tr><td>EU Refdrug</td><td>1.12</td></tr></table>'
        values=[c['value'] for c in catalog(self.evidence(text),'US Refdrug 대비 GMR').values()]
        self.assertIn('1.12',values)

    def coverage_fixture(self):
        text='<table><tr><th>Endpoint</th><th>Arm</th><th colspan="2">Ratio Testdrug/Refdrug</th><th colspan="2">Ratio EU Refdrug/US Refdrug</th></tr><tr><th>Endpoint</th><th>Arm</th><th>Estimate</th><th>90% CI</th><th>Estimate</th><th>90% CI</th></tr><tr><td>AUCinf</td><td>US Refdrug</td><td>1.11</td><td>1.01, 1.21</td><td>1.22</td><td>1.02, 1.32</td></tr><tr><td>Cmax</td><td>US Refdrug</td><td>1.33</td><td>1.03, 1.43</td><td>1.44</td><td>1.04, 1.54</td></tr></table>'
        question='Testdrug trial US Refdrug 대비, EU Refdrug와 US Refdrug 간 AUCinf, Cmax GMR와 90% CI는?'
        candidates=catalog(self.evidence(text),question)
        values=['1.11','1.01, 1.21','1.33','1.03, 1.43','1.22','1.02, 1.32','1.44','1.04, 1.54']
        items=[{'value':v,'source_selection':{'value_id':next(c['id'] for c in candidates.values() if c['value']==v)}} for v in values]
        return question,candidates,items

    def test_answer_for_only_one_requested_comparison_is_partial_without_added_values(self):
        question,candidates,items=self.coverage_fixture()
        out={'status':'answered','items':items[:4],'answer_ko':'Original answer','validation':{}}
        enforce_comparison_coverage(out,candidates,question,'Testdrug')
        coverage=out['validation']['requested_comparison_coverage']
        self.assertEqual((coverage['covered_count'],coverage['required_count']),(4,8))
        self.assertEqual(out['status'],'partial');self.assertEqual(out['items'],items[:4])
        self.assertTrue(all(m['comparison']=='eu refdrug/us refdrug' for m in coverage['missing']))

    def test_all_original_cells_cover_both_comparisons_and_endpoints(self):
        question,candidates,items=self.coverage_fixture()
        coverage=comparison_coverage(items,candidates,question,'Testdrug')
        self.assertEqual(coverage['covered_count'],8);self.assertFalse(coverage['missing'])

    def test_duplicates_and_model_comparison_labels_cannot_fill_missing_original_cells(self):
        question,candidates,items=self.coverage_fixture()
        repeated=[{**i,'item':'EU/US AUCinf Cmax GMR CI'} for i in items[:4]]*2
        coverage=comparison_coverage(repeated,candidates,question,'Testdrug')
        self.assertEqual(coverage['covered_count'],4)

    def test_unasked_second_comparison_is_not_inferred_from_available_table(self):
        _,candidates,items=self.coverage_fixture()
        self.assertIsNone(comparison_coverage(items,candidates,'Testdrug versus US Refdrug AUCinf GMR CI','Testdrug'))

    def test_explicit_english_question_and_different_reference_are_respected(self):
        _,candidates,items=self.coverage_fixture()
        question='Testdrug versus US Refdrug and between EU Refdrug and US Refdrug: AUCinf Cmax GMR 90% CI'
        self.assertEqual(comparison_coverage(items,candidates,question,'Testdrug')['covered_count'],8)
        question=question.replace('versus US','versus EU')
        self.assertEqual(comparison_coverage(items,candidates,question,'Testdrug')['covered_count'],0)

    def test_absent_or_ambiguous_question_reference_does_not_invent_a_contract(self):
        _,candidates,items=self.coverage_fixture()
        self.assertIsNone(comparison_coverage(items,candidates,'Testdrug US Refdrug 대비 AUCinf GMR','Testdrug'))
        self.assertIsNone(comparison_coverage(items,candidates,'US Refdrug 대비, EU Refdrug와 US Refdrug 간 GMR AUCinf','Otherdrug'))

    def test_single_bound_from_ci_cell_does_not_cover_the_full_interval(self):
        question,candidates,items=self.coverage_fixture()
        ci=candidates[items[1]['source_selection']['value_id']]
        candidates['bound']={**ci,'id':'bound','kind':'literal_number','value':'1.01',
            'context_ids':[ci['id']]}
        items[1]={'value':'1.01','source_selection':{'value_id':'bound'}}
        coverage=comparison_coverage(items,candidates,question,'Testdrug')
        self.assertEqual(coverage['covered_count'],7)
        self.assertEqual(coverage['missing'],[{'comparison':'testdrug/us refdrug','endpoint':'AUCinf','statistic':'CI'}])

if __name__=='__main__':unittest.main()
