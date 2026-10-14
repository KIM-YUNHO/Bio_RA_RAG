"""Shared evidence adapter: exact locations and restrictions, no model calls."""
import json
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import numpy as np

import test_review_viewer
from rag.preprocessed_evidence import PreprocessedEvidence, grounded_answer, local_base, validate_shape


class EvidenceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_review_viewer.ViewerTests('test_pages_assets_and_chunks_preserve_same_source_and_review_state')
        self.fixture.setUp();self.bundle=self.fixture.bundle
        self.chunks=[json.loads(line) for line in (self.bundle.candidate/'chunks.jsonl').read_text(encoding='utf-8').splitlines()]
        self.adapter=PreprocessedEvidence(self.bundle,self.chunks,self.bundle.chunks_sha256,'http://127.0.0.1:8766')

    def tearDown(self):self.fixture.tearDown()

    def test_full_chunks_preserve_review_state_and_source_locations(self):
        evidence=self.adapter.evidence(self.chunks,np.arange(len(self.chunks)))
        self.assertTrue(evidence)
        for block in evidence.values():
            self.assertEqual(block['text'],self.adapter.by_id[block['chunk_id']]['text'])
            self.assertFalse(block['representation']['numeric_evidence_eligible'])
            for link in block['review_links']:
                params=parse_qs(urlsplit(link['url']).fragment)
                self.assertEqual(params['index'],[self.bundle.chunks_sha256])
                self.assertEqual(self.bundle.assets[params['asset'][0]]['pdf_page'],int(params['page'][0]))

    def test_excluded_hit_cannot_enter_through_context(self):
        self.assertEqual(self.adapter.evidence(self.chunks,[]),{})

    def test_budget_excludes_whole_table(self):
        with patch('rag.preprocessed_evidence.EVIDENCE_CHARS',1):
            evidence=self.adapter.evidence(self.chunks[:1],np.arange(len(self.chunks)))
        self.assertEqual(evidence,{})
        self.assertEqual(self.adapter.last_omitted[0]['reason'],'whole_chunk_exceeds_answer_context_budget')

    def test_quote_links_select_the_actual_span(self):
        assets=sorted(self.bundle.assets.values(),key=lambda asset:asset['pdf_page'])
        chunk={'text':'first\nsecond','provenance':{'source_spans':[
            {'unit_id':assets[0]['asset_id'],'separator_before':'','representation_start':0,'representation_end':5},
            {'unit_id':assets[-1]['asset_id'],'separator_before':'\n','representation_start':0,'representation_end':6}]}}
        links=self.adapter.links(chunk,'second')
        self.assertEqual(len(links),1);self.assertEqual(links[0]['pdf_page'],2)

    def test_unverified_model_figure_numbers_and_free_prose_are_removed(self):
        block={'text':'The concentration was 12 ng/mL.','structure':{'kind':'figure_candidate'},'representation':{'origins':{'x':'qwen'}}}
        item={'study':'none','item':'value','source_label':'Concentration','value':'12 ng/mL','quote':block['text'],'exact':True,'evidence':['E1']}
        out={'status':'answered','items':[item],'answer_ko':'Also 999 subjects.'}
        checked=grounded_answer(out,{'E1':block})
        self.assertEqual(checked['items'],[]);self.assertNotIn('12',checked['answer_ko']);self.assertNotIn('999',checked['answer_ko'])
        block['structure']['kind']='text'
        checked=grounded_answer(out,{'E1':block})
        self.assertEqual(len(checked['items']),1);self.assertNotIn('999',checked['answer_ko'])

    def test_invalid_model_shape_remote_urls_and_mismatched_versions_fail(self):
        for value in [None,{}, {'status':'answered','items':[{}],'answer_ko':'x'}]:
            with self.assertRaises(ValueError):validate_shape(value)
        for value in ['https://example.org','http://localhost:8766/path','http://user:pass@127.0.0.1:8766']:
            with self.assertRaises(ValueError):local_base(value)
        with self.assertRaises(ValueError):PreprocessedEvidence(self.bundle,self.chunks,'wrong','http://127.0.0.1:8766')


if __name__=='__main__':unittest.main()
