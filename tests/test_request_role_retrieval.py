import unittest
from rag.literal_requests import compile_requests
from rag.request_role_retrieval import coverage_score


class RequestRoleRetrievalTests(unittest.TestCase):
    def chunk(self,table,**extra):
        return {'text':table,'product_id':'SB17','document_id':'synthetic','version_id':'v1','pdf_page':1,**extra}

    def test_literal_estimate_ci_bundle_covers_two_requests_without_values_in_query(self):
        q=compile_requests('SB17의 Cmax GMR 및 90% CI를 알려줘')
        t='<table><tr><th>Endpoint</th><th>Ratio of LS Geometric Means (90% CI)</th></tr><tr><td>Cmax</td><td>1.31 (1.04, 1.63)</td></tr></table>'
        self.assertEqual(coverage_score(q,self.chunk(t)),2)
        self.assertEqual(coverage_score(q,self.chunk(t.replace('1.31 (1.04, 1.63)','0.72 (0.53, 0.98)'))),2)

    def test_source_roles_cannot_borrow_and_condition_from_other_row(self):
        q=compile_requests('SB17의 단백질 보정 ADA 음성 Cmax GMR을 알려줘')
        t='<table><tr><th>Analysis</th><th>Endpoint</th><th>GMR</th></tr><tr><td>Protein adjusted</td><td>Cmax</td><td>1.3</td></tr><tr><td>ADA negative</td><td>Cmax</td><td>1.2</td></tr></table>'
        weaker=coverage_score(q,self.chunk(t))
        self.assertGreater(weaker,0)
        self.assertLess(weaker,1)
        self.assertEqual(coverage_score(q,self.chunk(t.replace('Protein adjusted</td>','Protein adjusted &amp; ADA negative</td>'))),1)

    def test_wrong_endpoint_mean_and_unresolved_table_do_not_rank_as_gmr(self):
        q=compile_requests('SB17의 AUCinf GMR을 알려줘')
        for t in ['<table><tr><th>Endpoint</th><th>GMR</th></tr><tr><td>AUClast</td><td>1.1</td></tr></table>',
                  '<table><tr><th>Endpoint</th><th>Mean</th></tr><tr><td>AUCinf</td><td>1.1</td></tr></table>',
                  '<table><tr><th>Endpoint</th><th>GMR</th></tr><tr><td>AUCinf</td><td>1.1</td><td>2.2</td></tr></table>']:
            self.assertEqual(coverage_score(q,self.chunk(t)),0)

    def test_value_or_source_change_cannot_reuse_old_role_result(self):
        q=compile_requests('SB17의 AUCinf GMR을 알려줘')
        t='<table><tr><th>Endpoint</th><th>GMR</th></tr><tr><td>AUCinf</td><td>1.1</td></tr></table>'
        self.assertEqual(coverage_score(q,self.chunk(t)),1)
        self.assertEqual(coverage_score(q,self.chunk(t.replace('AUCinf','AUClast'),version_id='v2')),0)
        self.assertEqual(coverage_score(q,self.chunk(t,version_id='v2')),1)

    def test_unknown_comparison_is_partial_signal_and_unsupported_query_does_not_rank(self):
        q=compile_requests('SB17의 EU Stelara 대비 Cmax GMR을 알려줘')
        t='<table><tr><th>Endpoint</th><th>GMR</th></tr><tr><td>Cmax</td><td>1.1</td></tr></table>'
        self.assertGreater(coverage_score(q,self.chunk(t)),0)
        self.assertLess(coverage_score(q,self.chunk(t)),1)
        self.assertEqual(coverage_score(None,self.chunk(t)),0)


if __name__=='__main__':unittest.main()
