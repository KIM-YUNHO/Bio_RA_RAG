import unittest
from datetime import date
from unittest.mock import patch

from rag.ra import ra_timeline
from rag.retrieval.followup import answer_summary


def chunk(cid, product, jurisdiction, title, text, page=1):
    return {'chunk_id': cid, 'product_id': product, 'jurisdiction': jurisdiction, 'title': title, 'text': text,
            'pdf_page': page, 'header': f'{product} (Brand) | src | {title} | p.{page}'}


CHUNKS = [
    chunk('eu1', 'P1', 'EU', 'Brand : EPAR - Public assessment report',
          'The applicant submitted on 24 April 2023 an application for marketing authorisation to the EMA.'),
    chunk('eu2', 'P1', 'EU', 'Brand : EPAR - Product information', '9. DATE OF FIRST AUTHORISATION\nDate of first authorisation: 19 April 2024\n10.'),
    chunk('us1', 'P1', 'US', 'Brand (BLA 761373 ORIG-1) — Multi-Discipline Review',
          '<tr><td>Received Date </td><td>March 30, 2023 </td></tr><tr><td>BsUFA Goal Date </td><td>June 30, 2024</td></tr>'),
    # Interchangeability (Original 2) cover: a Target Action Date, not BsUFA.
    chunk('us2', 'P1', 'US', 'Brand (BLA 761373 ORIG-2) — Multi-Discipline Review',
          '<tr><td>Received Date </td><td>October 31, 2024</td></tr><tr><td>Target Action Date </td><td>April 30, 2025</td></tr>'),
    chunk('us3', 'P1', 'US', 'Brand (BLA 761373 ORIG-2) — Approval Letter(s)',
          'Approval Package for: APPLICATION NUMBER: 761373Orig1s000 761373Orig2s000 Approval Date: '
          '761373Orig1 – June 28, 2024 761373Orig2 – April 30, 2025'),
    # Supplement letters describe a later supplement, never the original BLA.
    chunk('us4', 'P1', 'US', 'Brand (BLA 761373 SUPPL-1, 2024-12-27) — Approval Letter',
          'Please refer to your biologics license application (BLA) received February 29, 2024 , and your amendments'),
    chunk('ca1', 'P1', 'CA', 'Summary Basis of Decision for Brand',
          'Date Filed: 2023-08-22\nAuthorization Date: 2024-08-07'),
    # A Regulatory Decision Summary "Date Filed" is a different submission.
    chunk('ca2', 'P1', 'CA', 'Regulatory Decision Summary for Brand', 'Date Filed:\n2025-04-02'),
]


class TimelineRequestTests(unittest.TestCase):
    def test_detects_approval_date_and_period_questions(self):
        for q in ('우스테키누맙의 모든 바이오시밀러들의 ra 허가 날짜를 정리해줄래?\n제출 날짜 부터 최종 허가까지 걸린 기간이 궁금하다.',
                  'SB17 FDA 승인일 알려줘', '모든 제품 허가 날짜 알려줘, 허가까지 기간을 알고 싶은거야',
                  'What is the approval date of Wezlana?', '신청부터 승인까지 소요 기간은?'):
            self.assertTrue(ra_timeline.requested(q), q)

    def test_ignores_other_approval_topics(self):
        for q in ('허가 조건으로 요구된 시판 후 연구는?', 'SB17 보관 기간은?', 'EMA가 승인한 적응증 외삽 근거는?',
                  'PK 연구의 투여 용량은?'):
            self.assertFalse(ra_timeline.requested(q), q)


class TimelineArithmeticTests(unittest.TestCase):
    def test_parses_regulatory_date_formats(self):
        for text, expected in (('2022-11-15', date(2022, 11, 15)), ('20 April 2023', date(2023, 4, 20)),
                               ('22 Aug 2024', date(2024, 8, 22)), ('Nov. 29, 2023', date(2023, 11, 29)),
                               ('October 31, 2022', date(2022, 10, 31)), ('05/22/2025', date(2025, 5, 22))):
            self.assertEqual(expected, ra_timeline.parse(text))
        self.assertIsNone(ra_timeline.parse('31 Foo 2023'))

    def test_calendar_difference(self):
        self.assertEqual((407, 1, 1, 12), ra_timeline.elapsed(date(2022, 11, 15), date(2023, 12, 27)))
        self.assertEqual((584, 1, 7, 5), ra_timeline.elapsed(date(2024, 3, 12), date(2025, 10, 17)))
        self.assertEqual((29, 0, 0, 29), ra_timeline.elapsed(date(2024, 1, 31), date(2024, 2, 29)))
        self.assertEqual('407일 (1년 1개월 12일)', ra_timeline.period_ko(date(2022, 11, 15), date(2023, 12, 27)))


class TimelineExtractionTests(unittest.TestCase):
    def test_reads_original_dates_and_excludes_orig2_supplements_and_rds(self):
        best = ra_timeline.choose(ra_timeline.facts(CHUNKS))
        got = {k: (f['date'].isoformat(), f['chunk']['chunk_id']) for k, f in best.items()}
        self.assertEqual({('P1', 'EU', 'submission'): ('2023-04-24', 'eu1'), ('P1', 'EU', 'approval'): ('2024-04-19', 'eu2'),
                          ('P1', 'US', 'submission'): ('2023-03-30', 'us1'), ('P1', 'US', 'approval'): ('2024-06-28', 'us3'),
                          ('P1', 'CA', 'submission'): ('2023-08-22', 'ca1'), ('P1', 'CA', 'approval'): ('2024-08-07', 'ca1')}, got)

    def test_answer_cites_exact_quotes_and_computes_durations(self):
        understanding = {'products': ['P1', 'P2'], 'jurisdictions': ['CA', 'EU', 'US', 'sponsor'],
                         'tasks': [{'id': 'T1', 'request': 'r'}]}
        blocks = lambda corpus, selected: {'E1': dict(selected[0][0])}
        with patch('rag.ra.ra_retrieval.blocks', blocks):
            result = ra_timeline.answer(None, understanding, 'q', CHUNKS)
        evidence = result['evidence']
        for claim in result['claims']:
            for source in claim['sources']:
                start, end = source['source_range']
                self.assertEqual(source['quote'], evidence[source['evidence']]['text'][start:end])
        durations = {c['text'] for c in result['claims'] if c['kind'] == 'interpretation'}
        self.assertIn('P1 · EU(EMA): 제출부터 허가까지 361일 (11개월 26일) (코드 계산)', durations)
        self.assertTrue(all(c['computed']['generated_by_model'] is False for c in result['claims'] if c['kind'] == 'interpretation'))
        self.assertEqual(3, len(durations))
        # P2 has no documents in scope: reported as a gap, never invented.
        self.assertEqual('partial', result['status'])
        self.assertTrue(any(g.startswith('P2 · EU(EMA)') for g in result['gaps']))
        self.assertNotIn('sponsor', result['answer_ko'])

    def test_no_date_field_returns_none_for_fallback(self):
        understanding = {'products': ['P1'], 'jurisdictions': ['EU'], 'tasks': [{'id': 'T1', 'request': 'r'}]}
        self.assertIsNone(ra_timeline.answer(None, understanding, 'q', [chunk('x', 'P1', 'EU', 'EPAR', 'no dates')]))


class FollowupSummaryTests(unittest.TestCase):
    def test_summary_drops_new_gap_sections(self):
        for header in ('인용으로 완전히 확인하지 못한 요청', '확인하지 못한 항목', '추가 확인이 필요한 요청',
                       '계산하지 않은 값 (계산할 두 값이 검증된 인용에 모두 있지 않음)'):
            self.assertEqual('사실 A.', answer_summary(f'사실 A. [E1]\n\n{header}:\n- x'))
