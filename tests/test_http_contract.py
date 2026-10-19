"""What the RA service's HTTP layer promises, checked against each implementation (standard library and FastAPI).

The expected values are what the standard-library server answered on 2026-10-18 (tests/probe in the migration plan,
docs/project/FASTAPI_MIGRATION_PLAN_2026-10-18.md section 2): status codes, error texts, headers, body limits and the
same-origin rule. Anything the screen relies on is pinned here; the cases that are not (methods the service does not
define, an unexpected exception in a handler) are listed in the plan as known differences and are not pinned.
"""
import http.client
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from http_server import fastapi_available, serve  # noqa: E402
from test_workspace_http import JOB_ID, fixture_jobs  # noqa: E402
from test_internal_work import BUNDLE, FakeChat  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / 'rag_service/web'
STATIC = {'/': 'index.html', '/style.css': 'style.css', '/common.js': 'common.js', '/evidence-view.js': 'evidence-view.js',
          '/inventory.js': 'inventory.js', '/viewer.js': 'viewer.js', '/review-notes.js': 'review-notes.js', '/app.js': 'app.js',
          '/pipeline-notes.js': 'pipeline-notes.js', '/data-manager.js': 'data-manager.js', '/data-manager.css': 'data-manager.css',
          '/work-assist.js': 'work-assist.js'}
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8'}
JSON_TYPE = 'application/json; charset=utf-8'
MISSING = '찾을 수 없습니다.'
UNKNOWN_JOB = '등록되지 않은 질문입니다.'
UNKNOWN_ID = 'b' * 32
SESSION_ID = '-'.join('e' * n for n in (8, 4, 4, 4, 12))
EVIDENCE = f'/api/jobs/{JOB_ID}/evidence/0/E1'


class Contract:
    kind = None

    @classmethod
    def setUpClass(cls):
        cls.stack = []
        jobs = fixture_jobs()
        cls.jobs = jobs.__enter__()
        cls.stack.append(jobs)
        cls.jobs.work_drafts.chat_factory = lambda folder: FakeChat()  # never a provider call
        server = serve(cls.jobs, cls.kind)
        cls.port = server.__enter__()
        cls.stack.append(server)

    @classmethod
    def tearDownClass(cls):
        for context in reversed(cls.stack):
            context.__exit__(None, None, None)

    def call(self, method, path, body=None, headers=None, host=None, raw=None):
        """(status, headers, bytes). body is JSON-encoded unless raw bytes are given; POST defaults to a JSON content type."""
        headers = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if method == 'POST':
            headers.setdefault('Content-Type', 'application/json')
        for attempt in range(3):
            conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=15)
            try:
                conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
                conn.putheader('Host', host or f'127.0.0.1:{self.port}')
                for key, value in headers.items():
                    conn.putheader(key, value)
                if data is not None:
                    conn.putheader('Content-Length', str(len(data)))
                conn.endheaders(data)
                reply = conn.getresponse()
                payload = reply.read()
                return reply.status, {k.lower(): v for k, v in reply.getheaders()}, payload
            except ConnectionError:
                # A server that refuses a large body may answer and close before reading it; Windows then resets the
                # connection under the sender. The request is repeated (these checks do not depend on being the first).
                if attempt == 2:
                    raise
            finally:
                conn.close()

    def get_json(self, path, status=200):
        code, headers, payload = self.call('GET', path)
        self.assertEqual(code, status, payload[:200])
        self.assertEqual(headers['content-type'], JSON_TYPE)
        return json.loads(payload)

    def assertError(self, reply, status, message=None):
        code, headers, payload = reply
        self.assertEqual(code, status, payload[:200])
        self.assertEqual(headers['content-type'], JSON_TYPE)
        error = json.loads(payload)
        self.assertEqual(list(error), ['error'])
        if message is not None:
            self.assertEqual(error['error'], message)

    # ---- every response -------------------------------------------------------------------------------------

    def test_security_headers_on_every_kind_of_response(self):
        for path in ('/', '/api/summary', '/api/jobs/' + UNKNOWN_ID, '/api/nothing', EVIDENCE + '/page/1', '/favicon.ico'):
            with self.subTest(path=path):
                _, headers, _ = self.call('GET', path)
                self.assertEqual(headers['x-content-type-options'], 'nosniff')
                csp = headers['content-security-policy']
                self.assertTrue(csp.startswith("default-src 'self'; script-src 'self'"))
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertIn('cache-control', headers)
        _, headers, _ = self.call('POST', '/api/nothing', {})
        self.assertEqual(headers['x-content-type-options'], 'nosniff')

    def test_static_files(self):
        for path, name in STATIC.items():
            with self.subTest(path=path):
                code, headers, payload = self.call('GET', path)
                self.assertEqual(code, 200)
                self.assertEqual(headers['content-type'], TYPES[Path(name).suffix])
                self.assertEqual(headers['cache-control'], 'no-store')
                self.assertEqual(payload, (WEB / name).read_bytes())
        for path in ('/index.html', '/../../.env', '/rag_service/web/app.js', '/api/summary/'):
            with self.subTest(path=path):
                self.assertError(self.call('GET', path), 404, MISSING)

    def test_favicon_is_empty_and_cacheable(self):
        code, headers, payload = self.call('GET', '/favicon.ico')
        self.assertEqual((code, payload, headers['cache-control']), (204, b'', 'private, max-age=86400'))

    # ---- reads ----------------------------------------------------------------------------------------------

    def test_json_reads(self):
        self.assertEqual(self.get_json('/api/summary')['answer_model'], 'fixture')
        self.assertEqual(self.get_json('/api/inventory')['ingredients'][0]['name'], 'ustekinumab')
        self.assertEqual(self.get_json('/api/jobs')['total'], 1)
        self.assertEqual(self.get_json(f'/api/jobs/{JOB_ID}')['id'], JOB_ID)
        self.assertEqual(self.get_json(f'/api/jobs/{JOB_ID}/review')['job_id'], JOB_ID)
        self.assertEqual(self.get_json('/api/projects')['projects'], [])
        self.assertTrue(self.get_json('/api/ingestion')['database_ready'])
        for _, headers, _ in [self.call('GET', '/api/summary')]:
            self.assertEqual(headers['cache-control'], 'no-store')

    def test_job_list_parameters(self):
        self.assertEqual(self.get_json('/api/jobs?limit=5&offset=1&q=fix'), {'jobs': [], 'total': 1, 'limit': 5, 'offset': 1, 'busy': False})
        self.assertEqual(len(self.get_json('/api/jobs?q=FIXTURE')['jobs']), 1)
        self.assertEqual(self.get_json('/api/jobs?q=nothing-like-this')['jobs'], [])
        self.assertError(self.call('GET', '/api/jobs?limit=0'), 400, '목록 범위를 확인하세요.')
        self.assertError(self.call('GET', '/api/jobs?limit=51'), 400, '목록 범위를 확인하세요.')
        self.assertError(self.call('GET', '/api/jobs?offset=-1'), 400, '목록 범위를 확인하세요.')
        self.assertEqual(self.call('GET', '/api/jobs?limit=abc')[0], 400)

    def test_read_errors(self):
        for path, status, message in [
                ('/api/jobs/' + UNKNOWN_ID, 404, UNKNOWN_JOB), ('/api/jobs/zzz', 404, UNKNOWN_JOB),
                (f'/api/jobs/{UNKNOWN_ID}/review', 404, UNKNOWN_JOB),
                ('/api/conversations/' + 'c' * 32, 404, UNKNOWN_JOB), ('/api/conversations/bad', 400, 'Invalid conversation ID'),
                ('/api/projects/' + 'd' * 32, 404, UNKNOWN_JOB), ('/api/projects/bad', 404, MISSING),
                ('/api/ingestion/nothing', 404, MISSING), ('/api/nothing', 404, MISSING),
                (EVIDENCE + '?page=x', 400, '페이지 범위를 확인하세요.'),
                (f'/api/jobs/{JOB_ID}/evidence/9/E1', 404, UNKNOWN_JOB), (f'/api/jobs/{JOB_ID}/evidence/0/E9', 404, UNKNOWN_JOB),
                (f'/api/jobs/{UNKNOWN_ID}/evidence/0/E1', 404, UNKNOWN_JOB)]:
            with self.subTest(path=path):
                self.assertError(self.call('GET', path), status, message)

    def test_evidence(self):
        detail = self.get_json(EVIDENCE)
        self.assertEqual((detail['eid'], detail['product_id']), ('E1', 'SB17'))
        self.assertEqual(self.get_json(EVIDENCE + '?page=1')['eid'], 'E1')
        code, headers, payload = self.call('GET', EVIDENCE + '/page/1')
        self.assertEqual((code, headers['content-type'], headers['cache-control']), (200, 'image/png', 'private, max-age=3600'))
        self.assertTrue(payload.startswith(b'\x89PNG'))
        code, headers, payload = self.call('GET', EVIDENCE + '/document.pdf')
        self.assertEqual((code, headers['content-type'], headers['cache-control']), (200, 'application/pdf', 'private, max-age=3600'))
        self.assertTrue(payload.startswith(b'%PDF'))
        self.assertError(self.call('GET', EVIDENCE + '/crop'), 404, UNKNOWN_JOB)  # the fixture's claim has no stored region
        # A page number of more than five digits does not match the evidence route and is read as a job id.
        self.assertError(self.call('GET', EVIDENCE + '/page/99999999'), 404, UNKNOWN_JOB)
        self.assertError(self.call('GET', EVIDENCE + '/page/9'), 404, UNKNOWN_JOB)

    # ---- same origin ----------------------------------------------------------------------------------------

    def test_only_the_local_origin_is_served(self):
        local = f'http://127.0.0.1:{self.port}'
        for method, path, headers, host, status in [
                ('GET', '/api/summary', {'Origin': 'https://example.org'}, None, 403), ('GET', '/api/summary', {}, 'example.org', 403),
                ('GET', '/api/summary', {'Origin': 'null'}, None, 403), ('GET', '/', {'Origin': 'https://example.org'}, None, 403),
                ('POST', '/api/nothing', {'Origin': 'https://example.org'}, None, 403),
                ('POST', '/api/projects', {'Origin': 'https://example.org'}, None, 403),
                ('GET', '/api/summary', {'Origin': local}, None, 200),
                ('GET', '/api/summary', {'Origin': f'http://localhost:{self.port}'}, f'localhost:{self.port}', 200),
                ('GET', '/api/summary', {}, f'localhost:{self.port}', 200)]:
            with self.subTest(method=method, path=path, origin=headers.get('Origin'), host=host):
                reply = self.call(method, path, {} if method == 'POST' else None, headers, host)
                if status == 403:
                    self.assertError(reply, 403, 'Local origin required')
                else:
                    self.assertEqual(reply[0], status)

    # ---- writes ---------------------------------------------------------------------------------------------

    def test_post_checks_run_in_order(self):
        self.assertError(self.call('POST', '/api/nothing', {}), 404, MISSING)
        self.assertError(self.call('POST', '/api/nothing', headers={'Content-Type': 'text/plain'}, raw=b'x'), 404, MISSING)
        self.assertError(self.call('POST', '/api/projects', headers={'Content-Type': 'text/plain'}, raw=b'{}'), 415, 'JSON required')
        self.assertError(self.call('POST', '/api/projects', {}, headers={'Content-Type': 'application/json; charset=utf-8'}), 400, '등록된 성분을 선택하세요.')
        self.assertError(self.call('POST', '/api/projects', raw=b''), 400, '요청 크기를 확인하세요.')
        self.assertEqual(self.call('POST', '/api/projects', raw=b'{not json')[0], 400)

    def test_body_size_limits(self):
        body = lambda n: b'{"a":"' + b'x' * (n - 8) + b'"}'
        for path, limit in [('/api/projects', 65536), (f'/api/jobs/{JOB_ID}/review', 65536), ('/api/questions', 16384),
                            ('/api/ingestion/sessions', 16384), (f'/api/projects/{UNKNOWN_ID}/documents', 524288)]:
            with self.subTest(path=path):
                self.assertError(self.call('POST', path, raw=body(limit + 1)), 400, '요청 크기를 확인하세요.')
        # At the limit the request is read; the route then rejects the content, not the size.
        reply = self.call('POST', '/api/projects', raw=body(65536))
        self.assertEqual(reply[0], 400)
        self.assertNotEqual(json.loads(reply[2])['error'], '요청 크기를 확인하세요.')

    def test_post_route_errors(self):
        self.assertError(self.call('POST', '/api/ingestion/nothing', {}), 404, UNKNOWN_JOB)  # an unknown ingestion path is a KeyError
        self.assertError(self.call('POST', f'/api/jobs/{JOB_ID}/review', {}), 400, 'Invalid review request')
        self.assertError(self.call('POST', f'/api/jobs/{UNKNOWN_ID}/review', {}), 404, UNKNOWN_JOB)
        self.assertError(self.call('POST', '/api/ingestion/sessions', {'x': 1}), 400, '새 세션 요청을 확인하세요.')
        self.assertError(self.call('POST', '/api/projects', {'name': 'x', 'ingredient': 'unknown'}), 400, '등록된 성분을 선택하세요.')

    def test_project_round_trip(self):
        _, headers, payload = self.call('POST', '/api/projects', {'name': '계약 시험', 'ingredient': 'ustekinumab'})
        project = json.loads(payload)
        self.assertEqual(headers['content-type'], JSON_TYPE)
        self.assertEqual(self.get_json('/api/projects/' + project['id'])['name'], '계약 시험')

    def test_workspace_internal_documents_board_and_draft(self):  # after test_json_reads, which expects no projects
        _, _, payload = self.call('POST', '/api/projects', {'name': '내부 자료 ' + self.kind, 'ingredient': 'ustekinumab'})
        project_id = json.loads(payload)['id']
        code, _, payload = self.call('POST', f'/api/projects/{project_id}/documents', BUNDLE)
        self.assertEqual(code, 200, payload[:300])
        self.assertEqual(json.loads(payload)['document_count'], 20)
        self.assertNotIn('documents', self.get_json(f'/api/projects/{project_id}'))
        self.assertEqual(len(self.get_json(f'/api/projects/{project_id}/documents')['documents']), 20)
        board = self.get_json(f'/api/projects/{project_id}/workboard/2026-03-27')
        self.assertEqual(board['hidden_after_count'], 10)
        self.assertIn('W-IV-RERUN', {s['scope_id'] for s in board['states']})
        self.assertError(self.call('GET', f'/api/projects/{project_id}/workboard/2026-02-30'), 400, '날짜는 YYYY-MM-DD로 입력하세요.')
        self.assertError(self.call('POST', f'/api/projects/{project_id}/work-drafts', {'task': 'x', 'question': 'q', 'as_of': '2026-03-06'}), 400,
                         '업무를 결과 검토·협력사 미팅 준비·기관 질의 대응 준비 중에서 고르세요.')
        code, _, payload = self.call('POST', f'/api/projects/{project_id}/work-drafts',
                                     {'task': 'result_review', 'question': '결과를 검토해 주세요.', 'as_of': '2026-03-06'})
        self.assertEqual(code, 200, payload[:300])
        draft_id = json.loads(payload)['id']
        for _ in range(200):
            record = self.get_json(f'/api/projects/{project_id}/work-drafts/{draft_id}')
            if record['state'] != 'running':
                break
            time.sleep(.02)
        self.assertEqual(record['state'], 'completed')
        self.assertEqual(self.get_json(f'/api/projects/{project_id}/work-drafts')['drafts'][0]['id'], draft_id)
        self.assertError(self.call('GET', f'/api/projects/{UNKNOWN_ID}/work-drafts/{draft_id}'), 404, UNKNOWN_JOB)

    def test_methods_the_service_does_not_define_are_not_served(self):
        for method in ('PUT', 'DELETE', 'PATCH'):
            with self.subTest(method=method):
                self.assertNotIn(self.call(method, '/api/summary', {} if method != 'DELETE' else None)[0], range(200, 300))


class StdlibContract(Contract, unittest.TestCase):
    kind = 'stdlib'


@unittest.skipUnless(fastapi_available(), 'FastAPI app (rag_service/app.py) is not available')
class FastApiContract(Contract, unittest.TestCase):
    kind = 'fastapi'

    def test_api_reference(self):
        spec = self.get_json('/openapi.json')
        self.assertEqual(spec['openapi'][:2], '3.')
        for path, method in [('/api/summary', 'get'), ('/api/questions', 'post'), (f'/api/jobs/{{job_id}}/evidence/{{result_index}}/{{evidence_id}}', 'get'),
                             ('/api/ingestion/sessions/{session_id}/delete', 'post')]:
            self.assertIn(method, spec['paths'][path])
        self.assertNotIn('/app.js', spec['paths'])  # screen files are not part of the API
        for name in ('/api-docs', '/api-docs.js', '/api-docs.css'):
            code, headers, payload = self.call('GET', name)
            self.assertEqual((code, headers['cache-control']), (200, 'no-store'))
            self.assertIn('x-content-type-options', headers)
            self.assertGreater(len(payload), 100)
        self.assertNotIn(b'http', self.call('GET', '/api-docs')[2].replace(b'<!doctype html>', b''))  # nothing is loaded from elsewhere
        self.assertError(self.call('GET', '/openapi.json', headers={'Origin': 'https://example.org'}), 403, 'Local origin required')


if __name__ == '__main__':
    unittest.main()
