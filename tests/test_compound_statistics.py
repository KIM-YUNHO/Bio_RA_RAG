import unittest
from rag.source_selection import catalog
from rag.compound_statistics import apply

class CompoundStatisticTests(unittest.TestCase):
    def test_mean_sd_and_median_range_keep_complete_cell(self):
        for header,value in [('Mean (SD)','7.14 (2.218)'),('Median (range)','137 (23, 317)')]:
            e={'E1':{'text':'<table><tr><th>Parameter</th><th>'+header+'</th></tr><tr><td>Tmax (days)</td><td>'+value+'</td></tr></table>'}}
            c=catalog(e);apply(c,'Tmax 평균, SD와 범위')
            whole=next(v for v in c.values() if v['kind']=='table_cell' and v['value']==value)
            self.assertIsNot(whole.get('value_eligible'),False)
            children=[v for v in c.values() if v['kind']=='literal_number' and whole['id'] in v.get('context_ids',[])]
            self.assertTrue(children);self.assertTrue(all(v['value_eligible'] is False for v in children))
    def test_population_count_and_single_mean_remain_selectable(self):
        e={'E1':{'text':'<table><tr><th>Set</th><th>N (%)</th></tr><tr><td>PKS</td><td>23 (71%)</td></tr></table>'}}
        c=catalog(e);apply(c,'군별 PKS 인원');self.assertTrue(all('selection_exclusion' not in v for v in c.values()))
