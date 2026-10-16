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
        # Another claim's quote states IV; the GMR value cites only the SC study (EMA_UST_PK_023).
        evidence = {'E1': {'structure': {'section_path': ['2.6 Clinical aspects'], 'caption': None}}}
        background = {'id': 'C1', 'text': '별도 시험에서 IV 주입을 시행했다.',
                      'sources': [{'evidence': 'E1', 'quote': 'Study 002 used an intravenous infusion'}]}
        value = {'id': 'C2', 'text': 'BAT-2206-001-CR에서 AUCinf GMR은 0.9789였다.',
                 'sources': [{'evidence': 'E1', 'quote': 'pivotal PK study BAT-2206-001-CR, the 90% CIs of the GMRs'}]}
        r = {'status': 'answered', 'answer_ko': '답변', 'gaps': [], 'evidence': evidence, 'claims': [background, value]}
        question = 'IV 직접 비교 PK 시험에서 측정된 AUCinf GMR은?'
        self.assertEqual(check(question, r),
                         [{'dimension': 'route', 'requested': '정맥(IV)', 'cited_instead': [], 'value_claims': ['C2']}])
        value['text'] = 'IV 외삽을 뒷받침한 BAT-2206-001-CR에서 AUCinf GMR은 0.9789였다.'  # names only the requested route
        self.assertEqual(check(question, r)[0]['value_claims'], ['C2'])
        value['text'] = 'BAT-2206-001-CR(피하 투여)에서 AUCinf GMR은 0.9789였다.'  # names its own route
        self.assertEqual(check(question, r), [])

    def test_extrapolation_or_waiver_does_not_state_the_condition(self):
        r = result(['Adding AUCTmax-inf is considered adequate to support extrapolation of the SC data to the IV '
                    'administration route and to waive a clinical study using the IV presentation.'])
        self.assertEqual(check('IV 직접 비교 PK 시험의 AUCinf GMR은?', r),
                         [{'dimension': 'route', 'requested': '정맥(IV)', 'cited_instead': ['피하(SC)']}])

    def test_time_point_value_without_a_time_point_is_flagged(self):
        r = result(['<tr><td>NAb</td></tr><tr><td>Day 99</td><td>Positive</td><td>5/14</td><td>(35.7)</td></tr>'])
        r['claims'][0].update(id='C1', text='투여 후 NAb 양성률은 Pyzchiva군 35.7%였다.')
        missing = check('SB17-1001 시험에서 투여 후 NAb 양성률(군별)은?', r)
        self.assertEqual(missing, [{'dimension': 'timepoint', 'requested': '시점 미지정', 'cited_instead': ['Day 99'],
                                    'value_claims': ['C1']}])
        apply('SB17-1001 시험에서 투여 후 NAb 양성률(군별)은?', r)
        self.assertTrue(r['answer_ko'].startswith('인용한 값은 특정 시점(Day 99)의 결과입니다.'))
        r = result(['<tr><td>Day 99</td><td>5/14</td><td>(35.7)</td></tr>'])
        r['claims'][0]['text'] = 'Day 99 NAb 양성률은 35.7%였다.'
        self.assertEqual(check('SB17-1001 시험에서 투여 후 NAb 양성률(군별)은?', r), [])
        r['claims'][0]['text'] = 'NAb 양성률은 35.7%였다.'
        self.assertEqual(check('SB17-1001 시험의 Day 99 NAb 양성률은?', r), [])

    def test_whole_period_sentence_is_not_a_time_point_value(self):
        r = result(['The overall incidence of subjects with positive post-dose ADA across timepoints was 18 (26.9%) '
                    'at Day 29, Day 71 and Day 99 combined.'])
        r['claims'][0]['text'] = '투여 후 ADA 양성률은 26.9%였다.'
        self.assertEqual(check('SB17-1001 시험에서 투여 후 ADA 양성률(군별)은?', r), [])

    def test_value_claim_citing_the_requested_condition_is_not_flagged(self):
        r = result(['Cmax GMR 0.98 after a single intravenous infusion'])
        r['claims'][0]['text'] = 'IV 단회 주입 후 Cmax GMR은 0.98이었다.'
        self.assertEqual(check('IV 투여 시험의 Cmax GMR은?', r), [])
        r['claims'][0]['text'] = 'Cmax GMR은 0.98이었다.'
        self.assertEqual(check('IV 투여 시험의 Cmax GMR은?', r), [])


if __name__ == '__main__':
    unittest.main()
