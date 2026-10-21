from copy import deepcopy
import json
import unittest

from rag.ra import ra_answer, ra_quantities, ra_review, ra_spans, ra_support

TASK = {'id': 'T1', 'request': '투여 조건 비교', 'queries': ['dose route'], 'source_roles': ['assessment']}


def blocks():
    parts = ['Study Alpha.', 'The 90% confidence interval was calculated.',
             'Subjects received a single 45 mg SC injection.', 'The trial was conducted in France.']
    return {'E1': {'text': '\n'.join(parts), 'document_id': 'd1', 'version_id': 'v1', 'product_id': 'SB17',
        'jurisdiction': 'EU', 'source_spans': [{'unit_id': str(n), 'representation_start': 0,
            'representation_end': len(t), 'separator_before': '' if n == 0 else '\n'} for n, t in enumerate(parts)],
        'structure': {}, 'document_metadata': {}, 'representation': {}}}


def proposal(evidence=None, text='SB17은 45 mg SC로 투여하였고 프랑스에서 시험을 진행하였다.', ids=('S1', 'S2'), products=('SB17',)):
    evidence = evidence or blocks()
    spans, _ = ra_spans.catalog(evidence)
    raw = {'claims': [{'text': text, 'kind': 'source_fact', 'products': list(products), 'task_ids': ['T1'],
                      'claim_basis': 'source_statement', 'citation_span_ids': list(ids)}], 'gaps': []}
    resolved = ra_spans.resolve(raw, evidence, spans)
    return ra_answer.literal_check(resolved, evidence, [TASK], check_numbers=False)[0]


def clause(text='SB17은 45 mg SC로 투여하였다.', ids=('S1', 'S3'), verdict='supported'):
    return {'text': text, 'kind': 'source_fact', 'products': ['SB17'], 'claim_basis': 'source_statement',
            'verdict': verdict, 'final_span_ids': list(ids), 'reason': 'source check'}


def review(clauses):
    return {'claims': {'C1': {'selected_support': 'insufficient', 'context_support': 'supported', 'clauses': clauses}},
            'coverage': {'T1': {'status': 'complete', 'reason': 'Checked'}}, 'gaps': []}


def expansion(raw, evidence, claims, tasks):
    return ra_review.apply(raw, evidence, claims, tasks, allow_expansion=True)


class BoundReviewTests(unittest.TestCase):
    def test_context_can_repair_citation_only_by_binding_its_actual_span(self):
        evidence = blocks()
        kept, rejected, result = expansion(review([clause()]), evidence, proposal(), [TASK])
        self.assertFalse(rejected)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]['citation_expansion'], ['S3'])
        self.assertIn('45 mg', kept[0]['sources'][-1]['quote'])
        self.assertEqual(kept[0]['sources'][-1]['source_range'], [57, 103])
        self.assertEqual(kept[0]['selected_support'], 'insufficient')

    def test_context_fact_without_final_citation_stays_rejected(self):
        kept, rejected, _ = expansion(review([clause(ids=('S1', 'S2'))]), blocks(), proposal(), [TASK])
        self.assertFalse(kept)
        self.assertEqual(rejected[0]['reason'], 'claim_number_not_in_final_citations')

    def test_percentage_cannot_substitute_for_a_dose(self):
        kept, rejected, _ = expansion(review([clause(text='SB17은 90 mg SC로 투여하였다.', ids=('S1', 'S2'))]),
                                           blocks(), proposal(), [TASK])
        self.assertFalse(kept)
        self.assertEqual(rejected[0]['reason'], 'claim_quantity_value_or_unit_not_in_final_citations')

    def test_independent_positive_clause_survives_an_unsupported_absence(self):
        absent = clause(text='135 mg IV 투여군은 문서 전체에 없다.', ids=(), verdict='unsupported')
        absent['claim_basis'] = 'absence_of_evidence'
        kept, rejected, _ = expansion(review([clause(), absent]), blocks(), proposal(), [TASK])
        self.assertEqual([c['id'] for c in kept], ['C1.1'])
        self.assertEqual([c['id'] for c in rejected], ['C1.2'])

    def test_word_number_and_date_representation_do_not_erase_supported_facts(self):
        c = {'text': '3자 비교를 실시하였다.', 'sources': [{'evidence': 'E1', 'quote': 'Compare all three products.'}], 'products': []}
        self.assertIsNone(ra_quantities.check(c, blocks()))
        self.assertIn('6', ra_quantities.numbers('16 June 2021'))
        self.assertNotIn('6', ra_quantities.numbers('in June'))

    def test_unknown_id_and_omitted_claims_are_not_accepted(self):
        kept, rejected, _ = expansion(review([clause(ids=('S999',))]), blocks(), proposal(), [TASK])
        self.assertFalse(kept)
        self.assertEqual(rejected[0]['reason'], 'unknown_or_missing_final_citation_span')
        raw = review([clause()]); raw['claims'] = {}
        with self.assertRaisesRegex(ValueError, 'every original claim'):
            expansion(raw, blocks(), proposal(), [TASK])

    def test_final_citations_cannot_transfer_another_products_fact(self):
        evidence = blocks(); evidence['E2'] = deepcopy(evidence['E1'])
        evidence['E2'].update(product_id='ABP654', document_id='d2', version_id='v2')
        spans, _ = ra_spans.catalog(evidence)
        other = [alias for alias, s in spans.items() if s['evidence'] == 'E2' and '45 mg' in s['quote']][0]
        parents = proposal(evidence)
        second = proposal(evidence, text='ABP654은 45 mg SC로 투여하였다.', ids=('S5', other), products=('ABP654',))[0]
        second['id'] = 'C2'; parents.append(second)
        raw = review([clause(ids=('S1', other))])
        second_clause = clause(text=second['text'], ids=('S5', other)); second_clause['products'] = ['ABP654']
        raw['claims']['C2'] = {'selected_support': 'supported', 'context_support': 'supported', 'clauses': [second_clause]}
        kept, rejected, _ = expansion(raw, evidence, parents, [TASK])
        self.assertEqual([c['parent_claim_id'] for c in kept], ['C2'])
        self.assertEqual(rejected[0]['reason'], 'final_citation_wrong_product')

    def test_required_keys_and_id_enums_prevent_omission_and_freeform_quotes(self):
        evidence = blocks(); claims = proposal()
        schema = ra_review.schema(evidence, claims, [TASK], allow_expansion=True)['schema']
        self.assertEqual(schema['properties']['claims']['required'], ['C1'])
        self.assertEqual(schema['properties']['coverage']['required'], ['T1'])
        properties = schema['properties']['claims']['properties']['C1']['properties']['clauses']['items']['properties']
        self.assertNotIn('quote', properties)
        self.assertEqual(properties['final_span_ids']['items'], {'$ref': '#/$defs/span_id'})
        payload = json.loads(ra_review.messages('투여 조건', {'tasks': [TASK]}, evidence, claims, allow_expansion=True)[1]['content'])
        self.assertIn('explicit_final_span_ids_required', str(payload))
        self.assertEqual(payload['sources'][0]['reading_segments'][2][0], 'S3')

    def test_review_output_budget_covers_three_clauses_a_claim(self):
        self.assertEqual(ra_answer.review_output_budget(6, 3000), 3000)
        # 9-12 claims ran out at 3000-3240 tokens (about 100 tokens a clause, about 3 clauses a claim).
        self.assertEqual(ra_answer.review_output_budget(9, 3000), 3840)
        self.assertEqual(ra_answer.review_output_budget(12, 3000), 4920)
        self.assertEqual(ra_answer.review_output_budget(40, 3000), 8000)

    def test_quantities_are_checked_against_their_own_products_in_a_comparison(self):
        evidence = {'E1': {'product_id': 'SB17'}, 'E2': {'product_id': 'ABP654'}}
        c = {'products': ['SB17', 'ABP654'], 'text': 'SB17은 90 mg, ABP654는 45 mg을 투여하였다.',
             'sources': [{'evidence': 'E1', 'quote': 'SB17 45 mg'}, {'evidence': 'E2', 'quote': 'ABP654 90 mg'}]}
        self.assertEqual(ra_quantities.check(c, evidence), 'claim_quantity_not_in_own_product_citations')
        c['text'] = 'SB17은 45 mg, ABP654는 90 mg을 투여하였다.'
        self.assertIsNone(ra_quantities.check(c, evidence))

    def test_runtime_split_and_selected_source_review_share_one_model_call(self):
        class Tokens:
            def one(self, x): return len(x)
            def messages(self, xs): return sum(len(x['content']) for x in xs)
        class Client:
            tokenizer = Tokens(); input_limit = 100000
            def __init__(self): self.names = []
            def __call__(self, messages, json_schema=None, provider=None):
                self.names.append(json_schema['name'])
                if json_schema['name'] == 'ra_request_grounded_answer':
                    c = proposal()[0]
                    c.pop('id')
                    c['citation_span_ids'] = ['S1', 'S3']; c.pop('sources')
                    c['claim_basis'] = 'absence_of_evidence'
                    c['text'] = '135 mg IV 투여는 문서에 없고 SB17은 45 mg SC로 투여하였다.'
                    return {'text': json.dumps({'request_results': {'R1': {'claims': [c], 'gaps': []}}})}
                payload = json.loads(messages[1]['content'])
                assert 'France' not in str(payload)
                assert 'original_question' not in payload
                return {'text': json.dumps({'claims': review([clause()])['claims']})}
        client = Client()
        result = ra_answer.generate('투여 조건', {'tasks': [TASK]}, blocks(), client, 'spans')
        self.assertEqual(client.names, ['ra_request_grounded_answer', 'ra_selected_claim_truth'])
        self.assertEqual(len(result['claims']), 1)
        self.assertIn('45 mg', result['answer_ko'])

    def test_final_meaning_input_contains_only_the_clauses_actual_citations(self):
        evidence = blocks()
        kept, _, _ = expansion(review([clause()]), evidence, proposal(), [TASK])
        payload = json.loads(ra_support.messages('투여 조건', {'tasks': [TASK]}, evidence, kept)[1]['content'])
        quotes = [s['quote'] for s in payload['clauses'][0]['selected_sources']]
        self.assertIn('Subjects received a single 45 mg SC injection.', quotes)
        self.assertNotIn('The trial was conducted in France.', str(payload))
        self.assertNotIn('The 90% confidence interval was calculated.', str(payload))
        self.assertNotIn('reading_segments', str(payload))

    def test_final_meaning_rejection_preserves_other_supported_clauses(self):
        kept, _, _ = expansion(review([clause(), clause(text='SB17은 프랑스에서 시험하였다.', ids=('S1',))]),
                                     blocks(), proposal(), [TASK])
        raw = {'clauses': {'C1.1': {'verdict': 'supported', 'reason': 'dose quote'},
                          'C1.2': {'verdict': 'uncertain', 'reason': 'location not quoted'}},
               'coverage': {'T1': {'status': 'partial', 'reason': 'place missing'}}, 'gaps': []}
        accepted, rejected, _ = ra_support.apply(raw, kept, [TASK])
        self.assertEqual([c['id'] for c in accepted], ['C1.1'])
        self.assertEqual(rejected[0]['reason'], 'final_selected_source_uncertain')
        raw['clauses'].pop('C1.2')
        with self.assertRaisesRegex(ValueError, 'every clause'):
            ra_support.apply(raw, kept, [TASK])

    def test_default_review_does_not_supply_or_accept_uncited_neighbor_spans(self):
        evidence = blocks(); parents = proposal(text='SB17은 90 mg SC로 투여하였다.')
        payload = json.loads(ra_review.messages('투여 조건', {'tasks': [TASK]}, evidence, parents)[1]['content'])
        quotes = [s['quote'] for s in payload['proposed_claims'][0]['selected_sources']]
        self.assertEqual(quotes, ['Study Alpha.', 'The 90% confidence interval was calculated.'])
        self.assertNotIn('France', str(payload))
        self.assertNotIn('Subjects received', str(payload))
        schema = ra_review.schema(evidence, parents, [TASK])['schema']
        self.assertEqual(schema['$defs']['span_C1']['enum'], ['S1', 'S2'])
        accepted, rejected, _ = ra_review.apply(review([clause()]), evidence, parents, [TASK])
        self.assertFalse(accepted)
        self.assertEqual(rejected[0]['reason'], 'unknown_or_missing_final_citation_span')

    def test_another_claims_selected_fact_cannot_be_borrowed_even_for_the_same_product(self):
        evidence = blocks()
        parents = proposal(text='SB17 시험을 진행하였다.', ids=('S1',))
        second = proposal(ids=('S3',))[0]; second['id'] = 'C2'; parents.append(second)
        raw = review([clause(ids=('S3',))])
        raw['claims']['C2'] = {'selected_support': 'supported', 'context_support': 'supported',
                               'clauses': [clause(ids=('S3',))]}
        kept, rejected, _ = ra_review.apply(raw, evidence, parents, [TASK])
        self.assertEqual([c['parent_claim_id'] for c in kept], ['C2'])
        self.assertEqual(rejected[0]['reason'], 'final_citation_outside_claim_selected_sources')


if __name__ == '__main__':
    unittest.main()
