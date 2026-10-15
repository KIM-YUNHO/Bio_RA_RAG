"""Follow-up turns: inherited scope rules, standalone checks, planner wiring and the service flow."""
from http.server import ThreadingHTTPServer
import json
from threading import Event, Thread
import time
import unittest
import urllib.request

from rag import followup, ra_tasks
from rag_service.__main__ import Jobs, handler

FIVE = ['ABP654', 'BAT2206', 'BMAB1200', 'CTP43', 'SB17']
DOCS = [{'source': 'EMA', 'jurisdiction': 'EU'}, {'source': 'FDA', 'jurisdiction': 'US'}, {'source': 'Health Canada', 'jurisdiction': 'CA'}]
FIRST = {'question': 'EMA 문서에서 CTP43 평가할 때 중점적으로 본 이슈가 뭐인지 알려줄 수 있을까?',
         'understanding': {'products': ['CTP43'], 'jurisdictions': ['EU']},
         'results': [{'answer_ko': '중요한 확인된 위험은 없다고 기술했다. [E3]\n\n추가 확인이 필요한 요청:\n- x'}]}


class RuleTests(unittest.TestCase):
    def setUp(self): self.context = followup.context_from_report(FIRST)

    def test_context_keeps_named_scope_and_short_answer(self):
        self.assertEqual((self.context['products'], self.context['jurisdictions']), (['CTP43'], ['EU']))
        self.assertEqual(self.context['answer_summary'], '중요한 확인된 위험은 없다고 기술했다.')

    def test_previous_scope_is_offered_to_the_planner(self):
        # Keeping, replacing or widening the scope is the planner's reading of the follow-up.
        for q in ('이슈가 아예 없던거야?', '그럼 FDA는?', '다른 제품들도 같은 이슈가 있었어?'):
            r = followup.resolve(q, self.context)
            self.assertEqual(r['inherited'], {'products': ['CTP43'], 'jurisdictions': ['EU']})
        self.assertEqual(followup.resolve('SB17은 어땠어?', self.context)['own']['products'], ['SB17'])
        self.assertEqual(followup.resolve('첫 질문', None)['turn_kind'], 'first')

    def test_standalone_check(self):
        r = followup.resolve('이슈가 아예 없던거야?', self.context)
        ok = 'EMA 문서에서 CTP43 평가 시 중요 확인 위험이 없다는 것이 평가 이슈가 전혀 없었다는 뜻인가?'
        self.assertEqual(followup.check_standalone(ok, '이슈가 아예 없던거야?', r), (ok, 'model_standalone_accepted'))
        text, reason = followup.check_standalone('EMA 문서에서 CTP43과 SB17의 이슈는?', '이슈가 아예 없던거야?', r)
        self.assertEqual((text, reason), ('EMA 문서에서 CTP43에 대해 이슈가 아예 없던거야?', 'fallback_new_products_in_standalone'))
        # Dropping the carried names is the planner's decision; code does not re-add them.
        text, reason = followup.check_standalone('다른 제품들의 평가 이슈는?', '다른 제품들도?', r)
        self.assertEqual((text, reason), ('다른 제품들의 평가 이슈는?', 'model_standalone_accepted'))


class Client:
    provider = 'openai'
    def __init__(self, reply): self.reply = reply; self.schemas = []; self.systems = []
    def __call__(self, messages, json_schema=None, provider=None):
        self.schemas.append(json_schema['name']); self.systems.append(messages[0]['content'])
        return {'text': json.dumps(self.reply, ensure_ascii=False)}


def plan_reply(**extra):
    return {**extra, 'in_scope': True, 'products': ['CTP43'], 'jurisdictions': ['EU'],
            'jurisdiction_mentions': [{'jurisdiction': 'EU', 'role': 'regulator', 'name': 'EMA', 'quote': 'EMA 문서에서'}],
            'english_query': 'CT-P43 EMA assessment issues important identified risks',
            'tasks': [{'request': 'EMA 문서에서 CTP43 평가 이슈가 전혀 없었는지 확인해 달라.', 'queries': ['CT-P43 EMA major objections'],
                       'source_roles': ['assessment'], 'guideline_request_quote': '',
                       'intent': {'intent': 'regulatory_reasoning', 'request_quote': ''}}]}


class PlannerTests(unittest.TestCase):
    def test_follow_up_is_planned_and_scoped_from_the_standalone_question(self):
        context = followup.context_from_report(FIRST)
        standalone = 'EMA 문서에서 CTP43 평가 시 중요 확인 위험이 없다는 것이 평가 이슈가 전혀 없었다는 뜻인가?'
        client = Client(plan_reply(standalone_question=standalone))
        u, _ = ra_tasks.interpret('중요 확인 위험은 없다고 하는데, 이슈가 아예 없던거야?', client, FIVE, ['CA', 'EU', 'US'],
                                  selected_products=FIVE, selected_agencies=['CA', 'EU', 'US'], documents=DOCS, context=context)
        self.assertEqual(client.schemas, ['query_understanding_follow_up'])
        self.assertIn('Previous answer summary', client.systems[0])
        self.assertEqual(u['source_question'], standalone)
        self.assertEqual((u['products'], u['jurisdictions']), (['CTP43'], ['EU']))
        self.assertEqual(u['conversation']['standalone_check'], 'model_standalone_accepted')
        self.assertEqual(u['conversation']['user_question'], '중요 확인 위험은 없다고 하는데, 이슈가 아예 없던거야?')

    def test_follow_up_cannot_add_an_agency_the_user_did_not_name(self):
        context = followup.context_from_report(FIRST)
        standalone = 'EMA와 FDA 문서에서 CTP43 평가 이슈가 전혀 없었나?'
        reply = plan_reply(standalone_question=standalone)
        reply['jurisdictions'] = ['EU', 'US']
        reply['jurisdiction_mentions'].append({'jurisdiction': 'US', 'role': 'regulator', 'name': 'FDA', 'quote': 'FDA 문서'})
        u, _ = ra_tasks.interpret('이슈가 아예 없던거야?', Client(reply), FIVE, ['CA', 'EU', 'US'],
                                  selected_products=FIVE, selected_agencies=['CA', 'EU', 'US'], documents=DOCS, context=context)
        self.assertEqual(u['jurisdictions'], ['EU'])
        u, _ = ra_tasks.interpret('FDA는 어땠어?', Client(reply), FIVE, ['CA', 'EU', 'US'],
                                  selected_products=FIVE, selected_agencies=['CA', 'EU', 'US'], documents=DOCS, context=context)
        self.assertEqual(sorted(u['jurisdictions']), ['EU', 'US'])

    def test_single_turn_path_is_unchanged(self):
        client = Client(plan_reply())
        u, _ = ra_tasks.interpret(FIRST['question'], client, FIVE, ['CA', 'EU', 'US'],
                                  selected_products=FIVE, selected_agencies=['CA', 'EU', 'US'], documents=DOCS)
        self.assertEqual(client.schemas, ['query_understanding'])
        self.assertEqual((u['products'], u['jurisdictions']), (['CTP43'], ['EU']))
        self.assertNotIn('FOLLOW-UP', client.systems[0])
        self.assertEqual(u['source_question'], FIRST['question'])
        self.assertEqual(u['conversation']['turn_kind'], 'first')


class Pipeline:
    def __init__(self): self.contexts = []; self.done = Event()
    def summary(self): return {'mode': 'fixture'}
    def validate_request(self, question, *args):
        if not question: raise ValueError('Question required')
    def ask(self, question, products, jurisdictions, documents, progress, include_nondefault=False, context=None):
        self.contexts.append(context)
        standalone = question if context is None else 'EMA 문서에서 CTP43에 대해 ' + question
        return {'question': standalone, 'user_question': question, 'understanding': {'jurisdictions': ['EU']},
                'conversation': {'turn_kind': 'follow_up' if context else 'first', 'standalone_question': standalone},
                'results': [{'answer_ko': '중요한 확인된 위험은 없다. [E1]'}]}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = Pipeline(); self.jobs = Jobs(self.pipeline)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.jobs))
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.jobs.close(); self.server.shutdown(); self.server.server_close(); self.thread.join()

    def call(self, path, payload=None):
        request = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}'+path,
            data=json.dumps(payload).encode() if payload is not None else None, headers={'Content-Type': 'application/json'})
        with self.opener.open(request) as reply: return json.load(reply)

    def wait(self, identity):
        for _ in range(200):
            job = self.jobs.get(identity)
            if job['state'] != 'running': return job
            time.sleep(.01)

    def test_second_turn_receives_first_turn_context(self):
        first = self.call('/api/questions', {'question': FIRST['question']}); self.wait(first['id'])
        self.assertFalse(first['follow_up'])
        second = self.call('/api/questions', {'question': '이슈가 아예 없던거야?', 'conversation_id': first['conversation_id']})
        self.wait(second['id'])
        self.assertTrue(second['follow_up']); self.assertEqual(second['conversation_id'], first['conversation_id'])
        self.assertIsNone(self.pipeline.contexts[0])
        self.assertEqual((self.pipeline.contexts[1]['products'], self.pipeline.contexts[1]['jurisdictions']), (['CTP43'], ['EU']))
        conversation = self.call('/api/conversations/' + first['conversation_id'])
        self.assertEqual([t['job_id'] for t in conversation['turns']], [first['id'], second['id']])
        self.assertEqual(conversation['turns'][1]['state'], 'completed')
        self.assertTrue(conversation['turns'][1]['standalone_question'].startswith('EMA 문서에서 CTP43'))
        self.assertEqual(self.jobs.get(second['id'])['conversation_id'], first['conversation_id'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call('/api/questions', {'question': 'x', 'conversation_id': 'f'*32})
        self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
