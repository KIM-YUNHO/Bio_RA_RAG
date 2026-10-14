import unittest
from copy import deepcopy
from rag.preprocessed_studies import annotate


class StudyCandidates(unittest.TestCase):
    def setUp(self):
        self.units = {'h': {'unit_id': 'h', 'document_id': 'd', 'version_id': 'v',
            'text': 'Clinical Phase I study (SB17-1001)', 'source_refs': [{'pdf_page': 37, 'sha256': 's'}]},
            'b': {'unit_id': 'b', 'document_id': 'd', 'version_id': 'v',
            'text': 'The primary endpoint was Cmax.', 'source_refs': [{'pdf_page': 39, 'sha256': 's'}]}}
        self.c = {'product_id': 'SB17', 'document_id': 'd', 'version_id': 'v', 'text': self.units['b']['text'],
            'header': 'unchanged', 'provenance': {'source_spans': [{'unit_id': 'b',
            'representation_start': 0, 'representation_end': len(self.units['b']['text'])}]},
            'structure': {'heading_anchors': [{'study_keys': ['ST_SB17_1001'],
            'heading_location': {'block_id': 'h'}}]}}

    def test_section_candidate_has_exact_source_anchor_and_preserves_embedding_input(self):
        got = annotate(self.c, self.units)
        self.assertEqual(got['studies'], ['ST_SB17_1001'])
        self.assertEqual(got['study_candidates']['anchors'][0]['quote'], 'SB17-1001')
        self.assertEqual(got['study_candidates']['anchors'][0]['source_refs'][0]['pdf_page'], 37)
        self.assertEqual((got['text'], got['header']), (self.c['text'], self.c['header']))
        self.assertNotIn('studies', self.c)

    def test_semantic_heading_and_old_assignment_cannot_invent_identifiers(self):
        self.units['h']['text'] = 'Healthy subject pharmacokinetic study'
        self.c['studies'] = ['ST_SB17_3001']
        self.assertEqual(annotate(self.c, self.units)['studies'], [])

    def test_literal_mentions_are_candidates_even_when_multiple_or_negated(self):
        text = 'Not SB17-1001; contextual comparison with SB17-3001.'
        self.units['b']['text'] = text
        self.c['provenance']['source_spans'][0]['representation_end'] = len(text)
        got = annotate(self.c, self.units)
        self.assertEqual(got['study_scope'], 'multi')
        self.assertEqual(got['study_attribution_status'], 'heuristic_unverified')

    def test_source_identity_and_range_mismatch_fail(self):
        self.units['h']['version_id'] = 'other'
        with self.assertRaises(ValueError): annotate(self.c, self.units)
        self.units['h']['version_id'] = 'v'
        self.c['provenance']['source_spans'][0]['representation_end'] = 999
        with self.assertRaises(ValueError): annotate(self.c, self.units)

    def test_only_used_source_range_can_add_literal_mention(self):
        self.units['b']['text'] += ' SB17-3001'
        self.c['structure']['heading_anchors'] = []
        self.assertEqual(annotate(self.c, self.units)['studies'], [])

    def test_ctp43_parts_remain_distinct_and_pooled_is_not_inferred(self):
        text='CT-P43 1.1 Part 2 and CT-P43 1.2 were compared.'
        self.c['product_id']='CTP43';self.c['structure']['heading_anchors']=[]
        self.units['b']['text']=text
        self.c['provenance']['source_spans'][0]['representation_end']=len(text)
        got=annotate(self.c,self.units)
        self.assertEqual(got['studies'],['ST_CTP43_1_1_P2','ST_CTP43_1_2'])
        self.assertNotIn('ST_CTP43_POOLED',got['studies'])


if __name__ == '__main__': unittest.main()
