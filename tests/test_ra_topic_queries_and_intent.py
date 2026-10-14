import unittest

from rag import ra_intent, ra_topic_queries


class FixedTopicQueries(unittest.TestCase):
    def test_topics_come_from_original_question_wording(self):
        q = 'BAT2206과 BMAB1200의 차이점을 혹시 정리해줄 수 있을까? 임상실험 관련해서 전체적으로 알려줘'
        self.assertEqual(ra_topic_queries.topics(q), ['clinical_program'])
        self.assertEqual(ra_topic_queries.topics('EMA에서 SB17 건강인 PK 시험의 실제 투여 용량과 경로를 알려줘.'), ['pk_study'])
        self.assertEqual(ra_topic_queries.topics('ABP 654의 모든 시험에서 ADA에 대한 코멘트를 정리해줘'), ['immunogenicity'])
        self.assertEqual(ra_topic_queries.topics('FDA 라벨의 성인 판상 건선 용량을 알려줘.'), [])

    def test_same_question_gives_same_queries_whatever_the_planner_wrote(self):
        q = '임상시험의 유효성과 안전성을 비교해줘'
        a = ra_topic_queries.for_task(q, {'request': '유효성 안전성 비교', 'queries': ['x']}, 1)
        b = ra_topic_queries.for_task(q, {'request': '전혀 다른 표현', 'queries': ['y']}, 1)
        self.assertEqual(a, b)
        self.assertEqual(a[0], ['clinical_program', 'efficacy', 'safety'])

    def test_multi_task_topic_needs_the_task_own_request(self):
        q = 'SB17의 투여 용량을 알려주고, 임상시험 안전성 결과도 정리해줘'
        dose = ra_topic_queries.for_task(q, {'request': 'SB17의 투여 용량'}, 2)
        safety = ra_topic_queries.for_task(q, {'request': '임상시험 안전성 결과'}, 2)
        self.assertEqual(dose, ([], []))
        self.assertEqual(safety[0], ['clinical_program', 'safety'])

    def test_fixed_queries_carry_no_identities(self):
        for _, _, queries in ra_topic_queries.TOPICS:
            for query in queries:
                self.assertNotRegex(query, r'SB17|ABP|CT-?P43|BAT|BMAB|Stelara|EMA|FDA|Health Canada|\d{3,}')


def plan(question, *tasks, in_scope=True):
    return {'in_scope': in_scope, 'tasks': [{'id': f'T{n}', 'request': r, 'intent': {'intent': i, 'request_quote': q}}
                                            for n, (r, i, q) in enumerate(tasks, 1)]}


class TaskIntentCheck(unittest.TestCase):
    def test_multi_intent_question_keeps_one_intent_per_task(self):
        q = 'SB17과 ABP654의 PK 시험 설계를 비교하고, FDA가 면역원성을 어떻게 평가했는지 알려줘'
        v = ra_intent.check(q, plan(q, ('PK 시험 설계 비교', 'fact_lookup', 'SB17과 ABP654의 PK 시험 설계를 비교하고'),
                                       ('FDA 면역원성 평가', 'regulatory_reasoning', 'FDA가 면역원성을 어떻게 평가했는지')))
        self.assertEqual(v['intents'], ['compare_products', 'regulatory_reasoning'])
        self.assertEqual(v['intent'], 'multi_intent')
        self.assertEqual(v['intent_check']['tasks'][0]['rule'], 'two_named_products_with_comparison_marker')
        self.assertEqual(v['intent_check']['uncovered_request_segments'], [])

    def test_comparison_rule_reads_only_the_task_own_quote(self):
        q = 'SB17과 ABP654를 비교하고, ABP654의 투여 경로도 알려줘'
        v = ra_intent.check(q, plan(q, ('비교', 'compare_products', 'SB17과 ABP654를 비교하고'),
                                       ('경로', 'fact_lookup', 'ABP654의 투여 경로도 알려줘')))
        self.assertEqual(v['intents'], ['compare_products', 'fact_lookup'])

    def test_unverified_quote_is_recorded_and_dropped_request_is_reported(self):
        q = 'BAT2206 임상시험을 정리해줘. 그리고 EMA가 어떤 우려를 제기했는지 알려줘.'
        v = ra_intent.check(q, plan(q, ('임상시험 정리', 'fact_lookup', 'BAT2206 임상시험 요약')))
        self.assertFalse(v['intent_check']['tasks'][0]['quote_verified'])
        self.assertIn('그리고 EMA가 어떤 우려를 제기했는지 알려줘.', v['intent_check']['uncovered_request_segments'])

    def test_agency_versus_protocol_is_compare_sources(self):
        q = 'SB17의 프로토콜 계획과 EMA 평가에서 1차 평가변수가 어떻게 다른지 비교해줘'
        v = ra_intent.check(q, plan(q, ('비교', 'fact_lookup', q)))
        self.assertEqual(v['intents'], ['compare_sources'])

    def test_comparator_origin_is_not_a_regulator(self):
        q = 'SB17 시험에서 미국 Stelara와 유럽 Stelara 군을 비교해줘'
        v = ra_intent.check(q, plan(q, ('비교', 'fact_lookup', q)))
        self.assertEqual(v['intents'], ['fact_lookup'])

    def test_compare_products_without_two_named_products_is_downgraded(self):
        q = 'SB17 시험에서 미국 Stelara와 유럽 Stelara 군의 PK 결과를 비교해줘.'
        v = ra_intent.check(q, plan(q, ('군 비교', 'compare_products', '미국 Stelara와 유럽 Stelara 군의 PK 결과를 비교해줘')))
        self.assertEqual(v['intents'], ['fact_lookup'])
        self.assertEqual(v['intent_check']['tasks'][0]['rule'], 'compare_products_needs_two_named_products')

    def test_empty_english_query_is_not_an_error(self):
        from rag.ra_models import ResearchPlan
        task = {'request': 'x', 'queries': ['x'], 'source_roles': ['assessment'], 'guideline_request_quote': '',
                'intent': {'intent': 'fact_lookup', 'request_quote': 'x'}}
        base = {'products': [], 'jurisdictions': [], 'jurisdiction_mentions': [], 'english_query': '', 'tasks': [task]}
        for in_scope in (True, False):
            ResearchPlan.model_validate({**base, 'in_scope': in_scope})

    def test_subjectless_product_task_inherits_question_products(self):
        import json
        from rag import ra_tasks
        q = 'Yesintek 성인 크론병 정맥 유도 용량을 알려주고, Health Canada 제품 모노그래프의 최신 버전 날짜도 알려줘.'
        reply = {'in_scope': True, 'products': ['BMAB1200'], 'jurisdictions': [], 'jurisdiction_mentions': [], 'english_query': '',
                 'tasks': [{'request': 'Yesintek 성인 크론병 정맥 유도 용량', 'queries': ['dose'], 'source_roles': ['label'],
                            'guideline_request_quote': '', 'intent': {'intent': 'fact_lookup', 'request_quote': 'Yesintek 성인 크론병 정맥 유도 용량을 알려주고'}},
                           {'request': 'Health Canada 제품 모노그래프의 최신 버전 날짜를 알려줘.', 'queries': ['monograph date'],
                            'source_roles': ['label'], 'guideline_request_quote': '',
                            'intent': {'intent': 'document_info', 'request_quote': 'Health Canada 제품 모노그래프의 최신 버전 날짜도 알려줘.'}}]}
        u, _ = ra_tasks.interpret(q, lambda *a, **k: {'text': json.dumps(reply, ensure_ascii=False)}, ['BMAB1200'], ['CA', 'EU', 'US'])
        self.assertEqual(u['tasks'][0]['request'], 'Yesintek 성인 크론병 정맥 유도 용량')
        self.assertTrue(u['tasks'][1]['request'].startswith('BMAB1200 대상: '))

    def test_out_of_scope_part_is_split_from_an_in_scope_question(self):
        q = 'SB17의 투여 경로와 현재 유럽 시장 점유율을 알려줘'
        v = ra_intent.check(q, plan(q, ('경로', 'fact_lookup', 'SB17의 투여 경로'),
                                       ('점유율', 'out_of_scope', '현재 유럽 시장 점유율을 알려줘')))
        self.assertTrue(v['in_scope'])
        self.assertEqual([t['id'] for t in v['tasks']], ['T1'])
        self.assertEqual([t['id'] for t in v['out_of_scope_tasks']], ['T2'])
        self.assertEqual(v['intent'], 'fact_lookup')

    def test_all_tasks_out_of_scope_makes_the_question_out_of_scope(self):
        q = '아달리무맙 시장 점유율과 가격을 알려줘'
        v = ra_intent.check(q, plan(q, ('점유율', 'out_of_scope', '아달리무맙 시장 점유율'), ('가격', 'out_of_scope', '가격')))
        self.assertFalse(v['in_scope'])
        self.assertEqual(v['intent'], 'out_of_scope')

    def test_same_product_aliases_are_not_a_product_comparison(self):
        q = 'BMAB1200과 Yesintek의 임상 결과를 비교해줘'
        v = ra_intent.check(q, plan(q, ('비교', 'compare_products', q)))
        self.assertEqual(v['intents'], ['fact_lookup'])

    def test_out_of_scope_is_question_level(self):
        q = '오늘 날씨 어때?'
        v = ra_intent.check(q, plan(q, (q, 'fact_lookup', q), in_scope=False))
        self.assertEqual(v['intent'], 'out_of_scope')

    def test_every_task_intent_has_guidance_and_schema_matches_model(self):
        from rag import ra_tasks
        from rag.ra_models import TaskIntent
        self.assertEqual(set(ra_intent.GUIDANCE), set(ra_intent.INTENTS) - {'out_of_scope'})
        schema_enum = ra_tasks.SCHEMA['schema']['properties']['tasks']['items']['properties']['intent']['properties']['intent']['enum']
        self.assertEqual(schema_enum, list(TaskIntent.model_fields['intent'].annotation.__args__))


class RegistryNames(unittest.TestCase):
    def test_bare_name_matches_a_registry_name_with_market_qualifier(self):
        from rag.ra_requirements import named_products
        self.assertEqual(named_products('Wezlana의 Product Quality Review'), ['ABP654'])
        self.assertEqual(named_products('Wezenla EPAR'), ['ABP654'])
        self.assertEqual(named_products('SB17 시험'), ['SB17'])


class ProtocolSourceRouting(unittest.TestCase):
    def test_protocol_request_detection_tolerates_korean_particles(self):
        for text in ('SAP에 계획된 분석 집단', 'NCT04967508의 선정 기준', 'SB17 프로토콜', 'ClinicalTrials.gov 등록 정보'):
            self.assertTrue(ra_intent.PROTOCOL.search(text), text)
        for text in ('SAPPHIRE 연구', 'BAT2206 임상시험 결과', 'EMA 평가 보고서'):
            self.assertFalse(ra_intent.PROTOCOL.search(text), text)

    def test_planner_drops_unrequested_protocol_role_and_adds_sponsor_when_requested(self):
        import json
        from rag import ra_tasks

        class Client:
            def __init__(self, roles, jurisdictions, mentions):
                self.reply = {'in_scope': True, 'products': ['SB17'], 'jurisdictions': jurisdictions,
                              'jurisdiction_mentions': mentions, 'english_query': 'primary endpoint',
                              'tasks': [{'request': 'r', 'queries': ['primary endpoint'], 'source_roles': roles,
                                         'guideline_request_quote': '', 'intent': {'intent': 'fact_lookup', 'request_quote': ''}}]}

            def __call__(self, *a, **k):
                return {'text': json.dumps(self.reply, ensure_ascii=False)}
        agencies = ['CA', 'EU', 'US', 'sponsor']
        u, _ = ra_tasks.interpret('SB17 임상시험 결과를 알려줘', Client(['assessment', 'protocol'], [], []), ['SB17'], agencies)
        self.assertEqual(u['tasks'][0]['source_roles'], ['assessment'])
        q = 'SB17 프로토콜의 1차 평가변수와 EMA 평가를 비교해줘'
        mention = [{'jurisdiction': 'EU', 'role': 'regulator', 'quote': 'EMA'}]
        u, _ = ra_tasks.interpret(q, Client(['protocol', 'assessment'], ['EU'], mention), ['SB17'], agencies)
        self.assertEqual(u['jurisdictions'], ['EU', 'sponsor'])
        self.assertEqual(u['tasks'][0]['source_roles'], ['protocol', 'assessment'])
        # An index without CTG documents never gains a sponsor source.
        u, _ = ra_tasks.interpret(q, Client(['protocol', 'assessment'], ['EU'], mention), ['SB17'], ['CA', 'EU', 'US'])
        self.assertEqual(u['jurisdictions'], ['EU'])


if __name__ == '__main__':
    unittest.main()
