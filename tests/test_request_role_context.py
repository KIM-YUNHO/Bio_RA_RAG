import unittest
from unittest.mock import patch
from types import SimpleNamespace
from rag.budgeted_evidence import BudgetedEvidence
from rag.selection_candidate import context_score


class RequestRoleContextTests(unittest.TestCase):
    def test_matching_table_beats_same_page_summary_and_keeps_source_boundary(self):
        base={'product_id':'SB17','document_id':'synthetic','version_id':'v1','pdf_page':1,'structure':{}}
        summary={**base,'chunk_id':'summary','text':'Summary of the pharmacokinetic results, GMR and 90% confidence intervals.'}
        table={**base,'chunk_id':'table','text':'<table><tr><th>Endpoint</th><th>Ratio of Geometric Means (90% CI)</th></tr><tr><td>Cmax</td><td>1.27 (1.06, 1.54)</td></tr></table>'}
        excluded={**table,'chunk_id':'excluded','document_id':'foreign'}
        adapter=BudgetedEvidence.__new__(BudgetedEvidence)
        adapter.by_page={('synthetic','v1',1):[summary,table,excluded]}
        adapter.budget={};adapter.cue_fn=lambda *a:[]
        q='SB17 Cmax GMR 및 90% CI'
        adapter.context_role_score=lambda chunk:context_score(q,chunk)
        with patch('rag.budgeted_evidence.relevance',side_effect=lambda c,*a:100 if c['chunk_id']=='summary' else 0):
            selected=adapter.page_candidates([summary],{'summary','table'},q)
        self.assertEqual([c['chunk_id'] for c in selected],['table','summary'])
        self.assertEqual(selected[0]['text'],table['text'])

    def test_unsupported_original_request_keeps_the_existing_passage_order(self):
        base={'product_id':'SB17','document_id':'synthetic','version_id':'v1','pdf_page':1,'structure':{}}
        a={**base,'chunk_id':'a','text':'Summary'};b={**base,'chunk_id':'b','text':'<table><tr><th>Endpoint</th><th>GMR</th></tr><tr><td>Cmax</td><td>1.2</td></tr></table>'}
        adapter=BudgetedEvidence.__new__(BudgetedEvidence);adapter.by_page={('synthetic','v1',1):[a,b]};adapter.budget={};adapter.cue_fn=lambda *a:[]
        q='Cmax GMR에서 EU Stelara 제외';adapter.context_role_score=lambda chunk:context_score(q,chunk)
        with patch('rag.budgeted_evidence.relevance',side_effect=lambda c,*a:100 if c['chunk_id']=='a' else 1):
            self.assertEqual([c['chunk_id'] for c in adapter.page_candidates([a],{'a','b'},q)],['a','b'])

    def test_final_token_allocation_does_not_reorder_summary_ahead_of_matching_table(self):
        base={'product_id':'SB17','document_id':'synthetic','version_id':'v1','pdf_page':1,'structure':{'kind':'text'}}
        summary={**base,'chunk_id':'summary','text':'Summary of GMR results'}
        table={**base,'chunk_id':'table','text':'<table><tr><th>Endpoint</th><th>GMR</th></tr><tr><td>Cmax</td><td>1.27</td></tr></table>'}
        adapter=BudgetedEvidence.__new__(BudgetedEvidence);adapter.by_page={('synthetic','v1',1):[summary,table]}
        adapter.by_id={'summary':summary,'table':table};adapter.chunks=[summary,table];adapter.visual_bundle=None;adapter.bundle=SimpleNamespace(units={})
        adapter.context_role_score=lambda c:context_score('SB17 Cmax GMR',c)
        adapter.cue_fn=lambda q,c,*a:['broad summary hint'] if c['chunk_id']=='summary' else []
        adapter.blocks=lambda selected:{c['chunk_id']:c for c,r in selected}
        adapter.budget={'query':'SB17 Cmax GMR','counter':None,'core_tokens':100,'core_fits':lambda b:True,'fits':lambda b:len(b)<=1}
        with patch('rag.budgeted_evidence.relevance',return_value=1),patch('rag.budgeted_evidence.view',return_value=None):
            out=adapter.evidence([summary,table],[0,1])
        self.assertEqual(list(out),['table'])
        self.assertEqual(out['table']['text'],table['text'])
        self.assertEqual(adapter.last_omitted[0]['chunk_id'],'summary')


if __name__=='__main__':unittest.main()
