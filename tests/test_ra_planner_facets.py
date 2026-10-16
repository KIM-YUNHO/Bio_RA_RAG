"""The planner's closed-set answer facets and topics drive requirements, prompt modules and topic queries."""
import unittest

from rag import ra_obligations, ra_prompt, ra_topic_queries


def task(quote, intent='fact_lookup', facets=(), topics=(), request=None):
    return {'id': 'T1', 'request': request or quote, 'queries': ['q'], 'source_roles': ['assessment'],
            'intent': {'intent': intent, 'request_quote': quote}, 'intent_check': {'quote_verified': True},
            'answer_facets': [{'facet': f[0], 'detail': f[1], 'items': list(f[2]) if len(f) > 2 else []} for f in facets],
            'topics': list(topics)}


class PlannerFacetRequirements(unittest.TestCase):
    def test_rows_follow_planner_facets_per_named_product(self):
        q = 'SB17과 ABP654의 PK 시험 투여 용량과 경로, 대조약을 정리해줘.'
        rows = ra_obligations.build(q, [task(q, facets=[('dose_route', ''), ('comparator', '')])])
        self.assertEqual({(r['product'], r['facet']) for r in rows},
                         {(p, f) for p in ('SB17', 'ABP654') for f in ('dose_route', 'comparator')})

    def test_endpoint_and_part_details_must_be_printed_in_the_question(self):
        q = 'SB17 Part 2의 목적과 Cmax 평균을 알려줘.'
        rows = ra_obligations.build(q, [task(q, facets=[('purpose', '2'), ('table_statistics', 'Cmax'),
                                                         ('table_statistics', 'AUCinf')])])
        self.assertIn(('purpose', '2'), {(r['facet'], r['part']) for r in rows})
        endpoints = [r.get('endpoint') for r in rows if r['facet'] == 'table_statistics']
        self.assertEqual(endpoints, ['Cmax', None])

    def test_one_requirement_per_item_the_question_names(self):
        q = 'ABP 654의 PK 요약표에서 US ustekinumab군과 EU ustekinumab군의 Tmax 중앙값을 확인해줘.'
        rows = ra_obligations.build(q, [task(q, facets=[('table_statistics', 'Tmax',
                                                         ['US ustekinumab군', 'EU ustekinumab군', 'JP ustekinumab군'])])])
        self.assertEqual([(r['endpoint'], r.get('item')) for r in rows],
                         [('Tmax', 'US ustekinumab군'), ('Tmax', 'EU ustekinumab군')])  # unprinted item dropped
        self.assertTrue(all(r['item'] in r['label'] for r in rows))
        one = ra_obligations.build(q, [task(q, facets=[('table_statistics', 'Tmax', ['US ustekinumab군']),
                                                       ('statistic_identity', '')])])
        self.assertNotIn('item', one[0])

    def test_comparison_row_comes_from_the_planner_intent_not_keywords(self):
        q = 'SB17과 ABP654의 투여 경로를 정리해줘.'
        rows = ra_obligations.build(q, [task(q, intent='compare_products', facets=[('dose_route', '')])])
        self.assertIn('comparison', [r['facet'] for r in rows])
        rows = ra_obligations.build(q, [task(q, facets=[('dose_route', '')])])
        self.assertNotIn('comparison', [r['facet'] for r in rows])

    def test_open_request_without_facets_takes_the_request_path(self):
        q = '우스테키누맙 바이오시밀러들의 허가 날짜를 정리해줘.'
        self.assertEqual(ra_obligations.build(q, [task(q, facets=[('calculation', '')])]), [])

    def test_plans_without_facets_use_the_legacy_patterns(self):
        q = 'SB17과 ABP654의 투여 용량과 경로를 정리해줘.'
        legacy = {k: v for k, v in task(q).items() if k not in ('answer_facets', 'topics')}
        self.assertEqual(ra_obligations.build(q, [legacy]), ra_obligations.build(q))


class PlannerFacetModules(unittest.TestCase):
    def test_modules_follow_facets_not_question_words(self):
        q = '시험 관련 허가 날짜와 걸린 기간을 알려줘'  # "시험" and "기간" would trigger STUDY/DERIVED by keyword
        u = {'tasks': [task(q, facets=[])], 'products': ['SB17']}
        prompt = ra_prompt.system(q, u, {}, 8)
        self.assertNotIn(ra_prompt.STUDY, prompt)
        self.assertNotIn(ra_prompt.DERIVED, prompt)
        u = {'tasks': [task(q, facets=[('calculation', ''), ('dose_route', '')])], 'products': ['SB17']}
        prompt = ra_prompt.system(q, u, {}, 8)
        self.assertIn(ra_prompt.STUDY, prompt)
        self.assertIn(ra_prompt.DERIVED, prompt)

    def test_table_module_needs_a_planner_facet_and_a_table_in_evidence(self):
        q = 'SB17 PK 결과'
        u = {'tasks': [task(q, facets=[('table_statistics', '')])], 'products': ['SB17']}
        self.assertNotIn(ra_prompt.TABLE, ra_prompt.system(q, u, {'E1': {'text': 'plain'}}, 8))
        self.assertIn(ra_prompt.TABLE, ra_prompt.system(q, u, {'E1': {'text': '<table></table>'}}, 8))


class ChecklistMode(unittest.TestCase):
    def test_planner_reading_is_a_checklist_not_answer_slots(self):
        q = 'ABP 654의 PK 요약표에서 US ustekinumab군과 EU ustekinumab군의 Tmax 중앙값을 확인해줘.'
        tasks = [task(q, facets=[('table_statistics', 'Tmax', ['US ustekinumab군', 'EU ustekinumab군'])])]
        self.assertEqual(ra_obligations.slot_rows(q, tasks), [])
        items = ra_obligations.checklist(q, tasks)
        self.assertEqual([i['item'] for i in items], ['US ustekinumab군', 'EU ustekinumab군'])
        prompt = ra_prompt.system(q, {'tasks': tasks, 'products': ['ABP654']}, {}, 8)
        self.assertIn(ra_prompt.CHECKLIST, prompt)
        self.assertNotIn(ra_prompt.REQUIREMENT, prompt)


class ThirdRoleForMultiAgencyTasks(unittest.TestCase):
    def plan(self, question, task_agencies, agencies):
        import json
        from rag import ra_tasks
        reply = {'in_scope': True, 'products': ['SB17'], 'jurisdictions': [], 'jurisdiction_mentions': [],
                 'english_query': 'approval date', 'tasks': [{'request': question, 'queries': ['approval date'],
                 'source_roles': ['label', 'correspondence', 'decision_summary'], 'guideline_request_quote': '',
                 'intent': {'intent': 'fact_lookup', 'request_quote': question}, 'answer_facets': [], 'topics': [],
                 'jurisdictions': task_agencies}]}
        client = lambda *a, **k: {'text': json.dumps(reply, ensure_ascii=False)}
        u, _ = ra_tasks.interpret(question, client, ['SB17'], agencies)
        return u['tasks'][0]

    def test_third_role_needs_several_agencies(self):
        q = 'SB17의 허가 날짜를 알려줘'
        self.assertEqual(len(self.plan(q, [], ['CA', 'EU', 'US'])['source_roles']), 3)
        single = self.plan(q, ['US'], ['CA', 'EU', 'US'])
        self.assertEqual(single['source_roles'], ['label', 'correspondence'])
        self.assertEqual(single['roles_trimmed'], ['decision_summary'])


class ClaimFormatSlips(unittest.TestCase):
    def test_repeated_task_or_product_id_is_merged_not_fatal(self):
        from rag.ra_models import SpanClaim
        claim = SpanClaim.model_validate({'text': 'x', 'kind': 'source_fact', 'products': ['SB17', 'SB17'],
                                          'claim_basis': 'source_statement', 'task_ids': ['T1', 'T1'],
                                          'citation_span_ids': ['S1']}, context={'task_ids': ['T1']})
        self.assertEqual((claim.task_ids, claim.products), (['T1'], ['SB17']))


class AtomicNeeds(unittest.TestCase):
    def test_each_fact_and_item_becomes_a_need_with_its_own_query(self):
        from rag.ra_needs import atomic_tasks
        t = {'id': 'T1', 'request': 'EU 최초 허가일과 MAH', 'queries': ['EU authorisation'], 'source_roles': ['label'],
             'intent': {'intent': 'fact_lookup', 'request_quote': 'x'}, 'topics': ['design'],
             'answer_facets': [{'facet': 'fact', 'detail': '', 'ask': 'EU 최초 허가일', 'query': 'date of first authorisation', 'items': []},
                               {'facet': 'dose_route', 'detail': '', 'ask': '용량', 'query': 'dose', 'items': ['55 kg 이하', '85 kg 초과']},
                               {'facet': 'calculation', 'detail': '', 'ask': '기간', 'query': '', 'items': []}]}
        open_task = {**t, 'id': 'T2', 'answer_facets': []}
        tasks = atomic_tasks({'tasks': [t, open_task]})['tasks']
        self.assertEqual([x['id'] for x in tasks], ['T1.1', 'T1.2', 'T1.3', 'T1.req', 'T2'])
        self.assertEqual(tasks[0]['queries'], ['date of first authorisation'])
        self.assertEqual(tasks[1]['queries'], ['dose 55 kg 이하'])
        self.assertEqual(tasks[1]['request'], '용량 55 kg 이하')
        self.assertEqual(tasks[0]['topics'], [])
        self.assertEqual(tasks[3]['topics'], ['design'])  # the safety-net request keeps the task's topics
        self.assertEqual({x['need_of'] for x in tasks}, {'T1', 'T2'})


class UnitFusedRerank(unittest.TestCase):
    def test_unit_query_and_fusion_protect_strong_hybrid_hits(self):
        from types import SimpleNamespace
        from rag import ra_retrieval
        seen = []

        class Reverse:  # scores the hybrid order backwards
            def rank(self, query, hits, chunks, limit):
                seen.append(query)
                return [(float(n), i) for n, (_, i) in enumerate(hits)][:limit]
        corpus = SimpleNamespace(_ra_reranker=Reverse(), chunks={})
        hits = [(0, i) for i in range(10)]  # hybrid order 0..9
        task = {'request': 'r', 'queries': ['q'], 'answer_facets': [{'facet': 'fact', 'ask': 'EU 최초 허가일', 'items': []}]}
        replaced = [i for _, i in ra_retrieval.rerank(corpus, task, hits, 3, 'ask_unit', 'SB17', 'EU')]
        fused = [i for _, i in ra_retrieval.rerank(corpus, task, hits, 3, 'ask_unit', 'SB17', 'EU', fuse=True)]
        self.assertEqual(replaced, [9, 8, 7])
        self.assertIn(0, fused)  # the hybrid top hit survives fusion
        self.assertTrue(seen[0].startswith('SB17 Pyzchiva EMA: ') and seen[0].endswith('EU 최초 허가일'))

    def test_fquery_unit_uses_the_english_per_fact_query(self):
        from types import SimpleNamespace
        from rag import ra_retrieval
        seen = []

        class Record:
            def rank(self, query, hits, chunks, limit):
                seen.append(query)
                return [(0.0, i) for _, i in hits][:limit]
        corpus = SimpleNamespace(_ra_reranker=Record(), chunks={})
        task = {'request': 'r', 'queries': ['task query'],
                'answer_facets': [{'facet': 'fact', 'ask': 'EU 최초 허가일', 'query': 'date of first authorisation', 'items': []}]}
        ra_retrieval.rerank(corpus, task, [(0, 1)], 1, 'fquery_unit', 'SB17', 'EU', fuse=True)
        self.assertEqual(seen, ['SB17 Pyzchiva EMA: date of first authorisation'])
        seen.clear()
        ra_retrieval.rerank(corpus, {**task, 'answer_facets': []}, [(0, 1)], 1, 'fquery_unit', 'SB17', 'EU')
        self.assertEqual(seen, ['SB17 Pyzchiva EMA: task query'])  # open task: the task's English query
        self.assertEqual(ra_retrieval.DEFAULT_PROFILE['rerank'], 'fquery_unit')


class PlannerTopics(unittest.TestCase):
    def test_fixed_queries_follow_planner_topics(self):
        q = 'SB17의 안전성 결과를 알려줘'
        names, queries = ra_topic_queries.for_task(q, task(q, topics=['immunogenicity']), 1)
        self.assertEqual(names, ['immunogenicity'])
        self.assertTrue(queries)
        legacy = {k: v for k, v in task(q).items() if k != 'topics'}
        self.assertEqual(ra_topic_queries.for_task(q, legacy, 1)[0], ['safety'])


if __name__ == '__main__':
    unittest.main()
