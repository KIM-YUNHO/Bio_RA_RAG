from copy import deepcopy
import unittest

from rag.ra import ra_regimen, ra_requirements


def evidence(text):
    return {'E1': {'text': text, 'document_id': 'd', 'version_id': 'v',
        'product_id': 'SB17', 'jurisdiction': 'EU', 'structure': {'heading_anchors': [
            {'kind': 'study', 'title': 'SB17-1001', 'heading_location': {'block_id': 'h'}}]}}}


def claim(text, quote, cid='C1'):
    return {'id': cid, 'text': text, 'products': ['SB17'], 'task_ids': ['T1'],
        'sources': [{'evidence': 'E1', 'quote': quote}]}


class RegimenRolesTests(unittest.TestCase):
    def test_former_proposal_does_not_conflict_with_actual_same_study(self):
        quote = 'The applicant originally proposed 90 mg SC, but healthy subjects received 45 mg SC.'
        first = claim('원래 제안된 요법은 90 mg 피하 단회 투여였고, 후속 요법은 45 mg 피하 투여였다.', quote)
        second = claim('SB17은 건강인에게 실제 45 mg 피하 투여되었다.', quote, 'C2')
        before = deepcopy(first)
        task = {'id': 'T1', 'request': 'EMA SB17 건강인 실제 용량과 경로'}
        row = ra_requirements.coverage(task['request'], {'tasks': [task]}, evidence(quote), [first, second])['tasks'][0]
        self.assertTrue(row['minimum_requirements_met'])
        regimen = next(c for c in row['checks'] if c['kind'] == 'unambiguous_regimen')
        self.assertEqual(regimen['observed_quantities'], [{'value': '45', 'unit': 'mg'}])
        self.assertTrue(regimen['historical_exclusions'])
        self.assertEqual(first, before)

    def test_unknown_or_conflicting_actual_doses_remain_blocking(self):
        quote = 'Healthy subjects received 45 mg SC or 90 mg SC.'
        claims = [claim('SB17은 건강인에게 45 mg SC를 투여했다.', quote),
                  claim('SB17은 건강인에게 90 mg SC를 투여했다.', quote, 'C2')]
        task = {'id': 'T1', 'request': 'EMA SB17 건강인 용량과 경로'}
        row = ra_requirements.coverage(task['request'], {'tasks': [task]}, evidence(quote), claims)['tasks'][0]
        self.assertFalse(row['minimum_requirements_met'])

    def test_claim_proposal_word_without_own_source_proof_is_not_ignored(self):
        quote = 'Healthy subjects received 90 mg SC.'
        c = claim('원래 제안된 요법은 90 mg SC였다.', quote)
        self.assertFalse(ra_regimen.completion_view(c, evidence(quote))['excluded_historical_clauses'])

    def test_mixed_proposed_and_administered_clause_is_not_silently_hidden(self):
        quote = 'The applicant proposed 90 mg SC and administered 45 mg SC.'
        c = claim('The applicant proposed 90 mg SC and administered 45 mg SC.', quote)
        self.assertFalse(ra_regimen.completion_view(c, evidence(quote))['excluded_historical_clauses'])

    def test_historical_only_fact_cannot_complete_actual_dose(self):
        quote = 'The applicant originally proposed a 90 mg SC dose in healthy subjects.'
        c = claim('원래 제안된 용량은 건강인 90 mg SC였다.', quote)
        task = {'id': 'T1', 'request': 'EMA SB17 건강인 실제 용량과 경로'}
        row = ra_requirements.coverage(task['request'], {'tasks': [task]}, evidence(quote), [c])['tasks'][0]
        self.assertFalse(row['minimum_requirements_met'])

    def test_false_requested_arm_stays_incomplete(self):
        quote = 'The applicant originally proposed 90 mg SC, but healthy subjects received 45 mg SC.'
        c = claim('원래 제안된 용량은 90 mg SC였고, 실제 건강인 용량은 45 mg SC였다.', quote)
        task = {'id': 'T1', 'request': 'EMA SB17 건강인 90 mg SC 실제 용량과 경로'}
        row = ra_requirements.coverage(task['request'], {'tasks': [task]}, evidence(quote), [c])['tasks'][0]
        self.assertFalse(row['minimum_requirements_met'])

    def test_planner_document_rephrasing_is_checkable_without_accepting_results(self):
        self.assertTrue(ra_requirements.fully_checkable_request('EMA 문서에서 SB17 건강인 대상 PK 시험의 실제 투여 용량과 투여 경로를 확인해줘.'))
        self.assertFalse(ra_requirements.fully_checkable_request('EMA 문서에서 SB17 건강인 대상 PK 시험의 용량과 경로 및 결과를 확인해줘.'))


if __name__ == '__main__': unittest.main()
