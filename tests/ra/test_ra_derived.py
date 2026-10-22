import unittest

from rag.ra import ra_answer, ra_derived
from rag.ra.ra_models import RequestedDraft
from rag.evidence.schema_budget import enum_count


def claim(cid, product, *sources):
    return {'id': cid, 'products': [product], 'task_ids': ['T1'], 'kind': 'source_fact',
            'sources': [{'evidence': 'E1', 'quote': q, 'citation_alias': a} for a, q in sources]}


VERIFIED = [claim('C1', 'P1', ('S1', 'Date Filed: 2022-11-15'), ('S2', 'Authorization Date: 2023-12-27')),
            claim('C2', 'P1', ('S3', '<td>45.2</td>'), ('S4', '<td>90.4</td>'), ('S5', '1.07 (0.98, 1.17)'))]


class DerivedValueTests(unittest.TestCase):
    def compute(self, op, ids, label='제출부터 허가까지'):
        return ra_derived.compute([{'label': label, 'operation': op, 'operand_span_ids': ids}], VERIFIED)

    def test_date_difference_is_computed_from_verified_operands(self):
        made, withheld = self.compute('date_difference', ['S1', 'S2'])
        self.assertEqual([], withheld)
        self.assertEqual('제출부터 허가까지: 2022-11-15 → 2023-12-27: 407일 (1년 1개월 12일) (코드 계산)', made[0]['text'])
        self.assertEqual('407', made[0]['computed']['result'])
        self.assertFalse(made[0]['computed']['generated_by_model'])
        self.assertEqual(['S1', 'S2'], [s['citation_alias'] for s in made[0]['sources']])

    def test_numeric_operations(self):
        self.assertIn('90.4 − 45.2 = 45.2', self.compute('difference', ['S3', 'S4'], '차이')[0][0]['text'])
        self.assertIn('45.2 / 90.4 = 0.5', self.compute('ratio', ['S3', 'S4'], '비')[0][0]['text'])
        self.assertIn('45.2 → 90.4: 100%', self.compute('percent_change', ['S3', 'S4'], '증감')[0][0]['text'])

    def test_unverified_ambiguous_or_numbered_declarations_are_withheld(self):
        cases = {('date_difference', ('S1', 'S9'), 'x'): 'derived_operand_not_in_verified_claims',
                 ('difference', ('S3', 'S5'), 'x'): 'derived_operand_value_ambiguous',
                 ('date_difference', ('S2', 'S1'), 'x'): 'derived_negative_interval',
                 ('date_difference', ('S1', 'S2'), '407일 소요'): 'derived_label_contains_number'}
        for (op, ids, label), reason in cases.items():
            made, withheld = self.compute(op, list(ids), label)
            self.assertEqual([], made)
            self.assertEqual(reason, withheld[0]['reason'])


class ClaimBudgetAndSchemaTests(unittest.TestCase):
    def test_budget_scales_with_enumerated_units_only(self):
        self.assertEqual(8, ra_answer.claim_budget({'products': ['SB17'], 'jurisdictions': ['EU', 'US', 'CA', 'sponsor']}))
        self.assertEqual(15, ra_answer.claim_budget({'products': list('ABCDE'), 'jurisdictions': ['EU', 'US', 'CA']}))
        self.assertEqual(16, ra_answer.claim_budget({'products': list('ABCDEFG'), 'jurisdictions': ['EU', 'US', 'CA']}))

    def test_request_schema_shares_one_span_enum_with_derived_operands(self):
        text = '\n\n'.join(f'Paragraph {i}.' for i in range(30))
        evidence = {'E1': {'text': text, 'version_id': 'v', 'document_id': 'd', 'product_id': 'P1', 'chunk_id': 'c'}}
        contract = ra_answer.schema(evidence, [{'id': 'T1'}], 'spans',
                                    question='각 허가 날짜를 정리해줘? 기간이 궁금하다.', claim_budget=15)
        schema = contract['schema']
        self.assertEqual(30, len(schema['$defs']['span_id']['enum']))
        self.assertLess(enum_count(schema), 30 + 40)
        self.assertIn('derived', schema['required'])
        self.assertEqual(15, schema['properties']['request_results']['properties']['R1']['properties']['claims']['maxItems'])

    def test_drafts_accept_derived_and_older_replies_without_it(self):
        claim = {'text': 't', 'kind': 'source_fact', 'products': [], 'claim_basis': 'source_statement',
                 'task_ids': ['T1'], 'citation_span_ids': ['S1']}
        context = {'task_ids': ['T1'], 'request_ids': ['R1']}
        old = RequestedDraft.model_validate({'request_results': {'R1': {'claims': [claim], 'gaps': []}}}, context=context)
        self.assertEqual([], old.derived)
        new = RequestedDraft.model_validate({'request_results': {'R1': {'claims': [claim] * 12, 'gaps': []}},
            'derived': [{'label': 'x', 'operation': 'date_difference', 'operand_span_ids': ['S1', 'S2']}]}, context=context)
        self.assertEqual(1, len(new.derived))


if __name__ == '__main__':
    unittest.main()


class LexicalGateFalsePositiveTests(unittest.TestCase):
    def test_iso_date_matches_us_order_source_date_only_when_equal(self):
        from rag.ra import ra_quantities as q
        for claim_date, source in (('2023-03-30', 'Received Date March 30, 2023'),
                                   ('2022-10-31', 'Received Date Oct 31, 2022'), ('2023-11-29', 'Nov. 29, 2023')):
            self.assertLessEqual(q.numbers(claim_date), q.numbers(source))
        self.assertFalse(q.numbers('2023-04-30') <= q.numbers('March 30, 2023'))

    def test_presentation_name_is_not_a_route_claim(self):
        from rag.ra import ra_source_constraints as c
        claim = lambda text: {'text': text, 'products': ['ABP654'],
                              'sources': [{'quote': 'Date Filed: 2022-11-15', 'evidence': 'E1'}]}
        self.assertIsNone(c.check(claim('ABP654(Wezlana/Wezlana I.V.)의 캐나다 NDS 제출일은 2022-11-15이다.'), {'E1': {}}))
        self.assertEqual('claim_route_not_in_selected_sources', c.check(claim('Wezlana는 정맥(IV) 투여된다.'), {'E1': {}}))


class DerivedLabelTests(unittest.TestCase):
    def test_product_codes_in_label_are_allowed_but_values_are_not(self):
        declared = lambda label: [{'label': label, 'operation': 'date_difference', 'operand_span_ids': ['S1', 'S2']}]
        self.assertEqual(1, len(ra_derived.compute(declared('SB17 · BMAB1200 · ABP 654 캐나다 소요 기간'), VERIFIED)[0]))
        self.assertEqual('derived_label_contains_number', ra_derived.compute(declared('SB17 407일'), VERIFIED)[1][0]['reason'])


class DateParseTests(unittest.TestCase):
    def test_out_of_range_dates_are_not_dates(self):
        self.assertIsNone(ra_derived.parse('13/45/2024'))
        self.assertIsNone(ra_derived.parse('31 February 2024'))


class SpanChoiceTests(unittest.TestCase):
    def test_large_catalog_uses_id_pattern_instead_of_enum(self):
        def evidence(paragraphs):
            text = '\n\n'.join(f'Paragraph {i}.' for i in range(paragraphs))
            return {'E1': {'text': text, 'version_id': 'v', 'document_id': 'd', 'product_id': 'P1', 'chunk_id': 'c'}}
        q = '각 허가 날짜를 정리해줘? 기간이 궁금하다.'
        small = ra_answer.schema(evidence(30), [{'id': 'T1'}], 'spans', question=q)['schema']
        large = ra_answer.schema(evidence(1500), [{'id': 'T1'}], 'spans', question=q)['schema']
        self.assertEqual(30, len(small['$defs']['span_id']['enum']))
        self.assertEqual(ra_answer.SPAN_PATTERN, large['$defs']['span_id']['pattern'])
        self.assertLess(enum_count(large), 100)


class PrintedOperandTests(unittest.TestCase):
    def test_printed_value_selects_one_date_from_a_multi_date_span_or_its_claim(self):
        claims = [claim('C1', 'P1', ('S1', 'approved on December 17, 2024 and interchangeable on April 30, 2025'),
                        ('S2', 'On June 30, 2023, the applicant submitted a BLA')),
                  claim('C2', 'P1', ('S3', '<td>Received Date </td>'), ('S4', '<td>March 30, 2023 </td>'))]
        made, withheld = ra_derived.compute([{'label': 'FDA 소요 기간', 'operation': 'date_difference', 'operand_span_ids': ['S2', 'S1'],
                                             'operand_values': ['June 30, 2023', 'December 17, 2024']}], claims)
        self.assertEqual([], withheld)
        self.assertEqual('536', made[0]['computed']['result'])
        made, _ = ra_derived.compute([{'label': 'FDA 소요 기간', 'operation': 'date_difference', 'operand_span_ids': ['S3', 'S1'],
                                       'operand_values': ['March 30, 2023', 'December 17, 2024']}], claims)
        self.assertEqual(1, len(made))

    def test_printed_value_absent_from_verified_sources_is_withheld(self):
        claims = [claim('C1', 'P1', ('S1', 'approved on December 17, 2024'), ('S2', 'submitted on June 30, 2023'))]
        made, withheld = ra_derived.compute([{'label': 'FDA', 'operation': 'date_difference', 'operand_span_ids': ['S2', 'S1'],
                                             'operand_values': ['June 30, 2022', 'December 17, 2024']}], claims)
        self.assertEqual([], made)
        self.assertEqual('derived_operand_value_ambiguous', withheld[0]['reason'])


class DerivedLabelIdentifierTests(unittest.TestCase):
    def test_identifier_printed_in_operand_sources_is_allowed(self):
        claims = [claim('C1', 'P1', ('S1', 'NDS # 277532 Date Filed: 2023-08-22'), ('S2', 'Authorization Date: 2024-08-07'))]
        declared = lambda label: [{'label': label, 'operation': 'date_difference', 'operand_span_ids': ['S1', 'S2']}]
        self.assertEqual(1, len(ra_derived.compute(declared('캐나다 NDS Control #277532'), claims)[0]))
        self.assertEqual('derived_label_contains_number', ra_derived.compute(declared('캐나다 351일'), claims)[1][0]['reason'])
