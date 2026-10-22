from copy import deepcopy
import unittest

from rag.retrieval.plan_query import plan
from rag.retrieval.request_scope import bind_request_scope


class RequestScopeTests(unittest.TestCase):
    def setUp(self):
        self.u={'intent':'fact_lookup','products':['SB17'],'jurisdictions':['EU'],
            'studies':['ST_SB17_1001'],'study_scope':'specified','topic':'design',
            'doc_groups':['assessment'],'jurisdiction_mentions':[]}
        self.docs=[{'source':'EMA','jurisdiction':'EU'},{'source':'FDA','jurisdiction':'US'},
            {'source':'ClinicalTrials.gov','jurisdiction':'sponsor'}]

    def bind(self, question, u, products=('SB17',), agencies=('EU',), **kwargs):
        return bind_request_scope(question,u,products,agencies,documents=self.docs,**kwargs)

    def test_generic_study_cannot_override_selected_ema(self):
        self.u['jurisdictions']=['sponsor']
        self.u['jurisdiction_mentions']=[{'jurisdiction':'sponsor','role':'regulator','quote':'SB17-1001 시험'}]
        u=self.bind('SB17-1001 시험 설계는?',self.u,selected_agencies=['EU'])
        self.assertEqual(u['jurisdictions'],['EU'])
        self.assertEqual(u['request_scope']['status'],'applied')
        self.assertEqual(plan(u,agencies=['EU'],products=['SB17'])[0]['jurisdiction'],'EU')
        self.assertEqual(self.u['jurisdictions'],['sponsor'])

    def test_generic_label_hallucination_does_not_replace_pinned_product(self):
        self.u['products']=['ABP654'];self.u['studies']=[]
        u=self.bind('Health Canada 우스테키누맙 Cmax는?',self.u,agencies=['CA'],
            selected_products=['SB17'],selected_agencies=['CA'],document_scope=True)
        self.assertEqual(u['products'],['SB17'])
        self.assertFalse(u['request_scope']['conflicts'])

    def test_named_conflicting_product_stops_without_substitution(self):
        self.u['products']=['ABP654']
        u=self.bind('ABP654의 Cmax는?',self.u,selected_products=['SB17'])
        self.assertEqual(u['request_scope']['status'],'conflict')
        self.assertEqual(plan(u,agencies=['EU'],products=['SB17']),[])
        self.assertTrue(u['scope_warnings'])

    def test_named_regulator_conflict_and_comparator_region_are_distinct(self):
        self.u['jurisdictions']=['US']
        self.u['jurisdiction_mentions']=[{'jurisdiction':'US','role':'regulator','name':'FDA','quote':'FDA 문서'}]
        u=self.bind('FDA 문서의 결과는?',self.u,selected_agencies=['EU'])
        self.assertEqual(plan(u),[])
        self.u['jurisdiction_mentions']=[{'jurisdiction':'US','role':'comparator','name':'US','quote':'US Stelara'}]
        u=self.bind('US Stelara 대비 값은?',self.u,selected_agencies=['EU'])
        self.assertEqual(u['request_scope']['status'],'applied')

    def test_regulator_mention_checks_proper_nouns_not_quote_formatting(self):
        from rag.retrieval.request_scope import regulator_mention
        q = 'Pyzchiva의 EU 최초 판매허가일과 미국 승인일을 정리해줘.'
        # The copied name must be printed; the quote's format does not matter.
        self.assertTrue(regulator_mention(q, {'jurisdiction': 'EU', 'role': 'regulator', 'name': 'EU', 'quote': 'EU ... 판매허가일'}))
        self.assertTrue(regulator_mention(q, {'jurisdiction': 'US', 'role': 'regulator', 'name': '미국', 'quote': '미국에서의 승인일'}))
        # An invented agency name does not narrow; a reference product is a comparator's origin.
        self.assertFalse(regulator_mention(q, {'jurisdiction': 'US', 'role': 'regulator', 'name': 'FDA', 'quote': 'FDA 승인일'}))
        arms = 'SB17 시험에서 미국 Stelara와 유럽 Stelara 군의 PK 결과를 비교해줘.'
        self.assertFalse(regulator_mention(arms, {'jurisdiction': 'US', 'role': 'regulator', 'name': '미국', 'quote': '미국 Stelara'}))
        self.assertFalse(regulator_mention(q, {'jurisdiction': 'US', 'role': 'comparator', 'name': '미국', 'quote': '미국'}))
        self.assertFalse(regulator_mention('NCT04967508 계획', {'jurisdiction': 'sponsor', 'role': 'regulator', 'name': 'NCT04967508', 'quote': ''}))

    def test_plan_validation_flags_names_not_in_the_question(self):
        import json
        from rag.ra.ra_models import ResearchPlan
        task = {'request': 'x', 'queries': ['x'], 'source_roles': ['label'], 'intent': {'intent': 'fact_lookup', 'request_quote': 'x'}}
        reply = json.dumps({'in_scope': True, 'products': [], 'jurisdictions': ['EU', 'US'], 'english_query': '', 'tasks': [task],
                            'jurisdiction_mentions': [{'jurisdiction': 'EU', 'role': 'regulator', 'name': 'EU', 'quote': 'EU'},
                                                      {'jurisdiction': 'US', 'role': 'regulator', 'name': 'FDA', 'quote': 'FDA'}]})
        plan = ResearchPlan.model_validate_json(reply, context={'question': 'EU 허가일을 알려줘'})
        self.assertEqual([m.name_in_question for m in plan.jurisdiction_mentions], [True, False])

    def test_unpinned_unavailable_product_is_not_silently_replaced(self):
        self.u['products']=['ABP654']
        u=self.bind('암젠 제품의 값은?',self.u)
        self.assertEqual(u['request_scope']['status'],'conflict')
        self.assertEqual(plan(u),[])

    def test_empty_eligible_document_scope_stays_empty(self):
        u=self.bind('프로토콜 내용은?',self.u,products=[],agencies=[],document_scope=True)
        self.assertEqual(u['request_scope']['status'],'empty')
        self.assertEqual(plan(u,products=[],agencies=[]),[])

    def test_named_targets_narrow_the_selected_boundary(self):
        # 2026-10-13 18:53: all five products ticked, question names CT-P43 and EMA.
        five=['ABP654','BAT2206','BMAB1200','CTP43','SB17']
        self.u.update(products=['CTP43'],jurisdictions=['EU'],
            jurisdiction_mentions=[{'jurisdiction':'EU','role':'regulator','name':'EMA','quote':'EMA 문서'}])
        u=self.bind('EMA 문서에서 CTP43 평가할 때 중점적으로 본 이슈가 뭐야?',self.u,products=five,agencies=['CA','EU','US'],
            selected_products=five,selected_agencies=['CA','EU','US'])
        self.assertEqual((u['products'],u['jurisdictions']),(['CTP43'],['EU']))
        self.assertEqual(u['request_scope']['narrowed_by_question'],{'products':['CTP43'],'jurisdictions':['EU']})
        self.assertIn('CTP43',u['scope_warnings'][-1])
        # The same model output without the names in the question keeps the whole selection.
        u=self.bind('평가할 때 중점적으로 본 이슈가 뭐야?',self.u,products=five,agencies=['CA','EU','US'],
            selected_products=five,selected_agencies=['CA','EU','US'])
        self.assertEqual((u['products'],u['jurisdictions']),(five,['CA','EU','US']))
        self.assertEqual(u['request_scope']['narrowed_by_question'],{})

    def test_plan_enforces_boundaries_independently(self):
        self.u['products']=['SB17','ABP654'];self.u['jurisdictions']=['EU','US']
        units=plan(self.u,products=['SB17'],agencies=['EU'])
        self.assertEqual([(u['product'],u['jurisdiction']) for u in units],[('SB17','EU')])
        self.assertEqual(plan(self.u,products=[],agencies=['EU']),[])


if __name__=='__main__':unittest.main()
