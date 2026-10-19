"""Issue guide: issues in the document bundle, held-out products, and a run with a fake RA service and a fake model."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from rag_service import internal_documents, issue_guide, notebook_api
from rag_service.workspaces import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
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


class FakeModel:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def __call__(self, messages, json_schema=None):
        self.prompts.append((messages, json_schema))
        return {'text': json.dumps(self.answer, ensure_ascii=False), 'prompt_tokens': 1, 'completion_tokens': 1, 'model_reported': 'fake'}

    def close(self):
        pass


def item(text, basis='public', ids=('P1',)):
    return {'text': text, 'basis': basis, 'source_ids': list(ids)}


ANSWER = {'summary': 'C-말단 라이신 차이를 먼저 확인한다 [P1].',
          'sections': {'cause': [item('전하 변이, 특히 C-말단 Lys 차이를 확인한다 (P1, U03)')],
                       'experiments': [item('CPB 처리 전후 FcRn 결합을 비교한다', 'suggestion', ['P2'])],
                       'response': [item('PFS 결과는 범위 안이다', 'internal', ['U04'])],
                       'mock_questions': [{'question': 'IV에서만 낮은 이유는?', 'why': '제형 차이', 'prepare': 'CEX 자료', 'source_ids': ['U03']}],
                       'precedents': [item('공개 근거 없이 쓴 문장', 'public', ['U03'])]}}


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

    def test_questions_may_not_name_a_blocked_term_and_units_must_exist(self):
        bundle = deepcopy(BUNDLE)
        bundle['issues'][0]['questions'][0]['text'] = 'SB17 심사에서 FcRn은?'
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
        self.model = FakeModel(ANSWER)
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def run_guide(self, jobs):
        service = issue_guide.IssueGuideService(jobs, self.root / 'guides', chat_factory=lambda folder: self.model, poll=0.01)
        summary = service.submit(self.project_id, {'issue_id': 'ISSUE-IV-FCRN'})
        service.executor.shutdown(wait=True)
        return service, service.get(self.project_id, summary['id'])

    def test_guide_searches_without_the_held_out_product_and_never_sends_the_answer_key(self):
        jobs = FakeJobs(self.store)
        service, record = self.run_guide(jobs)
        self.assertEqual(record['state'], 'completed', record.get('error'))
        self.assertEqual(len(jobs.asked), 4)
        for payload in jobs.asked:
            self.assertEqual(payload['products'], ['ABP654', 'BAT2206', 'BMAB1200', 'CTP43'])
        # One public source per cited page; uncited evidence is not offered.
        self.assertEqual([s['id'] for s in record['sources']], ['P1', 'P2', 'P3', 'P4'])
        self.assertEqual(record['sources'][0]['evidence_id'], 'E1')
        messages, schema = self.model.prompts[0]
        prompt = json.dumps(messages, ensure_ascii=False)
        key = self.store.get(self.project_id)['issues'][0]['answer_key']
        for text in [key['summary'], *(p['text'] for p in key['points'])]:
            self.assertNotIn(text, prompt)
        for term in BUNDLE['blocked_terms']:
            self.assertNotIn(term, prompt)
        allowed = schema['schema']['properties']['sections']['properties']['cause']['items']['properties']['source_ids']['items']['enum']
        self.assertEqual(allowed, ['P1', 'P2', 'P3', 'P4', 'U01', 'U02', 'U03', 'U04', 'U05', 'U07'])
        guide = record['guide']
        self.assertEqual(guide['summary'], 'C-말단 라이신 차이를 먼저 확인한다.', 'IDs the model wrote into prose are removed')
        self.assertEqual(guide['sections'][0]['items'][0]['text'], '전하 변이, 특히 C-말단 Lys 차이를 확인한다')
        self.assertEqual([w['code'] for w in record['warnings']], ['public_without_source'])
        review = {p['id']: p['covered'] for p in record['review']['points']}
        self.assertTrue(review['K1'] and review['K2'] and review['K4'])
        self.assertEqual(service.summary(record)['covered'], sum(review.values()))
        self.assertEqual([g['id'] for g in service.list(self.project_id)], [record['id']])

    def test_a_point_counts_only_in_its_sections(self):
        key = self.store.get(self.project_id)['issues'][0]['answer_key']
        guide = issue_guide.normalize(deepcopy(ANSWER), ['P1', 'P2', 'U03', 'U04'])
        self.assertTrue(next(p for p in issue_guide.review(guide, key)['points'] if p['id'] == 'K2')['covered'])
        # The same words only in another product's precedent are not a proposal for this product.
        guide['sections'][1]['items'] = [{'text': '재현 시험을 한다', 'basis': 'suggestion', 'basis_label': 'AI 제안', 'source_ids': []}]
        guide['sections'][4]['items'] = [{'text': 'ABP654는 CPB 처리 자료를 냈다', 'basis': 'public', 'basis_label': '공개 근거', 'source_ids': ['P1']}]
        self.assertFalse(next(p for p in issue_guide.review(guide, key)['points'] if p['id'] == 'K2')['covered'])

    def test_a_cited_held_out_product_fails_the_guide(self):
        _, record = self.run_guide(FakeJobs(self.store, product='SB17'))
        self.assertEqual(record['state'], 'failed')
        self.assertIn('SB17', record['error'])
        self.assertEqual(self.model.prompts, [], 'no model call after a leak')

    def test_one_guide_at_a_time_and_only_registered_issues(self):
        service = issue_guide.IssueGuideService(FakeJobs(self.store), self.root / 'guides', chat_factory=lambda folder: self.model)
        with self.assertRaisesRegex(ValueError, '이슈'):
            service.submit(self.project_id, {'issue_id': 'NOPE'})
        service.busy = True
        with self.assertRaises(BlockingIOError):
            service.submit(self.project_id, {'issue_id': 'ISSUE-IV-FCRN'})


if __name__ == '__main__':
    unittest.main()
