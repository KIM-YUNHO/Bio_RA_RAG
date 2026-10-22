from copy import deepcopy
import json
import unittest

from rag.ra import ra_answer, ra_spans


def evidence():
    parts = ['Study Alpha.', 'The dose was 45 mg SC.', 'No other results are supplied.']
    text = '\n'.join(parts)
    return {'E1': {'text': text, 'document_id': 'doc1', 'version_id': 'version1',
        'product_id': 'SB17', 'pdf_page': 2, 'document_metadata': {},
        'structure': {'kind': 'text'}, 'representation': {'origins': {'u1': 'native'}},
        'source_spans': [{'unit_id': f'u{n}', 'representation_start': 0, 'representation_end': len(part),
                          'separator_before': '' if n == 1 else '\n'} for n, part in enumerate(parts, 1)]}}


TASK = {'id': 'T1', 'request': '투여 조건', 'queries': ['dose'], 'source_roles': ['assessment']}


def claim(ids):
    return {'text': 'SB17은 45 mg SC를 사용했다.', 'kind': 'source_fact', 'products': ['SB17'],
            'task_ids': ['T1'], 'claim_basis': 'source_statement', 'citation_span_ids': ids}


class SpanTests(unittest.TestCase):
    def test_existing_units_reconstruct_complete_context_and_original_offsets(self):
        blocks = evidence(); spans, reading = ra_spans.catalog(blocks)
        self.assertEqual(''.join(s['text'] for s in reading['E1']), blocks['E1']['text'])
        self.assertEqual(len(spans), 3)
        resolved = ra_spans.resolve({'claims': [claim(['S1', 'S2'])], 'gaps': []}, blocks, spans)
        sources = resolved['claims'][0]['sources']
        self.assertEqual([s['quote'] for s in sources], ['Study Alpha.', 'The dose was 45 mg SC.'])
        self.assertEqual(sources[1]['source_range'], [13, 35])
        self.assertEqual(sources[1]['unit_id'], 'u2')
        self.assertNotIn('Study Alpha.\nThe dose', sources[0]['quote'])

    def test_stable_identity_survives_evidence_order_but_changes_with_source_version(self):
        blocks = evidence(); spans, _ = ra_spans.catalog(blocks)
        moved = {'E9': blocks['E1']}
        shifted, _ = ra_spans.catalog(moved)
        self.assertEqual(spans['S2']['span_id'], shifted['S2']['span_id'])
        moved['E9'] = deepcopy(moved['E9']); moved['E9']['version_id'] = 'version2'
        changed, _ = ra_spans.catalog(moved)
        self.assertNotEqual(spans['S2']['span_id'], changed['S2']['span_id'])

    def test_invalid_id_is_rejected_without_erasing_other_claims(self):
        blocks = evidence(); spans, _ = ra_spans.catalog(blocks)
        raw = {'claims': [claim(['S2']), claim(['S999'])], 'gaps': []}
        resolved = ra_spans.resolve(raw, blocks, spans)
        accepted, rejected = ra_answer.literal_check(resolved, blocks, [TASK])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected[0]['reason'], 'unknown_or_invalid_citation_span')

    def test_rebound_or_changed_source_cannot_reuse_a_citation(self):
        blocks = evidence(); spans, _ = ra_spans.catalog(blocks)
        blocks['E1']['text'] = blocks['E1']['text'].replace('45 mg', '90 mg')
        with self.assertRaises(ValueError): ra_spans.resolve({'claims': [claim(['S2'])]}, blocks, spans)

    def test_separators_and_unit_lengths_are_validated(self):
        for mutate in (lambda b: b['source_spans'][1].update(separator_before='X'),
                       lambda b: b['source_spans'][-1].update(representation_end=999)):
            blocks = evidence(); mutate(blocks['E1'])
            with self.assertRaises(ValueError): ra_spans.catalog(blocks)

    def test_html_cells_and_headers_remain_separate_and_fully_readable(self):
        text = '<table><tr><th>Arm</th><th>Dose</th></tr><tr><td>SB17</td><td>45 mg</td></tr></table>\nFootnote.'
        blocks = evidence(); blocks['E1']['text'] = text; blocks['E1'].pop('source_spans')
        spans, reading = ra_spans.catalog(blocks)
        self.assertEqual(''.join(s['text'] for s in reading['E1']), text)
        self.assertEqual(sum(s['kind'] == 'table_cell' for s in spans.values()), 4)
        self.assertTrue(any(s['quote'] == '<th>Dose</th>' for s in spans.values()))
        self.assertFalse(any('Dose</th>' in s['quote'] and '45 mg' in s['quote'] for s in spans.values()))

    def test_gap_marker_is_readable_but_not_selectable(self):
        blocks = evidence(); blocks['E1']['text'] = 'Statement.\n\n[UNSUPPLIED SOURCE GAP]\n\nOther statement.'
        blocks['E1'].pop('source_spans')
        spans, reading = ra_spans.catalog(blocks)
        self.assertFalse(any('[UNSUPPLIED SOURCE GAP]' in s['quote'] for s in spans.values()))
        self.assertIn('[UNSUPPLIED SOURCE GAP]', ''.join(s['text'] for s in reading['E1']))

    def test_existing_native_guideline_page_locator_requires_no_rebuild(self):
        text = 'Reference product selection.\n\nA scientific bridge is required.'
        block = {'text': text, 'document_id': 'GL1', 'version_id': 'v1', 'product_id': None,
            'role': 'guideline', 'chunk_id': 'GL1:v1:native:6', 'pdf_page': 6,
            'source_spans': [{'parsed_path': 'known.pages.jsonl', 'start': 0, 'end': len(text)}]}
        spans, reading = ra_spans.catalog({'E1': block})
        self.assertEqual(''.join(s['text'] for s in reading['E1']), text)
        self.assertEqual(spans['S2']['unit_id'], block['chunk_id'])
        self.assertEqual(spans['S2']['pdf_page'], 6)
        block['source_spans'][0]['end'] -= 1
        with self.assertRaises(ValueError): ra_spans.catalog({'E1': block})

    def test_span_prompt_and_schema_do_not_ask_for_generated_quotes(self):
        blocks = evidence(); u = {'tasks': [TASK]}
        messages = ra_answer.messages('투여 조건', u, blocks, 'spans')
        payload = json.loads(messages[1]['content'])
        self.assertNotIn('source_text', payload['sources'][0])
        self.assertEqual(payload['sources'][0]['reading_segments'][0], ['S1', 'Study Alpha.'])
        properties = ra_answer.schema(blocks, [TASK], 'spans')['schema']['properties']['claims']['items']['properties']
        self.assertIn('citation_span_ids', properties)
        self.assertNotIn('sources', properties)
        with self.assertRaises(ValueError): ra_answer.schema(blocks, [TASK], 'unknown')

    def test_original_numeric_and_semantic_checks_still_apply(self):
        class Tokens:
            def one(self, value): return len(value)
            def messages(self, value): return sum(len(m['content']) for m in value)
        class Client:
            tokenizer = Tokens(); input_limit = 100000
            def __call__(self, messages, json_schema=None, provider=None):
                if json_schema['name'] == 'ra_request_grounded_answer':
                    good = claim(['S2']); bad = claim(['S2']); bad['text'] = 'SB17은 90 mg SC를 사용했다.'
                    return {'text': json.dumps({'request_results': {'R1': {'claims': [good, bad], 'gaps': []}}})}
                if json_schema['name'] == 'ra_selected_support_review':
                    clauses = json.loads(messages[1]['content'])['clauses']
                    return {'text': json.dumps({'clauses': {c['id']: {'verdict': 'supported', 'reason': 'dose'} for c in clauses},
                        'coverage': {'T1': {'status': 'complete', 'reason': 'Dose provided'}}, 'gaps': []})}
                proposals = json.loads(messages[1]['content'])['proposed_claims']
                return {'text': json.dumps({'claims': {p['id']: {'selected_support': 'supported',
                    'context_support': 'supported', 'clauses': [{'text': p['text'], 'kind': p['kind'],
                    'products': p['products'], 'claim_basis': p['claim_basis'], 'verdict': 'supported',
                    'final_span_ids': p['selected_span_ids'], 'reason': 'Original dose'}]} for p in proposals},
                    })}
        result = ra_answer.generate('투여 조건', {'tasks': [TASK]}, evidence(), Client(), 'spans')
        self.assertEqual(result['claims'][0]['sources'][0]['quote'], 'The dose was 45 mg SC.')
        self.assertEqual(result['validation']['rejected_items'][0]['reason'], 'claim_number_not_in_final_citations')
        self.assertNotIn('90 mg', result['answer_ko'])
        self.assertEqual(result['status'], 'partial')

    def test_model_gap_prose_stays_out_of_the_answer(self):
        class Tokens:
            def one(self, value): return len(value)
            def messages(self, value): return sum(len(m['content']) for m in value)
        prose = 'Study Alpha는 심사기간 정지 이후 2020년에 중단되었다.'
        class Client:
            tokenizer = Tokens(); input_limit = 100000
            def __call__(self, messages, json_schema=None, provider=None):
                if json_schema['name'] == 'ra_request_grounded_answer':
                    return {'text': json.dumps({'request_results': {'R1': {'claims': [claim(['S2'])], 'gaps': [prose]}},
                        'derived': [{'label': 'SB17 투여 간격', 'operation': 'difference', 'operand_span_ids': ['S2', 'S3']}]})}
                proposals = json.loads(messages[1]['content'])['proposed_claims']
                return {'text': json.dumps({'claims': {p['id']: {'selected_support': 'supported',
                    'context_support': 'supported', 'clauses': [{'text': p['text'], 'kind': p['kind'],
                    'products': p['products'], 'claim_basis': p['claim_basis'], 'verdict': 'supported',
                    'final_span_ids': p['selected_span_ids'], 'reason': 'Original dose'}]} for p in proposals}})}
        result = ra_answer.generate('투여 조건', {'tasks': [TASK]}, evidence(), Client(), 'spans')
        self.assertIn('SB17은 45 mg SC를 사용했다.', result['answer_ko'])
        self.assertNotIn('2020년', result['answer_ko'])
        self.assertNotIn('모델이 밝힌', result['answer_ko'])
        self.assertNotIn('인용으로 완전히 확인하지 못한 요청', result['answer_ko'])
        self.assertIn('투여 조건', result['gaps'])
        self.assertEqual({'texts': [prose], 'shown_to_user': False, 'citation_verified': False},
                         result['validation']['model_gap_reasons'])
        self.assertEqual('derived_operand_not_in_verified_claims',
                         result['validation']['derived_withheld'][0]['reason'])
        self.assertIn('계산하지 않은 값 (계산할 두 값이 검증된 인용에 모두 있지 않음):\n- SB17 투여 간격', result['answer_ko'])

    def test_review_uses_exact_selected_quotes_and_complete_cited_context(self):
        blocks = evidence(); spans, _ = ra_spans.catalog(blocks)
        resolved = ra_spans.resolve({'claims': [claim(['S2'])], 'gaps': []}, blocks, spans)
        accepted, _ = ra_answer.literal_check(resolved, blocks, [TASK])
        messages = ra_answer.review_messages('투여 조건', {'tasks': [TASK]}, blocks, accepted, 'spans')
        payload = json.loads(messages[1]['content'])
        self.assertEqual(payload['original_question'], '투여 조건')
        self.assertEqual(payload['proposed_claims'][0]['sources'], [
            {'evidence': 'E1', 'quote': 'The dose was 45 mg SC.'}])
        self.assertNotIn('citation_span_ids', payload['proposed_claims'][0])
        self.assertIn('No other results are supplied.', str(payload['sources']))

    def test_unselected_sources_cannot_supply_facts_to_support_review(self):
        blocks = evidence(); blocks['E2'] = {**deepcopy(blocks['E1']), 'text': 'Distractor fact.', 'source_spans': []}
        spans, _ = ra_spans.catalog(blocks)
        resolved = ra_spans.resolve({'claims': [claim(['S2'])], 'gaps': []}, blocks, spans)
        accepted, _ = ra_answer.literal_check(resolved, blocks, [TASK])
        payload = json.loads(ra_answer.review_messages('투여 조건', {'tasks': [TASK]}, blocks, accepted, 'spans')[1]['content'])
        self.assertEqual([s['id'] for s in payload['sources']], ['E1'])
        self.assertEqual(payload['review_context_policy']['generation_evidence_blocks'], 2)
        self.assertEqual(payload['review_context_policy']['review_evidence_blocks'], 1)


if __name__ == '__main__':
    unittest.main()
