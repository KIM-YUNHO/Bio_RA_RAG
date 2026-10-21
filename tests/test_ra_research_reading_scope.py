"""Additional research must preserve the initial source-reading boundary."""
from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from rag.ra.ra_research import extend


class ReadingScopeResearchTests(unittest.TestCase):
    def run_scope(self, reading_roles):
        task={'id':'T1','request':'저장된 일반 지침의 상태와 범위',
              'queries':['guidance status scope'],'source_roles':['guideline','correspondence'],
              'reading_source_roles':reading_roles}
        understanding={'tasks':[task],'products':['SB17'],'jurisdictions':['EU']}
        row={'id':'R1_F1','facet':'document_status_scope','part':None,'product':None}
        product={'chunk_id':'product-letter','document_id':'product-doc','version_id':'v',
                 'source':'EMA','jurisdiction':'EU','product_id':'SB17','pdf_page':1,
                 'text':'A product approval letter.'}
        guideline={**product,'chunk_id':'guidance-status','document_id':'guide-doc',
                   'product_id':None,'role':'guideline','text':'Draft guidance. Scope of this guidance.'}
        groups=[{'task_id':'T1','product':c['product_id'],'jurisdiction':'EU',
                 'hits':[{'chunk':c}]} for c in (product,guideline)]
        corpus=SimpleNamespace(allowed_documents={('product-doc','v'),('guide-doc','v')})
        client=SimpleNamespace(tokenizer=SimpleNamespace(one=len),input_limit=10000)
        with ExitStack() as stack:
            mocks={'rag.ra.ra_obligations.build':{'return_value':[row]},
                   'rag.ra.ra_obligations.pointers':{'return_value':{}},
                   'rag.ra.ra_retrieval.retrieve':{'return_value':groups},
                   'rag.ra.ra_retrieval.packet':{'side_effect':lambda c,x,r:[(x,r)]},
                   'rag.ra.ra_retrieval.blocks':{'side_effect':lambda c,p:{'E1':p[0][0]}},
                   'rag.ra.ra_answer.messages':{'return_value':[]},
                   'rag.ra.ra_answer.schema':{'return_value':{}},
                   'rag.ra.ra_answer.cost':{'return_value':100}}
            for target,args in mocks.items():stack.enter_context(patch(target,**args))
            return extend(corpus,groups,{'E1':{'chunk_id':'baseline','text':'Original guidance.'}},
                          {},'question',understanding,client,None)

    def test_guideline_only_reading_excludes_product_candidates_from_research(self):
        result,_,trace=self.run_scope(['guideline'])
        self.assertEqual({b['chunk_id'] for b in result.values()},{'baseline','guidance-status'})
        self.assertEqual([p['chunk_id'] for p in trace['added_packets']],['guidance-status'])
        self.assertEqual(trace['additional_llm_calls'],0)

    def test_explicit_mixed_reading_keeps_permitted_product_precedents(self):
        result,_,_=self.run_scope(['guideline','correspondence'])
        self.assertEqual({b['chunk_id'] for b in result.values()},
                         {'baseline','guidance-status','product-letter'})


if __name__=='__main__':unittest.main()
