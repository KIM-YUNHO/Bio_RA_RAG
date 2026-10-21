import unittest
from rag.evidence.table_canonical import canonicalize


class CanonicalTable(unittest.TestCase):
    def run_case(self,table,value,quote):
        item={'value':value,'quote':quote,'evidence':['E1']}
        return canonicalize({'items':[item]},{'E1':{'text':table}})['items'][0]

    def test_mean_sd_and_column_unit_get_separate_source_ranges(self):
        row='<tr><td>本剤</td><td>5.095<br>±1.498</td></tr>'
        text='<table><tr><th>Arm</th><th>Cmax<br>(μg/mL)</th></tr>'+row+'</table>'
        item=self.run_case(text,'5.095 ±1.498 μg/mL',row)
        self.assertEqual(item['value'],'5.095 ±1.498')
        self.assertEqual(item['original_model_value'],'5.095 ±1.498 μg/mL')
        self.assertEqual({c['dimension'] for c in item['conditions']},{'unit','column_header','row_header'})
        self.assertEqual(item['table_value_derivation']['column'],1)

    def test_wrong_header_unit_or_cross_cell_value_cannot_be_rewritten(self):
        row='<tr><td>A</td><td>5.095</td><td>±1.498</td></tr>'
        text='<table><tr><th>Arm</th><th>AUC (μg·h/mL)</th><th>SD</th></tr>'+row+'</table>'
        item=self.run_case(text,'5.095 ±1.498 μg/mL',row)
        self.assertNotIn('table_value_derivation',item)


if __name__=='__main__':unittest.main()
