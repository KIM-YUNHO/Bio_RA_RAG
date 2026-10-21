from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rag.ra.ra_research import extend


class ResearchTests(unittest.TestCase):
    def test_extension_preserves_baseline_and_excludes_out_of_scope_candidates(self):
        original={'E1':{'chunk_id':'baseline','text':'Original fact.'}}
        task={'id':'T1','request':'측정 대상','queries':['original query'],'source_roles':['assessment']}
        understanding={'tasks':[task],'products':['SB17'],'jurisdictions':['EU']}
        group={'task_id':'T1','product':'SB17','jurisdiction':'EU','hits':[]}
        row={'id':'R1_F1','facet':'assay_targets','part':None,'product':'SB17'}
        good={'chunk_id':'good','document_id':'d','version_id':'v','source':'EMA','product_id':'SB17',
              'jurisdiction':'EU','pdf_page':1,'text':'Assay measured serum concentration.'}
        foreign={**good,'chunk_id':'foreign','version_id':'other'}
        protected={**good,'chunk_id':'protected','document_metadata':{'evaluation_cohort':'held'}}
        candidates=[{**group,'hits':[{'chunk':c} for c in [foreign,protected,good]]}]
        corpus=SimpleNamespace(allowed_documents={('d','v')})
        client=SimpleNamespace(tokenizer=SimpleNamespace(one=lambda t:len(t)),input_limit=10000)
        before=deepcopy(original)
        with patch('rag.ra.ra_obligations.build',return_value=[row]),patch('rag.ra.ra_obligations.pointers',return_value={}), \
             patch('rag.ra.ra_retrieval.retrieve',return_value=candidates) as retrieval, \
             patch('rag.ra.ra_retrieval.packet',side_effect=lambda c,x,r:[(x,r)]), \
             patch('rag.ra.ra_retrieval.blocks',side_effect=lambda c,p:{'E1':p[0][0]}), \
             patch('rag.ra.ra_answer.messages',return_value=[]),patch('rag.ra.ra_answer.schema',return_value={}), \
             patch('rag.ra.ra_answer.cost',return_value=100):
            result,budget,trace=extend(corpus,[group],original,{},'question',understanding,client,None)
        self.assertEqual(original,before)
        self.assertEqual(result['E1'],original['E1'])
        self.assertEqual(result['E2']['chunk_id'],'good')
        self.assertEqual(len(trace['searches']),1)
        self.assertEqual(trace['additional_llm_calls'],0)
        self.assertFalse(trace['semantic_sufficiency_certified'])
        self.assertEqual(retrieval.call_args.kwargs['documents'],{('d','v')})

    def test_invalid_unbounded_round_count_is_rejected(self):
        with self.assertRaises(ValueError):extend(None,[],{},{} ,'',{},None,None,max_rounds=2)

    def test_disabled_research_cannot_invoke_retrieval(self):
        with patch('rag.ra.ra_obligations.build',return_value=[]), \
             patch('rag.ra.ra_obligations.pointers',return_value={}),patch('rag.ra.ra_retrieval.retrieve') as retrieval:
            result,budget,trace=extend(None,[],{},{} ,'',{},None,None,max_rounds=0)
        retrieval.assert_not_called()
        self.assertEqual(trace['stop_reason'],'disabled')

    def budget_run(self, *, text_size=20, candidates=1, cost=100, input_limit=10000):
        task={'id':'T1','queries':['original'],'source_roles':['assessment']}
        u={'tasks':[task],'products':['SB17'],'jurisdictions':['EU']}
        group={'task_id':'T1','product':'SB17','jurisdiction':'EU','hits':[]}
        row={'id':'R1_F1','facet':'assay_targets','part':None,'product':'SB17'}
        chunks=[{'chunk_id':f'c{i}','document_id':'d','version_id':'v','source':'EMA',
            'product_id':'SB17','jurisdiction':'EU','pdf_page':1,'text':'x'*text_size} for i in range(candidates)]
        corpus=SimpleNamespace(allowed_documents={('d','v')})
        client=SimpleNamespace(tokenizer=SimpleNamespace(one=len),input_limit=input_limit)
        with patch('rag.ra.ra_obligations.build',return_value=[row]),patch('rag.ra.ra_obligations.pointers',return_value={}), \
             patch('rag.ra.ra_retrieval.retrieve',return_value=[{**group,'hits':[{'chunk':c} for c in chunks]}]), \
             patch('rag.ra.ra_retrieval.packet',side_effect=lambda c,x,r:[(x,r)]), \
             patch('rag.ra.ra_retrieval.blocks',side_effect=lambda c,p:{'E1':p[0][0]}), \
             patch('rag.ra.ra_answer.messages',return_value=[]),patch('rag.ra.ra_answer.schema',return_value={}), \
             patch('rag.ra.ra_answer.cost',return_value=cost):
            return extend(corpus,[group],{'E1':{'chunk_id':'baseline','text':'Original'}},{},'question',u,client,None)

    def test_oversized_whole_source_is_not_clipped_or_admitted(self):
        result,_,trace=self.budget_run(text_size=1201)
        self.assertEqual(list(result),['E1'])
        self.assertEqual(trace['new_source_tokens'],0)

    def test_prompt_and_reserved_review_budget_blocks_addition(self):
        result,_,trace=self.budget_run(cost=9500,input_limit=10000)
        self.assertEqual(list(result),['E1'])
        self.assertFalse(trace['added_packets'])

    def test_packet_cap_stops_seventh_source(self):
        result,_,trace=self.budget_run(candidates=7)
        self.assertEqual(len(result),7)  # One unchanged original plus six new.
        self.assertEqual(len(trace['added_packets']),6)

    def test_source_budget_accumulates_across_candidates(self):
        result,_,trace=self.budget_run(text_size=700,candidates=2)
        self.assertEqual(len(result),2)
        self.assertEqual(trace['new_source_tokens'],700)


if __name__=='__main__':unittest.main()
