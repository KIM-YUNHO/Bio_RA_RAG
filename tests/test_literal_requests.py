import unittest
from rag.literal_requests import compile_requests


class LiteralRequestsTests(unittest.TestCase):
    def test_independent_fields_expand_without_model(self):
        c=compile_requests('건선 성인의 피하 투여 후 45 mg 및 90 mg tmax 중앙값과 평균 및 단위는?')
        self.assertEqual(6,len(c['requests']))
        self.assertEqual({'45 mg','90 mg'},{x['value'] for r in c['requests'] for a in r['conditions'] if a['dimension']=='dose' for x in a['values']})
        self.assertFalse(c['complete_question_interpretation'])

    def test_conjunctive_conditions_are_preserved(self):
        c=compile_requests('CT-P43 1.1 Part 2 단백질 함량 보정, ADA 음성, 체중 60~90 kg 조건 사후 분석의 EU Stelara 대비 AUCinf, Cmax GMR와 90% CI는?')
        self.assertIsNotNone(c)
        self.assertEqual(4,len(c['requests']))
        for r in c['requests']:
            self.assertTrue({'covariate','analysis_set','weight','analysis_timing','comparison','study'}<={a['dimension'] for a in r['conditions']})

    def test_every_comparison_is_requested(self):
        c=compile_requests('SB17-1001 US Stelara 대비 및 EU Stelara와 US Stelara 간 AUCinf, Cmax GMR와 90% CI는?')
        self.assertIsNotNone(c)
        self.assertEqual(8,len(c['requests']))
        pairs={tuple(v['value'] for v in a['values']) for r in c['requests'] for a in r['conditions'] if a['dimension']=='comparison'}
        self.assertEqual({frozenset(('SB17','US-Stelara')),frozenset(('EU-Stelara','US-Stelara'))},{frozenset(p) for p in pairs})

    def test_correlated_and_negative_questions_use_fallback(self):
        self.assertIsNone(compile_requests('45 mg tmax 중앙값; 90 mg Cmax 평균'))
        self.assertIsNone(compile_requests('US 군 제외, EU 대비 AUCinf GMR'))
        self.assertIsNone(compile_requests('45 mg 또는 90 mg Cmax 평균'))

    def test_alias_does_not_add_arithmetic_mean(self):
        c=compile_requests('AUCinf의 기하평균비(GMR)와 90% 신뢰구간은?')
        self.assertEqual({'GMR','CI90'},{r['field']['value'] for r in c['requests']})

    def test_unenumerated_subgroups_use_model_contract(self):
        for q in ['Cmax GMR 하위집단별','각 분석 집단 Cmax GMR','Cmax GMR each subgroup','ADA 상태별 Cmax GMR']:
            self.assertIsNone(compile_requests(q))

    def test_automatic_boundary_preserves_unknown_conditions_and_extra_fields(self):
        from rag.literal_requests import automatic_roles_complete
        self.assertTrue(automatic_roles_complete('SB17-1001 시험에서 EU Stelara 대비 Cmax GMR와 90% CI는?'))
        for modifier in ['비흡연자의','Day 29','남성만','그리고 분석군 인원도','및 SD','및 시험 설계']:
            self.assertFalse(automatic_roles_complete('SB17-1001 EU Stelara 대비 Cmax GMR와 90% CI '+modifier))
