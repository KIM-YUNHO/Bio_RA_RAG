import unittest
from rag import selection_candidate as s
from rag.dosing_coverage import slots,enforce
from rag.source_context import fact_role
from tests.test_selection_candidate import evidence

class CategoricalRoleTests(unittest.TestCase):
    def test_equivalent_comparators_share_coverage_without_losing_inclusivity(self):
        from rag.categorical_roles import weight_category_key
        self.assertEqual(weight_category_key('greater than 60 kg to 90 kg'),weight_category_key('more than 60 kg to 90 kg'))
        self.assertEqual(weight_category_key('>90 kg'),weight_category_key('over 90 kg'))
        self.assertNotEqual(weight_category_key('>90 kg'),weight_category_key('90 kg or more'))
        self.assertNotEqual(weight_category_key('less than 60 kg'),weight_category_key('60 kg or less'))
    def test_printed_weight_rows_require_every_dose_without_answer_constants(self):
        q='초기 정맥 투여 용량을 체중 구간별로 알려줘'
        e=evidence('Table 2. Initial intravenous dosing\n\n<table><tr><th>Weight</th><th>Dose</th></tr><tr><td>60 kg or less</td><td>240 mg</td></tr><tr><td>&gt;60 kg to 90 kg</td><td>360 mg</td></tr><tr><td>&gt;90 kg</td><td>480 mg</td></tr></table>')
        c=s.catalog(e,q);required=slots(c,e,q);self.assertEqual(3,len(required))
        self.assertEqual({'60 kg or less','>60 kg to 90 kg','>90 kg'},{r['population'] for r in required})
        out={'status':'answered','items':[{'source_selection':{'value_id':r['value_ids'][0]}} for r in required[:2]],'validation':{},'answer_ko':''}
        enforce(out,c,e,q);self.assertEqual('partial',out['status']);self.assertEqual(1,len(out['validation']['requested_dosing_coverage']['missing']))

    def test_parenthesized_dose_and_route_adverbs_keep_original_source(self):
        self.assertEqual('dose',fact_role({'kind':'literal_number','value':'(37 mg'}))
        e=evidence('The medicine was administered intravenously. Another arm received it subcutaneously.')
        c=s.catalog(e,'투여 경로는?')
        self.assertEqual({'intravenously','subcutaneously'},{v['value'] for v in c.values() if v['kind']=='literal_term'})
