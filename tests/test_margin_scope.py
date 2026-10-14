import unittest
from rag.source_selection import catalog
from rag.margin_scope import apply,finalize

class MarginScopeTests(unittest.TestCase):
    def test_ci_level_cannot_be_selected_as_a_prose_margin_value(self):
        evidence={'E1':{'text':'Equivalent efficacy was established if the 87% and 97% CIs were within predefined equivalence margins of ±7% and ±11%, respectively.'}}
        c=catalog(evidence);apply(c,'CI 수준별 동등성 한계를 알려줘')
        scalars=[v for v in c.values() if v['kind']=='literal_number' and '%' in v['value']]
        self.assertTrue(scalars);self.assertTrue(all(v['value_eligible'] is False for v in scalars))
        paragraphs=[v for v in c.values() if v.get('printed_margin_predicate')]
        self.assertTrue(paragraphs);self.assertIn('respectively',paragraphs[0]['value'])
        out={'status':'answered','items':[],'validation':{}}
        finalize(out,c,'동등성 한계');self.assertEqual(out['status'],'not_found')
        out={'status':'answered','items':[{'source_selection':{'value_id':paragraphs[0]['id']}}],'validation':{}}
        finalize(out,c,'동등성 한계');self.assertTrue(out['validation']['printed_margin_predicate']['covered'])
    def test_unrelated_percent_and_complete_table_values_are_unchanged(self):
        c=catalog({'E1':{'text':'Response rate was 81%.'}});apply(c,'반응률은?')
        self.assertTrue(all('selection_exclusion' not in v for v in c.values()))
    def test_unrelated_numeric_code_and_prose_cannot_substitute_for_margin(self):
        c=catalog({'E1':{'text':'Study AB12-01 supported similarity in healthy subjects.\n\nThe primary efficacy endpoint was mean response at Week 14.\n\nEquivalent efficacy required equivalence margins of ±8% and ±12% for the87% and97% CIs, respectively.'}})
        apply(c,'일차 평가변수 및 동등성 한계는?')
        self.assertFalse(any(v.get('value_eligible',True) for v in c.values() if v['kind']=='literal_number'))
        self.assertFalse(any(v.get('value_eligible',True) for v in c.values() if v['value'].startswith('Study AB12-01')))
        self.assertTrue(any(v.get('printed_primary_endpoint_predicate') and v['value_eligible'] for v in c.values()))
        c=catalog({'E1':{'text':'<table><tr><th>Margin</th><th>Criterion</th></tr><tr><td>Equivalence</td><td>75-135%</td></tr></table>'}})
        apply(c,'동등성 마진은?')
        self.assertTrue(all('selection_exclusion' not in v for v in c.values()))
