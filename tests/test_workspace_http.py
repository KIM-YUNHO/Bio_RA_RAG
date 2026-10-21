"""Notebook HTTP contract and a no-model fixture for DOM interaction checks."""
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request

import fitz

from rag_service.__main__ import Jobs

sys.path.insert(0, str(Path(__file__).parent))
from http_server import Running  # noqa: E402

JOB_ID = 'a' * 32


class FixtureIngestion:
    def overview(self, summary):
        return {'sessions': [], 'requests': [], 'releases': [], 'database_ready': True, 'service': summary}


@contextmanager
def fixture_jobs(ingestion_service=None):
    """Jobs over a one-document pipeline with one completed job (evidence E1 on a real PDF); no HTTP server."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root/'source.pdf'
        pdf = fitz.open(); pdf.new_page().insert_text((40, 60), 'Verified source sentence.'); pdf.save(path); pdf.close()
        doc = {'key': 'd1', 'document_id': 'doc1', 'version_id': 'v1', 'product_id': 'SB17', 'jurisdiction': 'EU',
               'source': 'EMA', 'title': 'Source', 'pages': 1, 'chunks': 1, 'eligible_chunks': 1, 'eligible_with_nondefault': 1}
        class Pipeline:
            documents = [doc]
            products = ['SB17']
            agencies = ['EU']
            bundle = SimpleNamespace(docs={'d1': {'document_id': 'doc1', 'version_id': 'v1', 'raw_path': str(path)}})
            guidelines = None
            def summary(self):
                return {'chunks': 1, 'eligible_chunks': 1, 'answer_model': 'fixture', 'run_name': 'fixture',
                        'review_url': 'http://127.0.0.1:1', 'pipeline_version': 'fixture', 'product_index_chunks': 1}
        jobs = Jobs(Pipeline(), root/'reports', ingestion_service=ingestion_service or FixtureIngestion())
        evidence = {**doc, 'pdf_page': 1, 'text': 'Verified source sentence.', 'source_refs': [], 'source_spans': [], 'structure': {'kind': 'text'}}
        claim = {'id': 'C1', 'text': '검증한 근거 메모', 'products': ['SB17'], 'task_ids': ['T1'], 'kind': 'source_fact',
                 'sources': [{'evidence': 'E1', 'quote': 'Verified source sentence.', 'source_range': [0, 25], 'unit_id': 'unit1', 'offset_basis': 'evidence_text'}]}
        memo_claim = {'claim_id': 'C1', 'text': claim['text'], 'products': ['SB17'], 'statement_role': 'source_fact',
                      'sources': [{'title': 'Source', 'pdf_page': 1}]}
        report = {'mode': 'ra', 'question': 'fixture question', 'seconds': 0, 'usage': {'input_tokens': 0, 'output_tokens': 0},
                  'understanding': {'products': ['SB17'], 'jurisdictions': ['EU']},
                  'results': [{'status': 'answered', 'answer_ko': '검증한 근거 메모 [E1]', 'claims': [claim], 'evidence': {'E1': evidence}}],
                  'review_memo': {'issues': [{'request': '확인 항목', 'claims': [memo_claim], 'unresolved_requests': [], 'potential_conflicts': []}], 'meeting_questions': [], 'markdown': 'fixture'}}
        job = {'id': JOB_ID, 'state': 'completed', 'question': report['question'], 'created_at': 1, 'report': report}
        jobs.jobs[JOB_ID] = job; jobs.persist(job)
        try:
            yield jobs
        finally:
            jobs.close()


def add_stop_route(app, running):
    """The fixture's POST /__fixture_stop, which the DOM checks use to end the server."""
    from fastapi import Response

    def stop():
        running.request_stop()
        return Response(b'{"stopping": true}', headers={'content-type': 'application/json; charset=utf-8'})
    app.add_api_route('/__fixture_stop', stop, methods=['POST'], include_in_schema=False)


@contextmanager
def fixture(ingestion_service=None):
    with fixture_jobs(ingestion_service) as jobs:
        server = Running(jobs, extend=add_stop_route)
        try:
            yield server, jobs, server.thread
        finally:
            server.shutdown()


class NotebookHttpTests(unittest.TestCase):
    def test_create_pin_edit_conflict_and_cross_origin(self):
        with fixture() as (server, jobs, _):
            base = f'http://127.0.0.1:{server.server_port}'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path, body=None, headers=None):
                req = urllib.request.Request(base+path, data=json.dumps(body).encode() if body is not None else None,
                                             headers={'Content-Type': 'application/json', **(headers or {})})
                with opener.open(req) as reply:
                    return json.load(reply)
            project = request('/api/projects', {'name': '자사 개발 파이프라인', 'ingredient': 'ustekinumab'})
            self.assertNotIn('products', project)
            self.assertNotIn('products', request('/api/projects')['projects'][0])
            payload = {'request_id': 'b'*32, 'body': '메모', 'stage_ids': ['phase1'], 'tags': ['CRO A'],
                       'source': {'job_id': JOB_ID, 'result_index': 0, 'kind': 'claim', 'claim_id': 'C1'}}
            route = '/api/projects/'+project['id']
            saved = request(route+'/memos', payload)
            memo = saved['memos'][0]
            self.assertEqual(memo['source']['original_text'], '검증한 근거 메모')
            self.assertEqual(memo['source']['question'], 'fixture question')
            self.assertEqual(memo['products'], ['SB17'])
            self.assertEqual(request(route)['memos'][0]['tags'], ['CRO A'])
            changes = {k: memo[k] for k in ('body', 'stage_ids', 'products', 'tags', 'archived')}
            changes.update(body='수정', expected_revision=1)
            revision = route+'/memos/'+memo['id']+'/revisions'
            request(revision, changes)
            for path, body, headers, status in [(revision, changes, None, 409), ('/api/projects', None, {'Origin': 'https://example.com'}, 403),
                                                ('/api/projects', {'name': 'bad', 'ingredient': 'unknown'}, None, 400),
                                                ('/api/projects', {'name': 'bad', 'ingredient': 'ustekinumab', 'products': ['SB17']}, None, 400),
                                                (route+'/memos', {**payload, 'source': {**payload['source'], 'quote': 'forged'}}, None, 400)]:
                with self.subTest(status=status), self.assertRaises(urllib.error.HTTPError) as error:
                    request(path, body, headers)
                self.assertEqual(error.exception.code, status)


if __name__ == '__main__':
    import sys
    if '--serve' in sys.argv:
        with fixture() as (server, _, thread):
            print(json.dumps({'port': server.server_port}), flush=True)
            thread.join()
    else:
        unittest.main()
