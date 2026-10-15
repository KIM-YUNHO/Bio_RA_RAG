"""The planner's closed-set answer facets and topics drive requirements, prompt modules and topic queries."""
import unittest

from rag import ra_obligations, ra_prompt, ra_topic_queries


def task(quote, intent='fact_lookup', facets=(), topics=(), request=None):
    return {'id': 'T1', 'request': request or quote, 'queries': ['q'], 'source_roles': ['assessment'],
            'intent': {'intent': intent, 'request_quote': quote}, 'intent_check': {'quote_verified': True},
            'answer_facets': [{'facet': f, 'detail': d} for f, d in facets], 'topics': list(topics)}


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
