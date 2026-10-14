"""The pilot must detect wrong cell associations, not just matching number sets."""
import unittest

from preprocess.quality_eval import table_checks


class LiteralEvaluationTests(unittest.TestCase):
    def test_swapped_columns_with_identical_numbers_fail(self):
        html='<table><tr><td>A</td><td>20</td><td>10</td></tr></table>'
        result=table_checks(html,[{'row_prefix':['A'],'cells':{'1':'10','2':'20'}}])
        self.assertFalse(any(c['passed'] for c in result))

    def test_interval_order_and_thousands_separator(self):
        html='<table><tr><td>A</td><td>4,958,100</td><td>1.08; 0.90</td></tr></table>'
        result=table_checks(html,[{'row_prefix':['A'],'cells':{'1':'4958100','2':'0.90; 1.08'}}])
        self.assertTrue(result[0]['passed'])
        self.assertFalse(result[1]['passed'])

    def test_spanning_visible_label_into_redaction_fails(self):
        html='<table><tr><td>A</td><td rowspan="2">Active substance (b) (4)</td></tr><tr><td>B</td></tr></table>'
        result=table_checks(html,[{'row_prefix':['B'],'cells':{},'redacted_columns':[1]}])
        self.assertFalse(result[0]['passed'])

    def test_ambiguous_row_prefix_cannot_assemble_values_from_two_rows(self):
        html='<table><tr><td>Group A</td><td>10</td><td>99</td></tr><tr><td>Group B</td><td>99</td><td>20</td></tr></table>'
        result=table_checks(html,[{'row_prefix':['Group'],'cells':{'1':'10','2':'20'}}])
        self.assertFalse(any(c['passed'] for c in result))

    def test_exact_row_label_does_not_accept_another_ingredient(self):
        html='<table><tr><td>Histidine hydrochloride</td><td>0.095 mg</td></tr></table>'
        result=table_checks(html,[{'row_prefix':['Histidine'],'exact_row_labels':True,'cells':{'1':'0.095 mg'}}])
        self.assertFalse(result[0]['passed'])


if __name__=='__main__':
    unittest.main()
