import unittest

from rag.ra_condition_check import apply, check, requested


def result(quotes, section=(), caption=None, status='answered'):
    evidence = {'E1': {'structure': {'section_path': list(section), 'caption': caption}}}
    return {'status': status, 'answer_ko': '답변', 'gaps': [], 'evidence': evidence,
            'claims': [{'sources': [{'evidence': 'E1', 'quote': q} for q in quotes]}]}


class ConditionCheckTests(unittest.TestCase):
    def test_requested_conditions_are_read_from_the_question(self):
        self.assertEqual(requested('Usymro EPAR의 IV 직접 비교 PK 시험 GMR은?'), {'route': ['정맥(IV)']})
        self.assertEqual(requested('SB17-1001 시험의 실제 용량은?'), {})

    def test_other_route_in_cited_quotes_is_flagged(self):
        r = result(['Each subject received a single 45 mg SC injection of BAT2206.'])
        missing = check('IV 직접 비교 PK 시험의 AUCinf GMR은?', r)
        self.assertEqual(missing, [{'dimension': 'route', 'requested': '정맥(IV)', 'cited_instead': ['피하(SC)']}])

    def test_caption_supplies_the_scope_of_a_table_cell(self):
        r = result(['260 mg 390 mg 520 mg'], section=["2.3 Recommended Adult Dosage in Crohn's Disease"],
                   caption='Initial Intravenous Dosage in Adult Patients with Crohn’s Disease')
        self.assertEqual(check('성인 크론병의 최초 정맥 유도 용량은?', r), [])

    def test_apply_adds_gap_and_notice_without_changing_claims(self):
        r = result(['single 45 mg SC injection'])
        claims = [dict(c) for c in r['claims']]
        apply('IV 시험 결과는?', r)
        self.assertEqual(r['status'], 'partial')
        self.assertTrue(r['answer_ko'].startswith('질문한 조건(정맥(IV))'))
        self.assertEqual(len(r['gaps']), 1)
        self.assertEqual(r['claims'], claims)

    def test_answer_without_claims_is_left_alone(self):
        r = {'status': 'not_found', 'answer_ko': '없음', 'gaps': [], 'claims': []}
        apply('IV 시험 결과는?', r)
        self.assertEqual(r['status'], 'not_found')
        self.assertEqual(r['condition_check']['missing'], [])


    def test_value_claim_needs_the_condition_in_its_own_cited_scope(self):
        # Another claim's quote names IV; the GMR value cites only the SC study (EMA_UST_PK_023).
        evidence = {'E1': {'structure': {'section_path': ['2.6 Clinical aspects'], 'caption': None}}}
        background = {'id': 'C1', 'text': 'EPAR은 SC 자료를 IV 경로로 외삽하는 근거를 기술했다.',
                      'sources': [{'evidence': 'E1', 'quote': 'extrapolation of the SC data to the IV administration route'}]}
        value = {'id': 'C2', 'text': 'BAT-2206-001-CR에서 AUCinf GMR은 0.9789였다.',
                 'sources': [{'evidence': 'E1', 'quote': 'pivotal PK study BAT-2206-001-CR, the 90% CIs of the GMRs'}]}
        r = {'status': 'answered', 'answer_ko': '답변', 'gaps': [], 'evidence': evidence, 'claims': [background, value]}
        self.assertEqual(check('IV 직접 비교 PK 시험에서 측정된 AUCinf GMR은?', r),
                         [{'dimension': 'route', 'requested': '정맥(IV)', 'cited_instead': [], 'value_claims': ['C2']}])
        value['text'] = 'BAT-2206-001-CR(피하 투여)에서 AUCinf GMR은 0.9789였다.'
        self.assertEqual(check('IV 직접 비교 PK 시험에서 측정된 AUCinf GMR은?', r), [])

    def test_value_claim_citing_the_requested_condition_is_not_flagged(self):
        r = result(['Cmax GMR 0.98 after a single intravenous infusion'])
        r['claims'][0]['text'] = 'IV 단회 주입 후 Cmax GMR은 0.98이었다.'
        self.assertEqual(check('IV 투여 시험의 Cmax GMR은?', r), [])
        r['claims'][0]['text'] = 'Cmax GMR은 0.98이었다.'
        self.assertEqual(check('IV 투여 시험의 Cmax GMR은?', r), [])


if __name__ == '__main__':
    unittest.main()
