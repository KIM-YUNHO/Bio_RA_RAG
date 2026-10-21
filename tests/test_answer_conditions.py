import unittest
from rag.evidence.answer_conditions import validate, label, explicit_conflicts


class Conditions(unittest.TestCase):
    def test_direction_is_displayed_as_source_and_eligibility_has_own_citation(self):
        evidence = {'E1':{'text':'SB17/EU sourced Stelara'},'E2':{'text':'Week 28 and considered eligible'}}
        item = {'source_label':'SB17 대비 EU', 'evidence':['E1'],'conditions':[
            {'dimension':'comparison','value':'SB17/EU sourced Stelara','quote':evidence['E1']['text'],'evidence':'E1'},
            {'dimension':'eligibility','value':'considered eligible','quote':evidence['E2']['text'],'evidence':'E2'}]}
        self.assertEqual(validate([item],evidence),[])
        self.assertIn('비교(원문): SB17/EU',label(item))
        self.assertIn('적격 조건: considered eligible',label(item))
        self.assertEqual(item['evidence'],['E1'])
        self.assertEqual(item['condition_evidence'],['E1','E2'])

    def test_unquoted_or_foreign_condition_is_removed(self):
        item = {'evidence':['E1'],'conditions':[{'dimension':'arm','value':'US','quote':'US','evidence':'E1'}]}
        self.assertEqual(len(validate([item],{'E1':{'text':'EU'}})),1)
        self.assertEqual(item['conditions'],[])

    def test_quote_only_condition_copies_printed_direction_without_translation(self):
        item = {'source_label':'translated label', 'evidence':['E1'], 'conditions':[
            {'dimension':'comparison','quote':'Pyzchiva/EU-Stelara','evidence':'E1'}]}
        self.assertEqual(validate([item],{'E1':{'text':'Ratio: Pyzchiva/EU-Stelara'}}),[])
        self.assertEqual(item['conditions'][0]['value'],'Pyzchiva/EU-Stelara')
        self.assertFalse(item['conditions'][0]['semantic_approval'])
        self.assertEqual(label(item),'비교(원문): Pyzchiva/EU-Stelara')

    def test_missing_comparison_has_neutral_label(self):
        self.assertEqual(label({'source_label':'EU vs SB17', 'unresolved_conditions':['comparison']}),
            '인용값 (비교 조건 미확정)')

    def test_literal_dose_and_analysis_conflicts_do_not_accept_neighbouring_results(self):
        items=[{'conditions':[{'dimension':'dose','quote':'45 mg','evidence':'E1'}]},
            {'conditions':[{'dimension':'analysis','quote':'Post-hoc analysis','evidence':'E2'}]}]
        conflicts=explicit_conflicts(items,'90 mg primary analysis',{})
        self.assertEqual({c['item_index'] for c in conflicts},{0,1})
        self.assertEqual(explicit_conflicts(items,'45 mg post-hoc analysis',{}),[])

    def test_validated_dosing_intervals_and_route_are_visible(self):
        item={'source_label':'투여', 'conditions':[
            {'dimension':'route','quote':'subcutaneous','evidence':'E1'},
            {'dimension':'dosing_interval','quote':'initially, 4 weeks later, then every 12 weeks','evidence':'E1'}]}
        self.assertFalse(validate([item],{'E1':{'text':'subcutaneous initially, 4 weeks later, then every 12 weeks'}}))
        self.assertIn('4 weeks later',label(item));self.assertIn('every 12 weeks',label(item))


if __name__ == '__main__': unittest.main()
