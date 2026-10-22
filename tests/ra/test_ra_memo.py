from copy import deepcopy
import unittest

from rag.ra.ra_memo import build


def report(status='partial'):
    return {'question': 'SB17의 용량과 브리징 조건', 'pipeline_version': 'test', 'index': {'chunks_sha256': 'x'},
        'understanding': {'tasks': [{'id': 'T1', 'request': '용량과 브리징 조건', 'source_roles': ['assessment']}],
                          'products': ['SB17'], 'jurisdictions': ['EU']},
        'results': [{'status': status, 'gaps': ['브리징 조건'] if status == 'partial' else [],
            'evidence': {'E1': {'document_id': 'd', 'version_id': 'v', 'title': 'Own document', 'pdf_page': 4,
                               'product_id': 'SB17', 'jurisdiction': 'EU', 'doc_group': 'assessment'}},
            'claims': [{'id': 'C1', 'text': 'SB17은 45 mg SC를 투여했다.', 'kind': 'source_fact',
                'products': ['SB17'], 'task_ids': ['T1'], 'sources': [{'evidence': 'E1',
                'quote': 'Subjects received 45 mg SC.', 'citation_span_id': 's1', 'source_range': [0, 27]}]}]}]}


class MemoTests(unittest.TestCase):
    def test_memo_preserves_own_claim_quote_document_and_gap_without_new_fact(self):
        r = report(); before = deepcopy(r); memo = build(r)
        source = memo['issues'][0]['claims'][0]['sources'][0]
        self.assertEqual(source['citation_span_id'], 's1')
        self.assertEqual(source['title'], 'Own document')
        self.assertIn('브리징 조건', memo['unresolved_requests'])
        self.assertEqual(memo['meeting_questions'][0]['issue_id'], 'T1')
        self.assertFalse(memo['domain_expert_reviewed'])
        self.assertEqual(r, before)

    def test_complete_answer_does_not_invent_meeting_questions(self):
        memo = build(report('answered'))
        self.assertFalse(memo['meeting_questions'])
        self.assertEqual(memo['issues'][0]['status'], 'complete')

    def test_unknown_task_reference_is_rejected(self):
        r = report(); r['results'][0]['claims'][0]['task_ids'] = ['foreign']
        with self.assertRaises(ValueError): build(r)

    def test_date_claim_keeps_its_own_document_identity(self):
        r = report(); r['results'][0]['claims'][0]['text'] = '시행일은 2014년 12월 1일이다.'
        memo = build(r)
        self.assertIn('Own document', memo['markdown'])
        self.assertIn('2014년 12월 1일', memo['markdown'])
        self.assertFalse(memo['semantic_accuracy_certified'])


if __name__ == '__main__': unittest.main()
