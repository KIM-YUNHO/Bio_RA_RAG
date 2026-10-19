"""A guideline-only plan whose guidance search finds nothing in scope reads the in-scope product documents."""
from types import SimpleNamespace
import unittest

import numpy as np

from rag import ra_retrieval


def chunk(n, text, doc_group='quality'):
    return {'chunk_id': f'c{n}', 'text': text, 'source': 'FDA', 'jurisdiction': 'US', 'product_id': 'ABP654',
            'document_id': 'FDA:ChemR', 'version_id': 'v1', 'doc_group': doc_group, 'pdf_page': n,
            'document_metadata': {'default_search': True}, 'structure': {}}


class Bm:
    def hits(self, query, idx, top_n=40):
        return [(1.0 / (n + 1), i) for n, i in enumerate(idx)][:top_n]


class NoGuidance:
    def search(self, queries, agencies, documents, k):
        return []


class GuidelineFallback(unittest.TestCase):
    def setUp(self):
        chunks = [chunk(1, 'Life-Cycle Considerations: Established Conditions were not proposed under ICH Q12.'),
                  chunk(2, 'Microbiology recommends approval.')]
        self.corpus = SimpleNamespace(chunks=chunks, vec=np.eye(2, dtype='float32'),
                                      encode=lambda qs: np.ones((len(qs), 2), dtype='float32'))
        self.plan = {'source_question': 'Life-Cycle Considerations에서 Established Conditions 설정 여부는?',
                     'products': ['ABP654'], 'jurisdictions': ['US'],
                     'tasks': [{'id': 'T1', 'request': 'Established Conditions 설정 여부', 'source_roles': ['guideline'],
                                'queries': ['ICH Q12 Established Conditions Life-Cycle Considerations']}]}
        self.picked = {('FDA:ChemR', 'v1')}

    def retrieve(self, profile):
        return ra_retrieval._retrieve(self.corpus, self.plan, NoGuidance(), Bm(), documents=self.picked, k=8,
                                      topic_queries=False, profile={'rerank': False, **profile})

    def test_switched_off_keeps_the_guideline_only_plan(self):
        groups = self.retrieve({'guideline_fallback': False})
        self.assertEqual([g['doc_groups'] for g in groups], [['guideline']])
        self.assertEqual(groups[0]['hits'], [])

    def test_empty_guidance_reads_the_picked_product_document(self):
        self.assertTrue(ra_retrieval.DEFAULT_PROFILE['guideline_fallback'])  # on by default on this branch
        groups = self.retrieve({})
        product = [g for g in groups if g['product'] == 'ABP654']
        self.assertEqual(len(product), 1)
        self.assertEqual(product[0]['fallback'], 'guideline_search_empty_product_documents_in_scope')
        self.assertEqual({h['chunk']['chunk_id'] for h in product[0]['hits']}, {'c1', 'c2'})
        self.assertEqual(groups[-1]['doc_groups'], ['guideline'])  # the guideline group is still reported

    def test_guidance_found_means_no_fallback(self):
        class Guidance(NoGuidance):
            def search(self, queries, agencies, documents, k):
                return [(1.0, {'chunk_id': 'g1', 'text': 'ICH Q12', 'role': 'guideline'})]
        groups = ra_retrieval._retrieve(self.corpus, self.plan, Guidance(), Bm(), documents=self.picked, k=8,
                                        topic_queries=False, profile={'rerank': False, 'guideline_fallback': True})
        self.assertEqual([g['doc_groups'] for g in groups], [['guideline']])

    def test_a_question_about_a_guideline_is_not_answered_from_product_documents(self):
        # Saved service question: the EMA guideline was not in scope, so "not found" is the honest answer.
        for question in ['EMA 바이오시밀러 가이드라인은 비교 임상 효능시험이 언제 필요하다고 권고해?',
                         'ICH Q12 지침은 Established Conditions를 어떻게 설명하나?',
                         'What does the FDA guidance recommend for Established Conditions?']:
            with self.subTest(question=question):
                self.plan['source_question'] = question
                groups = self.retrieve({'guideline_fallback': True})
                self.assertEqual([g['doc_groups'] for g in groups], [['guideline']])
                self.assertFalse(any(g.get('fallback') for g in groups))


if __name__ == '__main__':
    unittest.main()
