"""Memo units preserve source roles, uncertainty and citations through real persistence/API flows."""
from copy import deepcopy
import http.client
import json
import unittest
from uuid import uuid4

from rag_service.workspace import memo_items, notebook_api
from rag_service.reviews import RevisionConflict
from rag_service.workspace.workspaces import source_packet

from tests.support.http_server import serve
from tests.service.test_workspace_http import JOB_ID, fixture_jobs


class MemoItemTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_jobs()
        self.jobs = self.fixture.__enter__()
        self.store = self.jobs.workspaces
        self.project = self.store.create({'name': 'Isolated source replay', 'ingredient': 'ustekinumab'})
        # Public SB17 assessment values, used here as persistence input, not an LLM accuracy result.
        result = self.jobs.jobs[JOB_ID]['report']['results'][0]
        result['claims'][0]['text'] = 'IV FcRn observed range: 98–102%.'
        result['claims'][0]['support_review'] = {'status': 'supported'}
        result['claims'].append({**deepcopy(result['claims'][0]), 'id': 'C2',
                                 'text': 'Case-specific similarity range: 107–127%.', 'kind': 'applicant_position'})
        result['claims'].append({**deepcopy(result['claims'][0]), 'id': 'C3',
                                 'text': 'A hypothetical internal 99% does not establish acceptance.', 'kind': 'interpretation'})
        self.jobs.persist(self.jobs.jobs[JOB_ID])

    def tearDown(self):
        self.fixture.__exit__(None, None, None)

    def source(self, **extra):
        return source_packet(self.jobs, {'job_id': JOB_ID, 'result_index': 0, 'kind': 'answer', **extra})

    def add(self, source=None, **extra):
        payload = {'request_id': uuid4().hex, 'body': 'Editable working notes', 'stage_ids': ['cmc'],
                   'products': [], 'tags': [], **extra}
        return self.store.add(self.project['id'], payload, source)['memos'][0]

    def revise(self, memo, **extra):
        payload = {k: deepcopy(memo[k]) for k in ('body', 'stage_ids', 'products', 'tags', 'archived')}
        return self.store.revise(self.project['id'], memo['id'],
                                 {**payload, 'expected_revision': memo['revision'], **extra})['memos'][0]

    def test_selected_claims_keep_roles_quotes_and_unknown_states(self):
        packet = self.source(claim_ids=['C2', 'C1'])
        self.assertEqual([c['id'] for c in packet['claims']], ['C1', 'C2'])
        self.assertEqual(packet['key'], self.source(claim_ids=['C1', 'C2'])['key'])
        memo = self.add(packet, annotations=[{'key': 'C1', 'type': 'fact', 'subject': 'SB17 IV FcRn', 'modality': 'reported'},
                                             {'key': 'C2', 'type': 'criterion', 'subject': 'SB17 IV FcRn'}])
        observed, criterion = memo['items']
        self.assertIn('98–102%', observed['text'])
        self.assertIn('107–127%', criterion['text'])
        self.assertEqual(criterion['statement_role'], 'applicant_position')
        self.assertEqual(observed['source_quotes'][0]['unit_id'], 'unit1')
        self.assertEqual(observed['source_quotes'], packet['claims'][0]['sources'])
        self.assertEqual(observed['source_locator']['report_sha256'], packet['report_sha256'])
        self.assertEqual(observed['model_support_review'], {'status': 'supported'})
        self.assertEqual(observed['review_status'], 'unreviewed')
        self.assertEqual(observed['run_validity'], 'not_disclosed')
        self.assertEqual(observed['data_maturity'], 'not_disclosed')
        self.assertEqual(observed['annotation_basis'], 'user_entered_unverified')
        self.assertNotIn('mean', observed)

    def test_invalid_selection_or_forged_source_fields_are_rejected(self):
        for ids in ([], ['missing'], ['C1', 'C1'], 'C1', [None]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.source(claim_ids=ids)
        for field, value in [('text', 'passed'), ('source_quotes', []), ('review_status', 'approved'),
                             ('statement_role', 'regulator_position'), ('type', 'approved')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.add(self.source(), annotations=[{'key': 'C1', field: value}])
        self.assertEqual(self.store.get(self.project['id'])['memos'], [])

    def test_body_edits_and_classification_cannot_rewrite_rag_claim(self):
        memo = self.add(self.source())
        changed = self.revise(memo, body='Reviewer opinion only', annotations=[
            {'key': 'C1', 'type': 'interpretation', 'modality': 'predicted', 'note': 'Needs verification'}])
        self.assertEqual(changed['items'][0]['text'], memo['items'][0]['text'])
        self.assertEqual(changed['items'][0]['source_quotes'], memo['items'][0]['source_quotes'])
        self.assertEqual(changed['items'][0]['statement_role'], 'source_fact')
        self.assertEqual(changed['history'][0]['items'], memo['items'])
        self.assertEqual(changed['history'][0]['body'], memo['body'])
        self.assertEqual(changed['items'][2]['type'], 'interpretation')
        with self.assertRaises(RevisionConflict):
            self.revise(memo, body='stale update')

    def test_manual_unit_tracks_body_without_losing_identity_or_notes(self):
        memo = self.add(annotations=[{'key': 'manual', 'type': 'action', 'modality': 'planned', 'note': 'Mock action'}])
        changed = self.revise(memo, body='Request the undisclosed raw data')
        self.assertEqual(changed['items'][0]['text'], changed['body'])
        self.assertEqual(changed['items'][0]['id'], memo['items'][0]['id'])
        self.assertEqual(changed['items'][0]['note'], 'Mock action')
        self.assertEqual(changed['items'][0]['source_quotes'], [])

    def test_legacy_conversion_is_explicit_hash_checked_and_non_destructive(self):
        memo = self.add(self.source())
        project = self.store.get(self.project['id'])
        old = project['memos'][0]
        old.pop('items')
        old['source'].pop('claims')
        old['body'] = 'Previously edited memo text'
        old['archived'] = True
        self.store.write(project)
        path = f"/api/projects/{project['id']}/memos/{memo['id']}/structure"
        self.assertNotIn('items', self.store.get(project['id'])['memos'][0])
        converted = notebook_api.post(self.jobs, path, {'expected_revision': 1})['memos'][0]
        self.assertEqual(converted['body'], old['body'])
        self.assertTrue(converted['archived'])
        self.assertEqual(len(converted['items']), 3)
        self.assertEqual(converted['revision'], 2)
        self.assertIsNone(converted['history'][0]['items'])
        # Hash mismatch must not alter any saved metadata/body, even on an already converted memo.
        self.jobs.jobs[JOB_ID]['report']['results'][0]['claims'][0]['text'] = 'Replaced report'
        before = self.store.get(project['id'])
        with self.assertRaises(ValueError):
            notebook_api.post(self.jobs, path, {'expected_revision': 2})
        self.assertEqual(self.store.get(project['id']), before)

    def test_task_views_keep_uncertainty_and_exclude_archived_items(self):
        active = self.add(self.source(claim_ids=['C1']), annotations=[{'key': 'C1', 'type': 'fact'}])
        archived = self.add()
        self.revise(archived, archived=True)
        project = self.store.get(self.project['id'])
        for task in memo_items.TASKS:
            view = memo_items.brief(project, task)
            rows = [row for group in view['groups'] for row in group['items']]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['memo_id'], active['id'])
            self.assertEqual(rows[0]['run_validity'], 'not_disclosed')
            self.assertEqual(rows[0]['source_quotes'], active['items'][0]['source_quotes'])
            self.assertEqual(view['audience'], 'internal')
            self.assertFalse(view['generated_by_model'])
            self.assertTrue(view['draft_only'])
        self.assertEqual(memo_items.brief(project, 'ra_preparation')['groups'][0]['type'], 'question')

    def test_new_routes_work_over_http(self):
        memo = self.add(self.source())
        project = self.store.get(self.project['id'])
        project['memos'][0].pop('items')
        self.store.write(project)
        with serve(self.jobs) as port:
            def request(method, path, body=None):
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
                try:
                    conn.request(method, path, None if body is None else json.dumps(body), {'Content-Type': 'application/json'})
                    reply = conn.getresponse()
                    value = json.loads(reply.read())
                    self.assertEqual(reply.status, 200, value)
                    return value
                finally:
                    conn.close()
            request('POST', f"/api/projects/{project['id']}/memos/{memo['id']}/structure", {'expected_revision': 1})
            view = request('GET', f"/api/projects/{project['id']}/briefs/result_review")
            self.assertEqual(sum(len(g['items']) for g in view['groups']), 3)
            self.assertIn('criterion', request('GET', '/api/projects')['item_definitions']['types'])


if __name__ == '__main__':
    unittest.main()
