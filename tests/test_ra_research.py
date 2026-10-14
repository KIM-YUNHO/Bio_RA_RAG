"""RA task/synthesis integration, support gaps and source boundary regressions."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch

import numpy as np
import test_review_viewer
from preprocess.hybrid_config import HybridConfig
from preprocess.ingest import sha256
from rag import ra_answer, ra_tasks
from rag.ra_pipeline import RAPipeline
from rag.ra_sources import build, GuidelineIndex
from rag import ra_retrieval


class Counter:
    def one(self, text): return len(text) // 4 + 1
    def messages(self, messages): return sum(self.one(m['content']) + 8 for m in messages)


def evidence():
    return {'E1': {'text': 'Study Alpha used a single 45mg SC dose. The applicant claimed similarity.',
                   'version_id': 'v1', 'product_id': 'SB17', 'document_metadata': {},
                   'structure': {'kind': 'text'}, 'representation': {'origins': {'a': 'native'}}},
            'E2': {'text': 'Study Beta used a single 90mg SC dose.', 'version_id': 'v2', 'product_id': 'ABP654',
                   'document_metadata': {}, 'structure': {'kind': 'text'},
                   'representation': {'origins': {'b': 'native'}}}}


TASK = {'id': 'T1', 'request': '투여 조건을 비교해줘', 'queries': ['dose route comparison'],
        'source_roles': ['assessment']}


def proposal(text='Alpha는 45mg, Beta는 90mg을 SC로 투여했다.'):
    return {'text': text, 'kind': 'source_fact', 'task_ids': ['T1'], 'products': ['SB17', 'ABP654'],
            'claim_basis': 'comparison_of_sources', 'sources': [
        {'evidence': 'E1', 'quote': 'Study Alpha used a single 45mg SC dose.'},
        {'evidence': 'E2', 'quote': 'Study Beta used a single 90mg SC dose.'}]}


class SupportTests(unittest.TestCase):
    def test_comparison_keeps_each_products_own_support(self):
        claims, rejected = ra_answer.literal_check({'claims': [proposal()], 'gaps': []}, evidence(), [TASK])
        self.assertEqual(len(claims), 1)
        self.assertFalse(rejected)
        self.assertEqual([c['evidence'] for c in claims[0]['sources']], ['E1', 'E2'])

    def test_joined_quotes_unavailable_sources_and_changed_numbers_are_rejected(self):
        for mutate in (lambda c: c['sources'][0].update(quote='Study Alpha ... 45mg'),
                       lambda c: c['sources'][0].update(evidence='E99'),
                       lambda c: c.update(text='Alpha는 135mg을 투여했다.')):
            c = proposal(); mutate(c)
            accepted, rejected = ra_answer.literal_check({'claims': [c], 'gaps': []}, evidence(), [TASK])
            self.assertFalse(accepted); self.assertEqual(len(rejected), 1)

    def test_pdf_whitespace_is_aligned_to_the_original_contiguous_span(self):
        blocks = evidence()
        blocks['E1']['text'] = 'Study Alpha used a single\r\n45mg SC dose. The applicant claimed similarity.'
        accepted, rejected = ra_answer.literal_check({'claims': [proposal()], 'gaps': []}, blocks, [TASK])
        self.assertFalse(rejected)
        cite = accepted[0]['sources'][0]
        self.assertIn('\r\n', cite['quote'])
        self.assertIn(cite['quote'], blocks['E1']['text'])
        self.assertEqual(cite['quote_alignment'], 'contiguous_source_span_whitespace_only')

    def test_each_claimed_product_requires_its_own_cited_source(self):
        c = proposal('SB17과 ABP654 두 시험 모두 90mg을 사용했다.')
        c['sources'] = c['sources'][1:]
        accepted, rejected = ra_answer.literal_check({'claims': [c], 'gaps': []}, evidence(), [TASK])
        self.assertFalse(accepted)
        self.assertEqual(rejected[0]['reason'], 'claim_product_not_supported_by_own_cited_source')
        c['products'] = ['ABP654']  # Wrong explicit scope cannot hide SB17 prose.
        accepted, rejected = ra_answer.literal_check({'claims': [c], 'gaps': []}, evidence(), [TASK])
        self.assertFalse(accepted)

    def test_partial_corpus_does_not_certify_absence(self):
        c = proposal('두 문서에 투여군이 보고되지 않았다.')
        c['claim_basis'] = 'absence_of_evidence'
        accepted, rejected = ra_answer.literal_check({'claims': [c], 'gaps': []}, evidence(), [TASK])
        self.assertFalse(accepted)
        self.assertEqual(rejected[0]['reason'], 'partial_retrieval_cannot_establish_document_absence')

    def test_escaped_table_newline_does_not_hide_a_numeric_cell(self):
        self.assertIn('90', ra_answer.numbers(r'comparator;\n90 mg SC'))

    def test_query_diversity_keeps_the_specific_querys_best_source(self):
        hits = ra_retrieval.diverse_hits([[(10, 1), (5, 3)], [(10, 2), (9, 3)]], 2)
        self.assertEqual([key for _, key in hits], [1, 2])

    def test_whitespace_alignment_does_not_repair_words_or_join_passages(self):
        text = 'The dose was 45 mg. Other conditions. The route was SC.'
        for quote in ('The dose was 90 mg.', 'The dose was 45 mg. The route was SC.', 'The dose was 45mg.'):
            self.assertIsNone(ra_answer.source_quote(text, quote))

    def test_reviewer_checks_surrounding_context_and_original_question(self):
        u = {'tasks': [TASK], 'products': ['SB17', 'ABP654'], 'jurisdictions': ['EU']}
        messages = ra_answer.review_messages('기관이 동등성을 인정했는가?', u, evidence(), [proposal()])
        payload = json.loads(messages[1]['content'])
        self.assertEqual(payload['original_question'], '기관이 동등성을 인정했는가?')
        self.assertIn('applicant claimed', payload['sources'][0]['source_text'])
        document = payload['sources'][1]['document']
        self.assertEqual(payload['document_metadata_not_source_text'][document]['product_id'], 'ABP654')

    def test_unsupported_claim_is_withheld_without_erasing_supported_claim(self):
        class Client:
            tokenizer = Counter(); input_limit = 50000
            def __call__(self, messages, json_schema=None, provider=None):
                if json_schema['name'] == 'ra_grounded_answer':
                    value = {'claims': [proposal(), proposal('기관이 두 시험의 동등성을 인정했다.')], 'gaps': []}
                else:
                    value = {'claims': [{'id': 'C1', 'verdict': 'supported', 'reason': 'Own trial doses'},
                                        {'id': 'C2', 'verdict': 'unsupported', 'reason': 'Only applicant claim'}],
                             'coverage': [{'task_id': 'T1', 'status': 'complete', 'reason': 'Both doses'}], 'gaps': []}
                return {'text': json.dumps(value)}
        result = ra_answer.generate('투여 조건을 비교해줘', {'tasks': [TASK]}, evidence(), Client())
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['claims']), 1)
        self.assertNotIn('기관이 두 시험', result['answer_ko'])
        self.assertFalse(result['validation']['expert_certified'])

    def test_semantic_review_cannot_silently_skip_a_claim(self):
        with self.assertRaises(ValueError):
            ra_answer.check_review({'claims': [], 'coverage': [], 'gaps': []}, [{'id': 'C1'}], [TASK])

    def test_uncited_reviewer_gap_facts_are_diagnostics_only(self):
        class Client:
            tokenizer = Counter(); input_limit = 50000
            def __call__(self, messages, json_schema=None, provider=None):
                if json_schema['name'] == 'ra_grounded_answer':
                    value = {'claims': [proposal()], 'gaps': []}
                else:
                    value = {'claims': [{'id': 'C1', 'verdict': 'supported', 'reason': 'Own trial doses'}],
                        'coverage': [{'task_id': 'T1', 'status': 'partial', 'reason': 'The study started in 2019.'}],
                        'gaps': ['The other comparator used 135mg.']}
                return {'text': json.dumps(value)}
        result = ra_answer.generate(TASK['request'], {'tasks': [TASK]}, evidence(), Client())
        self.assertEqual(result['gaps'], [TASK['request']])
        self.assertNotIn('135mg', result['answer_ko'])
        self.assertNotIn('2019', result['answer_ko'])
        self.assertIn('135mg', str(result['validation']['semantic_review']))

    def test_empty_retrieval_does_not_claim_document_wide_nonreporting(self):
        result = ra_answer.generate('질문', {'tasks': [TASK]}, {}, None)
        self.assertEqual(result['status'], 'not_found')
        self.assertIn('현재 검색 범위', result['answer_ko'])
        self.assertNotIn('미보고', result['answer_ko'])

    def test_guideline_role_requires_a_quote_from_the_original_request(self):
        class Client:
            def __call__(self, *args, **kwargs):
                return {'text': json.dumps({'intent': 'compare_products', 'products': ['SB17'],
                    'jurisdictions': [], 'jurisdiction_mentions': [], 'english_query': 'dose route',
                    'tasks': [{'request': '시험 조건 비교', 'queries': ['dose route'],
                               'source_roles': ['assessment', 'guideline'],
                               'guideline_request_quote': ''}]})}
        u, _ = ra_tasks.interpret('SB17 시험 조건을 비교해줘', Client(), ['SB17'], ['EU'])
        self.assertEqual(u['tasks'][0]['source_roles'], ['assessment'])
        self.assertIn('routing_adjustment', u['tasks'][0])


class GuidelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.raw = self.root / 'source.pdf'; self.raw.write_bytes(b'known source bytes')
        digest = sha256(self.raw)
        self.parsed = self.root / 'page.jsonl'
        page = {'document_id': 'GL:test', 'version_id': digest, 'pdf_page': 1,
                'text': 'The non-EEA reference product requires bridging evidence.', 'text_layer': {}}
        self.parsed.write_text(json.dumps(page) + '\n', encoding='utf-8')
        self.catalog = self.root / 'catalog.jsonl'
        self.doc = {'document_id': 'GL:test', 'version_id': digest, 'sha256': digest, 'role': 'guideline',
                    'source': 'EMA', 'raw_path': 'source.pdf', 'parsed_path': 'page.jsonl',
                    'page_count': 1, 'document_type': 'guideline', 'title': 'Bridging guideline',
                    'url': 'https://example.org/guideline.pdf', 'source_metadata': {'status': 'Draft', 'checked': '2026-10-07'}}
        self.catalog.write_text(json.dumps(self.doc) + '\n', encoding='utf-8')
        self.out = self.root / 'snapshot'

    def tearDown(self): self.temp.cleanup()

    def test_guideline_is_product_independent_and_worker_thread_safe(self):
        result = build(self.catalog, self.out, self.root)
        self.assertEqual(result['new_model_calls'], 0)
        index = GuidelineIndex(self.out); results = []
        worker = Thread(target=lambda: results.extend(index.search(['bridging reference'], ['EU'])))
        worker.start(); worker.join()
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0][1]['product_id'])
        self.assertEqual(results[0][1]['document_metadata']['status'], 'Draft')
        self.assertFalse(results[0][1]['document_metadata']['current_validity_verified'])
        self.assertFalse(index.search(['bridging'], ['US']))

    def test_changed_parser_or_raw_source_invalidates_snapshot(self):
        build(self.catalog, self.out, self.root)
        self.parsed.write_text('{}\n', encoding='utf-8')
        with self.assertRaises(ValueError): GuidelineIndex(self.out)

    def test_protected_guidelines_are_never_added(self):
        self.doc['evaluation_cohort'] = 'final_holdout'
        self.catalog.write_text(json.dumps(self.doc) + '\n', encoding='utf-8')
        with self.assertRaises(ValueError): build(self.catalog, self.out, self.root)
        self.assertFalse(self.out.exists())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_review_viewer.ViewerTests('test_pages_assets_and_chunks_preserve_same_source_and_review_state')
        self.fixture.setUp(); self.bundle = self.fixture.bundle
        chunks = [json.loads(line) for line in (self.bundle.candidate / 'chunks.jsonl').read_text(encoding='utf-8').splitlines()]
        for c in chunks: c.update(source='EMA', jurisdiction='EU', product_id='SB17')
        meta = {'dim': 2, 'n_chunks': len(chunks), 'truncated_inputs': 0,
                'chunks_sha256': self.bundle.chunks_sha256, 'model': 'BAAI/bge-m3'}
        self.index = (chunks, np.tile([1., 0.], (len(chunks), 1)), meta)
        self.patches = [patch('rag.pipeline.load_index', return_value=self.index),
                        patch('rag.answer.load_index', return_value=self.index)]
        for p in self.patches: p.start()
        class Encoder:
            def encode_queries(self, queries): return np.tile([1., 0.], (len(queries), 1))
        class Client:
            tokenizer = Counter(); input_limit = 50000; model = 'fake-local'; provider = 'ollama'; local_only = True
            def __init__(self): self.calls = []; self.schemas = []
            def __call__(self, messages, json_schema=None, provider=None):
                name = json_schema['name']; self.schemas.append(name)
                self.calls.append({'prompt_tokens': 10, 'completion_tokens': 5})
                if name == 'query_understanding':
                    value = {'intent': 'regulatory_reasoning', 'products': [], 'jurisdictions': [],
                        'jurisdiction_mentions': [], 'english_query': 'Group',
                        'tasks': [{'request': '군 제목을 설명해줘', 'queries': ['Group'], 'source_roles': ['assessment']}]}
                elif name == 'ra_grounded_answer':
                    payload = json.loads(messages[1]['content'])
                    source = next(s for s in payload['sources'] if 'Group' in s['source_text'])
                    value = {'claims': [{'text': '군 제목은 Group이다.', 'kind': 'source_fact', 'task_ids': ['T1'],
                        'products': ['SB17'], 'claim_basis': 'source_statement',
                        'sources': [{'evidence': source['id'], 'quote': 'Group'}]}], 'gaps': []}
                else:
                    value = {'claims': [{'id': 'C1', 'verdict': 'supported', 'reason': 'Printed heading'}],
                             'coverage': [{'task_id': 'T1', 'status': 'complete', 'reason': 'Answered'}], 'gaps': []}
                return {'text': json.dumps(value), 'prompt_tokens': 10, 'completion_tokens': 5}
        self.client = Client()
        self.pipeline = RAPipeline(bundle=self.bundle, config=HybridConfig(), chat_client=self.client, encoder_factory=Encoder,
                                   citation_mode='quote')

    def tearDown(self):
        self.pipeline.close()
        for p in reversed(self.patches): p.stop()
        self.fixture.tearDown()

    def test_reads_and_synthesizes_instead_of_invoking_numeric_selector(self):
        with patch('rag.answer.answer_unit', side_effect=AssertionError('Legacy selector must not run')):
            report = self.pipeline.ask('군 제목을 설명해줘', products=['SB17'], jurisdictions=['EU'])
        self.assertEqual(self.client.schemas, ['query_understanding', 'ra_grounded_answer', 'ra_support_review'])
        self.assertEqual(report['results'][0]['status'], 'answered')
        self.assertTrue(report['results'][0]['evidence'])
        self.assertEqual(report['usage'], {'input_tokens': 30, 'output_tokens': 15})
        self.assertEqual(self.client.calls, [])
        self.assertFalse(report['index']['active_index_updated'])

    def test_default_pipeline_uses_bound_spans_and_selected_review_in_three_calls(self):
        from rag import ra_review
        original = self.client
        class SpanClient:
            tokenizer = Counter(); input_limit = 50000; model = 'fake-local'; provider = 'ollama'; local_only = True
            def __init__(self): self.calls = []; self.schemas = []
            def __call__(self, messages, json_schema=None, provider=None):
                name = json_schema['name']; self.schemas.append(name)
                self.calls.append({'prompt_tokens': 10, 'completion_tokens': 5})
                if name == 'query_understanding':
                    return original(messages, json_schema, provider)
                data = json.loads(messages[1]['content'])
                if name == 'ra_request_grounded_answer':
                    alias = next(segment[0] for source in data['sources'] for segment in source['reading_segments']
                                 if segment[0] and 'Group' in segment[1])
                    value = {'request_results': {'R1': {'claims': [{'text': '군 제목은 Group이다.', 'kind': 'source_fact', 'task_ids': ['T1'],
                        'products': ['SB17'], 'claim_basis': 'source_statement', 'citation_span_ids': [alias]}], 'gaps': []}
                    }}
                elif name == 'ra_selected_claim_truth':
                    claim = data['proposed_claims'][0]
                    value = {'claims': {claim['id']: {'selected_support': 'supported', 'context_support': 'supported',
                        'clauses': [{**{k: claim[k] for k in ('text', 'kind', 'products', 'claim_basis')},
                                    'verdict': 'supported', 'final_span_ids': claim['selected_span_ids'], 'reason': 'Printed heading'}]}}}
                else:
                    value = {'clauses': {c['id']: {'verdict': 'supported', 'reason': 'Printed heading'} for c in data['clauses']},
                        'coverage': {'T1': {'status': 'complete', 'reason': 'Answered'}}, 'gaps': []}
                return {'text': json.dumps(value)}
        self.pipeline.close()
        encoder = self.pipeline.encoder_factory
        self.client = SpanClient()
        self.pipeline = RAPipeline(bundle=self.bundle, config=HybridConfig(), chat_client=self.client, encoder_factory=encoder)
        report = self.pipeline.ask('군 제목을 설명해줘', products=['SB17'], jurisdictions=['EU'])
        result = report['results'][0]
        self.assertEqual(self.client.schemas, ['query_understanding', 'ra_request_grounded_answer', 'ra_selected_claim_truth'])
        self.assertEqual(report['citation_mode'], 'spans')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['validation']['semantic_review']['version'], ra_review.VERSION)
        cite = result['claims'][0]['sources'][0]
        self.assertEqual(result['evidence'][cite['evidence']]['text'][slice(*cite['source_range'])], cite['quote'])

    def test_scope_unavailable_does_not_borrow_from_another_agency(self):
        report = self.pipeline.ask('군 제목을 설명해줘', products=['ABP654'], jurisdictions=['EU'])
        self.assertEqual(self.client.schemas, ['query_understanding'])
        self.assertEqual(report['status'], 'scope_unavailable')
        self.assertFalse(report['results'][0]['evidence'])

    def test_missing_guideline_connection_remains_an_explicit_scope_gap(self):
        class Plan:
            def __call__(self, messages, json_schema=None, provider=None):
                return {'text': json.dumps({'intent': 'regulatory_reasoning', 'products': [], 'jurisdictions': [],
                    'jurisdiction_mentions': [], 'english_query': 'reference bridging',
                    'tasks': [{'request': '브리징 지침 설명', 'queries': ['bridging'], 'source_roles': ['guideline'],
                               'guideline_request_quote': '지침'}]})}
        self.pipeline.client = Plan()
        # No product evidence may answer a guideline-only request.
        with patch('rag.answer.Corpus.encode', side_effect=AssertionError('No product embedding')):
            report = self.pipeline.ask('브리징 지침을 설명해줘', jurisdictions=['EU'])
        self.assertEqual(report['results'][0]['status'], 'not_found')
        self.assertEqual(report['plan'][0]['scope_gap'], 'guideline_snapshot_not_connected')

    def test_oracle_ids_cannot_bypass_product_or_evaluation_scope(self):
        from rag.answer import Corpus
        corpus = Corpus(bundle=self.bundle)
        corpus.allowed_documents = {(c['document_id'], c['version_id']) for c in corpus.chunks}
        u = {'products': ['ABP654'], 'jurisdictions': ['EU'], 'tasks': [TASK]}
        try:
            with self.assertRaises(ValueError):
                ra_retrieval.oracle_groups(corpus, u, None, [corpus.chunks[0]['chunk_id']], corpus.allowed_documents)
            u['products'] = ['SB17']
            corpus.chunks[0].setdefault('document_metadata', {})['evaluation_cohort'] = 'holdout'
            with self.assertRaises(ValueError):
                ra_retrieval.oracle_groups(corpus, u, None, [corpus.chunks[0]['chunk_id']], corpus.allowed_documents)
        finally:
            corpus.bm.db.close()

    def test_legacy_oracle_cannot_bypass_model_execution_denial(self):
        from eval.ra_research_eval import oracle_retrieval
        from rag.answer import Corpus
        corpus = Corpus(bundle=self.bundle)
        try:
            source = corpus.chunks[0]
            source.setdefault('document_metadata', {})['model_execution_allowed'] = False
            corpus.allowed_documents = {(source['document_id'], source['version_id'])}
            u = {'products': ['SB17'], 'jurisdictions': ['EU'], 'tasks': [TASK]}
            with self.assertRaises(ValueError):
                oracle_retrieval([source['chunk_id']])(corpus, u, None)
        finally:
            corpus.bm.db.close()

    def test_literal_heading_uses_its_own_page_locator(self):
        from rag.answer import Corpus
        corpus = Corpus(bundle=self.bundle)
        try:
            chunk = deepcopy(corpus.chunks[0])
            uid = chunk['provenance']['source_spans'][0]['unit_id']
            unit = self.bundle.units[uid]
            self.assertNotIn('pdf_page', unit)
            chunk['structure']['heading_anchors'] = [{'heading_location': {
                'block_id': uid, 'pdf_page': 1, 'representation_start': 0, 'representation_end': len(unit['text'])}}]
            chunk['text'] = 'Body passage on a later page.'
            parts = ra_retrieval.packet(corpus, chunk, 'retrieved')
            self.assertEqual(parts[0][0]['pdf_page'], 1)
            self.assertEqual(parts[0][0]['text'], unit['text'])
            self.assertEqual(parts[0][1], 'source_heading')
        finally:
            corpus.bm.db.close()


if __name__ == '__main__': unittest.main()
