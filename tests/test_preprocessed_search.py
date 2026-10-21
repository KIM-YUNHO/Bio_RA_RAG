"""Search results retain restrictions on each preprocessed evidence chunk."""
import unittest
from copy import deepcopy
from test_rag_generalization import chunk
from rag.retrieval.search import to_pages, Bm25, literal_caption_hits


class PreprocessedSearchTests(unittest.TestCase):
    def test_explicit_caption_numbers_preserve_single_digits_and_exact_identity(self):
        self.assertIn('"Table 7"', Bm25.to_match('What is Table 7?'))
        self.assertIn('"Figure 3"', Bm25.to_match('Fig. 3 axes'))
        rows = []
        for title in ('Table 7. Results', 'Table 70. Other', 'Figure 7. Plot', '', 'Table 7.1. Subtable'):
            row = chunk(text='7 subjects'); row['structure'] = {'caption': title}
            rows.append(row)
        self.assertEqual(literal_caption_hits('Table 7', [0,1,2,3,4], rows), [(1.0,0)])
        self.assertEqual(literal_caption_hits('Table 7.1', [0,1,2,3,4], rows), [(1.0,4)])
        self.assertEqual(literal_caption_hits('Table 7', [1,2,3], rows), [])
        self.assertEqual(literal_caption_hits('serum concentration', [0,1,2,3], rows), [])

    def test_page_aggregation_preserves_distinct_visual_review_states(self):
        figure = chunk(text='Figure 3 caption')
        figure.update(schema_version='rag-chunk-3',
            representation={'numeric_evidence_eligible': False, 'description_review_required': True},
            provenance={'source_refs': [{'pdf_page': 1, 'bbox_pdf': [1, 2, 3, 4]}]},
            structure={'figure_id': 'figure3', 'member_unit_ids': ['panel1', 'panel2']})
        table = deepcopy(figure)
        table.update(chunk_id='table', text='table body', content_hash='table body')
        table['representation']['description_review_required'] = False
        table['structure'] = {'table_id': 'table4', 'member_unit_ids': ['table-region']}
        page = to_pages([(2., 0), (1., 1)], [figure, table])[0]
        evidence = page['representation_evidence']
        self.assertEqual(len(evidence), 2)
        self.assertTrue(evidence[0]['representation']['description_review_required'])
        self.assertEqual(evidence[0]['visual_parent']['member_unit_ids'], ['panel1', 'panel2'])
        self.assertFalse(evidence[1]['representation']['description_review_required'])
        self.assertTrue(all(not e['representation']['numeric_evidence_eligible'] for e in evidence))
        self.assertEqual(evidence[0]['source_refs'][0]['bbox_pdf'], [1, 2, 3, 4])

    def test_legacy_results_keep_existing_shape(self):
        self.assertNotIn('representation_evidence', to_pages([(1., 0)], [chunk()])[0])

    def test_collapsed_duplicates_keep_each_versions_restrictions(self):
        old = chunk(doc='old', version='v1', text='identical caption')
        old.update(schema_version='rag-chunk-3',
            representation={'numeric_evidence_eligible': False, 'description_review_required': True},
            provenance={'source_refs': [{'path': 'old.pdf'}]},
            structure={'figure_id': 'old-figure', 'member_unit_ids': ['old-panel']})
        new = deepcopy(old)
        new.update(document_id='new', version_id='v2', chunk_id='new:v2:1', doc_date='2026-01-02')
        new['representation']['description_review_required'] = False
        new['provenance']['source_refs'] = [{'path': 'new.pdf'}]
        page = to_pages([(2., 0), (1., 1)], [old, new])[0]
        self.assertEqual(page['document_id'], 'new')
        contracts = {e['version_id']: e for e in page['representation_evidence']}
        self.assertTrue(contracts['v1']['representation']['description_review_required'])
        self.assertFalse(contracts['v2']['representation']['description_review_required'])
        self.assertEqual(contracts['v1']['source_refs'], [{'path': 'old.pdf'}])
