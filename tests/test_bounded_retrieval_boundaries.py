import unittest
from types import SimpleNamespace
from unittest.mock import patch
from rag.retrieval_candidate import retrieve
from rag.search import to_pages


class BoundedRetrievalBoundariesTests(unittest.TestCase):
    def chunks(self):
        return [{'chunk_id':str(i),'product_id':'SB17','jurisdiction':'EU','document_id':'allowed' if i<7 else 'excluded',
            'version_id':'v1','pdf_page':i+1,'content_hash':str(i),'doc_date':None,'source_url':None,'locator_type':'pdf_page',
            'doc_group':'assessment','document_type':'assessment','title':'Synthetic','section':None,'source_kind':'native',
            'redaction_markers':0,'text':'source '+str(i),'studies':[]} for i in range(10)]

    def test_added_role_pages_keep_established_pages_and_user_document_boundary(self):
        chunks=self.chunks();corpus=SimpleNamespace(chunks=chunks,vec=None,bm=None,include_nondefault=False,allowed_documents={('allowed','v1')})
        unit={'product':'SB17','jurisdiction':'EU','studies':[],'doc_groups':['assessment'],'topic':'gmr'}
        u={'source_question':'SB17 Cmax GMR 및 90% CI','search_terms':'Cmax ratio','english_query':'Cmax ratio'}
        established=to_pages([(100-i,i) for i in range(5)],chunks,5)
        visited=[]
        def role(contract,chunk):visited.append(chunk['document_id']);return 1 if chunk['pdf_page']>=6 else 0
        with patch('rag.retrieval_candidate.baseline_retrieve',return_value=[(unit,established)]), \
             patch('rag.retrieval_candidate.filter_idx',return_value=list(range(10))) as filtered, \
             patch('rag.retrieval_candidate.adaptive_hits',side_effect=lambda qv,q,idx,*a:[(1,i) for i in idx]), \
             patch('rag.population_counts.requested',return_value=False), \
             patch('rag.printed_pk.request',return_value=None), \
             patch('rag.source_event_scope.cues',return_value=[]), \
             patch('rag.budgeted_evidence.relevance',return_value=0), \
             patch('rag.request_role_retrieval.coverage_score',side_effect=role):
            out=retrieve(corpus,u,None,k=7)
        pages=out[0][1]
        self.assertEqual(pages[:5],established)
        self.assertEqual(len(pages),7)
        self.assertTrue(all(p['document_id']=='allowed' for p in pages))
        self.assertTrue(visited and all(d=='allowed' for d in visited))
        self.assertEqual(filtered.call_args.args[1:4],('SB17',None,'EU'))
        self.assertFalse(filtered.call_args.kwargs['include_nondefault'])

    def test_page_budget_is_finite(self):
        for budget in [4,11]:
            with self.assertRaises(ValueError):retrieve(None,{},None,k=budget)


if __name__=='__main__':unittest.main()
