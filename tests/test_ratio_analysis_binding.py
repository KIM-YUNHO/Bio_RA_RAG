import unittest
from rag.contract_relations import attach_comparison
from rag.grounded_table_graph import build
from rag.answer_conditions import missing


class RatioAnalysisBindingTests(unittest.TestCase):
    def item(self):
        text='Table 31. Statistical assessment of PK parameters (ANCOVA with baseline weight)\n<table><tr><th>Endpoint</th><th>Ratio of Geometric Means 1</th></tr><tr><td>Cmax</td><td>1.18</td></tr></table>\n1 Ratio of geometric means SB17/EU-Stelara'
        e={'E1':{'text':text,'product_id':'SB17'}};g=build(e['E1']);n=next(n for n in g['tables'][0]['cells'] if n['value']=='1.18')
        c={'evidence':'E1','start':n['source_range'][0],'end':n['source_range'][1]}
        item={'source_label':'Model label','conditions':[]};attach_comparison(item,c,e,g)
        a,b=item['own_analysis_binding']['source_range']
        self.assertEqual(text[a:b],item['own_analysis_binding']['quote'])
        return item

    def test_unasked_primary_word_does_not_reject_source_bound_actual_analysis(self):
        item=self.item();u={'topic':'gmr','english_query':'Primary Cmax GMR'}
        self.assertFalse(missing([item],'SB17 Cmax GMR',u))
        self.assertEqual(item['own_analysis_binding']['primary_status'],'not_inferred')

    def test_explicit_primary_requirement_is_kept(self):
        item=self.item()
        for q in ['SB17 Cmax 주분석 GMR','SB17 Cmax primary analysis GMR']:
            self.assertIn('analysis',missing([item],q,{'topic':'gmr'})[0]['dimensions'])

    def test_foreign_or_missing_caption_proof_cannot_bypass_default_guard(self):
        item=self.item();item['own_analysis_binding']['quote']='foreign analysis'
        self.assertIn('analysis',missing([item],'SB17 Cmax GMR',{'topic':'gmr'})[0]['dimensions'])
        item.pop('own_analysis_binding')
        self.assertIn('analysis',missing([item],'SB17 Cmax GMR',{'topic':'gmr'})[0]['dimensions'])


if __name__=='__main__':unittest.main()
