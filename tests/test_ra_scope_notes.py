"""Planner scope notes never become research requests or unresolved-request lines."""
import unittest

from rag.ra_requirements import route_task
from rag.ra_tasks import strip_scope_notes


class ScopeNoteTests(unittest.TestCase):
    def test_observed_planner_notes_are_removed(self):
        # 2026-10-13 17:41 and 17:47 service requests (reports fda375d1..., 64f766ea...).
        request, notes = strip_scope_notes('3상 시험은 어떻게 진행되었지? 질문에 대한 시험 설계, 대상 집단, 투여 제형·경로, '
            '시험 기간 및 주요 진행 절차를 확인해 달라. 대상 제품이 지정되지 않아 제품별 확인 범위는 제한됨.')
        self.assertEqual(request, '3상 시험은 어떻게 진행되었지? 질문에 대한 시험 설계, 대상 집단, 투여 제형·경로, '
            '시험 기간 및 주요 진행 절차를 확인해 달라.')
        self.assertEqual(notes, ['대상 제품이 지정되지 않아 제품별 확인 범위는 제한됨.'])
        request, notes = strip_scope_notes('SB17의 3상 시험이 어떻게 진행되었는지 확인해 달라. 시험 설계, 대상 집단, 투여 제형·경로, '
            '시험 기간 및 주요 진행 절차를 포함하되, 대상 제품이 지정되지 않아 제품별 확인 범위는 제한된다.')
        self.assertEqual(request, 'SB17의 3상 시험이 어떻게 진행되었는지 확인해 달라. 시험 설계, 대상 집단, 투여 제형·경로, '
            '시험 기간 및 주요 진행 절차를 포함한다.')
        self.assertEqual(notes, ['대상 제품이 지정되지 않아 제품별 확인 범위는 제한된다.'])

    def test_ordinary_requests_are_kept(self):
        for text in ['EMA 문서에서 SB17-1001 시험의 투여 용량과 경로를 확인해 달라.',
                     '라벨에 명시되지 않은 적응증의 범위를 확인해 달라.',
                     '연령이 제한된 대상군의 선정 기준을 정리해 달라.',
                     '기관이 지정한 시험 범위와 제한 조건을 비교해 달라.']:
            self.assertEqual(strip_scope_notes(text), (text, []))

    def test_pasted_note_is_not_an_original_request_segment(self):
        question = 'SB17의 3상 시험은 어떻게 진행되었지? 대상 제품이 지정되지 않아 제품별 확인 범위는 제한됨.'
        task = {'request': 'SB17의 3상 시험이 어떻게 진행되었는지 확인해 달라.', 'queries': ['SB17 phase 3'],
                'source_roles': ['assessment'], 'guideline_request_quote': ''}
        self.assertEqual(route_task(question, task)['original_request_segments'], ['SB17의 3상 시험은 어떻게 진행되었지?'])


if __name__ == '__main__':
    unittest.main()
