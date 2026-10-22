"""Issue guide: issues in the document bundle, held-out products, and a run with a fake RA service and a fake model."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from rag_service import internal_documents, issue_guide, notebook_api
from rag_service.workspaces import WorkspaceStore

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = json.loads((ROOT / 'docs/demo/issue_guide_v5/01_ISSUE_DOCUMENTS.json').read_text(encoding='utf-8'))
PRODUCTS = ['ABP654', 'BAT2206', 'BMAB1200', 'CTP43', 'SB17']


def report(product, page, text):
    return {'results': [{'status': 'answered', 'answer_ko': text,
                         'claims': [{'text': text, 'products': [product], 'sources': [{'evidence': 'E1', 'quote': 'quoted sentence'}]}],
                         'evidence': {'E1': {'document_id': f'EMA:{product}', 'version_id': 'v1', 'pdf_page': page, 'title': f'{product} EPAR',
                                             'product_id': product, 'source': 'EMA', 'jurisdiction': 'EU', 'doc_group': 'assessment'},
                                      'E2': {'document_id': 'unused', 'version_id': 'v1', 'pdf_page': 1, 'product_id': product}}}]}


class FakeJobs:
    """The RA service as the guide uses it: submit one question, read the finished job."""
    def __init__(self, store, product='BMAB1200'):
        self.workspaces, self.pipeline, self.product = store, SimpleNamespace(products=PRODUCTS), product
        self.asked, self.jobs, self.busy_once = [], {}, True

    def submit(self, payload):
        if self.busy_once:  # a user's question is running: the guide waits and retries
            self.busy_once = False
            raise BlockingIOError('busy')
        self.asked.append(payload)
        job_id = f'{len(self.asked):032x}'
        self.jobs[job_id] = {'id': job_id, 'state': 'completed', 'report': report(self.product, 30 + len(self.asked), f'답변 {len(self.asked)}')}
        return {'id': job_id}

    def get(self, job_id):
        return deepcopy(self.jobs[job_id])


def item(title, text, basis='public', ids=('P1',)):
    return {'title': title, 'text': text, 'basis': basis, 'source_ids': list(ids)}


PLAN = {'hypotheses': [{'attribute': '전하 변이(C-말단 Lys)', 'reason': '염기성 변이 차이는 결합 측정에 영향을 줄 수 있다.'},
                       {'attribute': 'Fc 산화', 'reason': 'Met 산화는 FcRn 결합을 낮춘다.'}],
        'questions': [{'topic': 'precedents', 'text': '우스테키누맙 바이오시밀러의 FcRn 결합 차이 사례는?'},
                      {'topic': 'cause', 'text': '염기성 변이(C-terminal lysine) 차이는 어떻게 확인됐나?'},
                      {'topic': 'cause', 'text': 'SB17 심사에서 Fc 산화는?'},  # names a held-out product: dropped, never sent
                      {'topic': 'cause', 'text': 'Fc 산화(Met oxidation) 차이의 PK 영향 판단은?'},
                      {'topic': 'guideline', 'text': '범위 밖 품질 결과의 정당화에 대한 지침 요구는?'}]}
FOLLOWUP = {'questions': ['carboxypeptidase B 처리로 C-말단 Lys 차이를 확인한 사례와 PK 판단은?']}
ANSWER = {'situation': 'IV vial 3 lot만 범위 하한 미만이다 [U03].',
          'candidates': [{'name': '제형 매트릭스 간섭', 'likelihood': 'medium', 'support': 'IV에서만 낮다 (U03, U04)', 'against': '시험은 유효하다',
                          'confirm': 'buffer exchange 후 재측정', 'source_ids': ['U03', 'U04']},
                         {'name': '전하 변이(C-말단 Lys)', 'likelihood': 'medium', 'support': '다른 제품은 염기성 변이를 C-말단 Lys로 설명했다',
                          'against': 'FcRn과의 직접 연결 근거는 없다', 'confirm': 'CPB 처리 전후 FcRn 재측정', 'source_ids': ['P1', 'NOPE']}],
          'sections': {'experiments': [item('CPB 처리 재시험', 'CPB 처리 전후 FcRn 결합을 비교한다', 'suggestion', ['P2'])],
                       'response': [item('PFS 결과', 'PFS 결과는 범위 안이다', 'internal', ['U04'])],
                       'mock_questions': [{'question': 'IV에서만 낮은 이유는?', 'background': '제형 차이', 'prepare': 'CEX 자료', 'source_ids': ['U03']}],
                       'precedents': [item('근거 없는 사례', '공개 근거 없이 쓴 문장', 'public', ['U03'])]}}


class FakeModel:
    """Answers the plan, follow-up and writing calls by schema name."""
    def __init__(self, answers=None):
        self.answers = answers or {'issue_search_plan': PLAN, 'issue_search_followup': FOLLOWUP, 'issue_guide': ANSWER}
        self.prompts = []

    def __call__(self, messages, json_schema=None):
        self.prompts.append((messages, json_schema))
        return {'text': json.dumps(self.answers[json_schema['name']], ensure_ascii=False), 'prompt_tokens': 1, 'completion_tokens': 1,
                'model_reported': 'fake'}

    def close(self):
        pass


class IssueBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = WorkspaceStore(Path(self.tmp.name) / 'notebooks')
        self.project_id = self.store.create({'name': 'v5', 'ingredient': 'ustekinumab'})['id']

    def tearDown(self):
        self.tmp.cleanup()

    def test_issue_is_stored_and_served_without_the_answer_key(self):
        self.store.register_documents(self.project_id, deepcopy(BUNDLE))
        project = self.store.get(self.project_id)
        self.assertIn('answer_key', project['issues'][0])
        served = internal_documents.issue_list(project)
        self.assertEqual([i['id'] for i in served], ['ISSUE-IV-FCRN'])
        self.assertNotIn('answer_key', served[0])
        self.assertTrue(served[0]['has_answer_key'])
        self.assertEqual(internal_documents.issue_list(project, '2026-03-19'), [], 'not raised yet on that date')
        self.assertNotIn('issues', notebook_api.public(project))
        # Re-importing the same bundle changes nothing.
        before = project['revision']
        self.store.register_documents(self.project_id, deepcopy(BUNDLE))
        self.assertEqual(self.store.get(self.project_id)['revision'], before)

    def test_issue_text_may_not_name_a_blocked_term_and_units_must_exist(self):
        bundle = deepcopy(BUNDLE)
        bundle['issues'][0]['summary'] = 'SB17과 같은 결과'
        with self.assertRaisesRegex(ValueError, '차단'):
            self.store.register_documents(self.project_id, bundle)
        bundle = deepcopy(BUNDLE)
        bundle['issues'][0]['unit_ids'].append('U99')
        with self.assertRaisesRegex(ValueError, 'U99'):
            self.store.register_documents(self.project_id, bundle)


class GuideRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = WorkspaceStore(root / 'notebooks')
        self.project_id = self.store.create({'name': 'v5', 'ingredient': 'ustekinumab'})['id']
        self.store.register_documents(self.project_id, deepcopy(BUNDLE))
        self.model = FakeModel()
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def run_guide(self, jobs):
        service = issue_guide.IssueGuideService(jobs, self.root / 'guides', chat_factory=lambda folder: self.model, poll=0.01)
        summary = service.submit(self.project_id, {'issue_id': 'ISSUE-IV-FCRN'})
        service.executor.shutdown(wait=True)
        return service, service.get(self.project_id, summary['id'])

    def test_planned_and_follow_up_questions_search_without_the_held_out_product(self):
        jobs = FakeJobs(self.store)
        service, record = self.run_guide(jobs)
        self.assertEqual(record['state'], 'completed', record.get('error'))
        self.assertEqual([m[1]['name'] for m in self.model.prompts], ['issue_search_plan', 'issue_search_followup', 'issue_guide'])
        # Four planned questions (one dropped for naming a held-out product) and one follow-up, in order.
        self.assertEqual([p['question'] for p in jobs.asked], [q['text'] for q in PLAN['questions'] if 'SB17' not in q['text']] + FOLLOWUP['questions'])
        self.assertEqual([(q['id'], q['round'], q['state']) for q in record['questions']],
                         [('Q1', 1, 'completed'), ('Q2', 1, 'completed'), ('Q3', 1, 'completed'), ('Q4', 1, 'completed'), ('Q5', 2, 'completed')])
        self.assertEqual(record['plan']['dropped'][0]['reason'], '차단어 SB17')
        self.assertEqual(record['plan']['hypotheses'][0]['attribute'], '전하 변이(C-말단 Lys)')
        for payload in jobs.asked:
            self.assertEqual(payload['products'], ['ABP654', 'BAT2206', 'BMAB1200', 'CTP43'])
        # One public source per cited page; uncited evidence is not offered.
        self.assertEqual([s['id'] for s in record['sources']], ['P1', 'P2', 'P3', 'P4', 'P5'])
        self.assertEqual(record['sources'][0]['evidence_id'], 'E1')
        # The follow-up call sees the first answers; no call ever sees the answer key or a blocked term.
        self.assertIn('답변 1', self.model.prompts[1][0][1]['content'])
        for messages, _ in self.model.prompts[:2]:
            self.assertIn("'ustekinumab 바이오시밀러'", messages[0]['content'], 'the ingredient is written into the instructions')
            self.assertNotIn('{ingredient}', messages[0]['content'])
        key = self.store.get(self.project_id)['issues'][0]['answer_key']
        for messages, _ in self.model.prompts:
            prompt = json.dumps(messages, ensure_ascii=False)
            for text in [key['summary'], *(p['text'] for p in key['points'])]:
                self.assertNotIn(text, prompt)
            for term in BUNDLE['blocked_terms']:
                self.assertNotIn(term, prompt)
            self.assertNotIn('P17', messages[0]['content'], 'no product alias in the instructions')
        schema = self.model.prompts[2][1]
        allowed = schema['schema']['properties']['candidates']['items']['properties']['source_ids']['items']['enum']
        self.assertEqual(allowed, ['P1', 'P2', 'P3', 'P4', 'P5', 'U01', 'U02', 'U03', 'U04', 'U05', 'U07'])
        guide = record['guide']
        self.assertEqual(guide['situation'], 'IV vial 3 lot만 범위 하한 미만이다.', 'IDs the model wrote into prose are removed')
        cause = guide['sections'][0]
        self.assertEqual((cause['key'], [(c['rank'], c['name'], c['likelihood_label']) for c in cause['items']]),
                         ('cause', [(1, '제형 매트릭스 간섭', '중간'), (2, '전하 변이(C-말단 Lys)', '중간')]))
        self.assertEqual(cause['items'][0]['support'], 'IV에서만 낮다')
        self.assertEqual(cause['items'][1]['source_ids'], ['P1'], 'unknown IDs are dropped')
        self.assertEqual([s['key'] for s in guide['sections']], ['cause', 'experiments', 'response', 'mock_questions', 'precedents'])
        self.assertEqual(guide['sections'][1]['items'][0]['title'], 'CPB 처리 재시험')
        self.assertEqual(guide['sections'][3]['items'][0]['background'], '제형 차이')
        self.assertEqual([w['code'] for w in record['warnings']], ['public_without_source'])
        self.assertEqual([u['step'] for u in record['usage']], ['issue_search_plan', 'issue_search_followup', 'issue_guide'])
        review = {p['id']: p for p in record['review']['points']}
        self.assertEqual(review['K1']['where']['section'], '원인 후보 2순위', 'a cause point says which rank it came from')
        self.assertTrue(review['K2']['covered'] and review['K4']['covered'])
        review = {k: p['covered'] for k, p in review.items()}
        summary = service.summary(record)
        self.assertEqual((summary['covered'], summary['searches'], summary['failed_questions']), (sum(review.values()), 5, 0))
        self.assertEqual([g['id'] for g in service.list(self.project_id)], [record['id']])

    def test_no_follow_up_when_the_model_asks_for_none(self):
        self.model = FakeModel({'issue_search_plan': PLAN, 'issue_search_followup': {'questions': []}, 'issue_guide': ANSWER})
        jobs = FakeJobs(self.store)
        _, record = self.run_guide(jobs)
        self.assertEqual(record['state'], 'completed', record.get('error'))
        self.assertEqual(len(jobs.asked), 4)

    def test_a_point_counts_only_in_its_sections(self):
        key = self.store.get(self.project_id)['issues'][0]['answer_key']
        guide = issue_guide.normalize(deepcopy(ANSWER), ['P1', 'P2', 'U03', 'U04'])
        self.assertTrue(next(p for p in issue_guide.review(guide, key)['points'] if p['id'] == 'K2')['covered'])
        # The same words only in another product's precedent are not a proposal for this product.
        guide['sections'][0]['items'][1]['confirm'] = '재측정'
        guide['sections'][1]['items'] = [{'title': '재현', 'text': '재현 시험을 한다', 'basis': 'suggestion', 'basis_label': 'AI 제안', 'source_ids': []}]
        guide['sections'][4]['items'] = [{'title': 'ABP654', 'text': 'ABP654는 CPB 처리 자료를 냈다', 'basis': 'public',
                                          'basis_label': '공개 근거', 'source_ids': ['P1']}]
        self.assertFalse(next(p for p in issue_guide.review(guide, key)['points'] if p['id'] == 'K2')['covered'])

    def test_a_finished_guide_can_be_deleted_but_not_one_being_made(self):
        service, record = self.run_guide(FakeJobs(self.store))
        path = f"/api/projects/{self.project_id}/issue-guides/{record['id']}/delete"
        self.assertEqual(notebook_api.post(SimpleNamespace(issue_guides=service), path, {}), {'deleted': record['id']})
        self.assertEqual(service.list(self.project_id), [])
        self.assertFalse((self.root / 'guides' / (record['id'] + '_api')).exists())
        with self.assertRaises(KeyError):
            service.get(self.project_id, record['id'])
        running = 'f' * 32
        service.records[running] = {'id': running, 'project_id': self.project_id, 'state': 'running', 'created_at': '2026'}
        with self.assertRaisesRegex(ValueError, '만드는 중'):
            service.delete(self.project_id, running)
        with self.assertRaises(KeyError):
            service.delete('0' * 32, running)

    def test_a_cited_held_out_product_fails_the_guide(self):
        _, record = self.run_guide(FakeJobs(self.store, product='SB17'))
        self.assertEqual(record['state'], 'failed')
        self.assertIn('SB17', record['error'])
        self.assertEqual([m[1]['name'] for m in self.model.prompts], ['issue_search_plan'], 'no further model call after a leak')

    def test_one_guide_at_a_time_and_only_registered_issues(self):
        service = issue_guide.IssueGuideService(FakeJobs(self.store), self.root / 'guides', chat_factory=lambda folder: self.model)
        with self.assertRaisesRegex(ValueError, '이슈'):
            service.submit(self.project_id, {'issue_id': 'NOPE'})
        service.busy = True
        with self.assertRaises(BlockingIOError):
            service.submit(self.project_id, {'issue_id': 'ISSUE-IV-FCRN'})


if __name__ == '__main__':
    unittest.main()
