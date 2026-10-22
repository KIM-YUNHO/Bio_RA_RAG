"""Deleting a pipeline: it leaves the list, its record is kept, and work still being made blocks it."""
import http.client
import json
import unittest

from tests.support.http_server import serve  # noqa: E402
from tests.service.test_workspace_http import fixture_jobs  # noqa: E402


class ProjectDeleteTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture_jobs()
        self.jobs = self.context.__enter__()
        self.server = serve(self.jobs)
        self.port = self.server.__enter__()

    def tearDown(self):
        self.server.__exit__(None, None, None)
        self.context.__exit__(None, None, None)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        try:
            conn.request(method, path, None if body is None else json.dumps(body), {'Content-Type': 'application/json'})
            reply = conn.getresponse()
            return reply.status, json.loads(reply.read())
        finally:
            conn.close()

    def create(self, name):
        status, project = self.call('POST', '/api/projects', {'name': name, 'ingredient': 'ustekinumab'})
        self.assertEqual(status, 200, project)
        return project

    def test_deleted_pipeline_leaves_the_list_and_its_name_can_be_reused(self):
        keep, gone = self.create('남길 파이프라인'), self.create('지울 파이프라인')
        self.assertEqual(self.call('POST', f"/api/projects/{gone['id']}/delete", {}), (200, {'id': gone['id'], 'deleted': True}))
        status, listed = self.call('GET', '/api/projects')
        self.assertEqual([p['id'] for p in listed['projects']], [keep['id']])
        self.assertEqual(self.call('GET', f"/api/projects/{gone['id']}")[0], 404)
        self.assertEqual(self.call('POST', f"/api/projects/{gone['id']}/delete", {})[0], 404)
        self.assertTrue((self.jobs.workspaces.directory / 'deleted' / (gone['id'] + '.json')).is_file())
        self.create('지울 파이프라인')

    def test_request_with_content_is_refused(self):
        project = self.create('내용 있는 삭제 요청')
        self.assertEqual(self.call('POST', f"/api/projects/{project['id']}/delete", {'name': project['name']})[0], 400)
        self.assertEqual(self.call('GET', f"/api/projects/{project['id']}")[0], 200)

    def test_work_still_being_made_blocks_deletion(self):
        project = self.create('작업 중인 파이프라인')
        for service in (self.jobs.work_drafts, self.jobs.issue_guides):
            with self.subTest(service=type(service).__name__):
                service.records['f' * 32] = {'id': 'f' * 32, 'project_id': project['id'], 'state': 'running'}
                try:
                    status, body = self.call('POST', f"/api/projects/{project['id']}/delete", {})
                    self.assertEqual(status, 409)
                    self.assertIn('끝난 뒤 삭제', body['error'])
                finally:
                    service.records.pop('f' * 32)
        self.assertEqual(self.call('POST', f"/api/projects/{project['id']}/delete", {})[0], 200)


if __name__ == '__main__':
    unittest.main()
