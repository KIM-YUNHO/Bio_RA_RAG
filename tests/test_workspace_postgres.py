"""Opt-in real DB integration in an isolated disposable database, never user tables."""
from functools import partial
import os
from pathlib import Path
import unittest
from uuid import uuid4
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from rag_service.ingestion_api import IngestionAPI
from rag_service.reviews import RevisionConflict
from rag_service.workspaces import PostgresWorkspaceStore
from storage.postgres import connect, migrate, url


@unittest.skipUnless(os.environ.get('RA_TEST_POSTGRES') == '1', 'set RA_TEST_POSTGRES=1 for an isolated PostgreSQL test database')
class PostgresNotebookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dbname = 'ra_notebook_test_' + uuid4().hex
        with connect() as conn:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.dbname)))
        cls.dsn = make_conninfo(url(), dbname=cls.dbname)
        cls.connector = staticmethod(lambda **kw: psycopg.connect(cls.dsn, autocommit=True, **kw))
        with cls.connector() as conn:
            migrate(conn)

    @classmethod
    def tearDownClass(cls):
        if not cls.dbname.startswith('ra_notebook_test_') or len(cls.dbname) != len('ra_notebook_test_') + 32:
            raise RuntimeError('Unsafe test database name')
        with connect() as conn:
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.dbname)))

    def test_projects_tags_revision_and_catalog_reload_independence(self):
        store = PostgresWorkspaceStore(self.connector)
        project = store.create({'name': 'DB 개발 제품', 'ingredient': 'ustekinumab'})
        self.assertNotIn('products', project)
        self.assertNotIn('products', store.list()['projects'][0])
        payload = {'request_id': uuid4().hex, 'body': 'DB에 저장', 'stage_ids': ['cmc'], 'products': [], 'tags': ['CRO A']}
        saved = store.add(project['id'], payload)
        store.write({**saved, 'products': ['SB17']})
        with self.connector() as conn:
            conn.execute('TRUNCATE registry.licences, registry.products, registry.ingredients, corpus.pages, corpus.document_versions, corpus.documents, corpus.studies, corpus.study_results, corpus.guidelines, corpus.downloads, corpus.raw_objects CASCADE')
        reopened = PostgresWorkspaceStore(self.connector)
        self.assertEqual(reopened.get(project['id'])['memos'][0]['tags'], ['CRO A'])
        self.assertNotIn('products', reopened.get(project['id']))
        self.assertEqual(len(reopened.get(project['id'])['stages']), 7)
        self.assertTrue(all('ctd' not in s for s in reopened.get(project['id'])['stages']))
        memo = saved['memos'][0]
        changes = {k: memo[k] for k in ('body', 'stage_ids', 'products', 'tags', 'archived')}
        changes.update(body='수정', expected_revision=1)
        reopened.revise(project['id'], memo['id'], changes)
        with self.assertRaises(RevisionConflict):
            store.revise(project['id'], memo['id'], changes)
        self.assertEqual(store.list()['storage'], 'postgres')

    def test_actual_agent_session_resume_without_model_or_collection(self):
        from ingestion.agent import Agent
        from ingestion.contracts import TurnParse
        parse = lambda text, context: TurnParse(intent='other', mentions=[], jurisdictions=[], selected_keys=[])
        service = IngestionAPI(self.connector, partial(Agent, parse=parse))
        session = service.create()
        reply = service.turn(session['id'], {'text': '/status'})
        self.assertEqual(len(reply['turns']), 1)
        self.assertIn('적재 요청이 없습니다', reply['turns'][0]['reply'])
        self.assertEqual(service.session(session['id'])['turns'], reply['turns'])
        with self.assertRaises(RevisionConflict):
            service.turn(session['id'], {'product_id': 'SB17', 'report_sha256': '0'*64}, approval=True)
        with self.assertRaises(ValueError):
            service.turn(session['id'], {'text': '/approve SB17'})
        self.assertTrue(service.overview({})['database_ready'])

    def control_service(self):
        from ingestion.agent import Agent
        def no_model(*args):
            raise AssertionError('Explicit buttons must not call a language model')
        return IngestionAPI(self.connector, partial(Agent, parse=no_model))

    def seed_planned(self, service):
        from ingestion.contracts import load_targets
        product = next(p for p in load_targets().products if p.product_id == 'SB17').model_dump(mode='json')
        session = service.create()
        state = session['state']
        state.update(stage='planned', plan={'entry': product, 'request': {'kind': 'new_product', 'ingredient': 'ustekinumab',
                     'products': [product], 'jurisdictions': ['EU']}}, user_texts=['test plan'])
        from psycopg.types.json import Jsonb
        with self.connector() as conn:
            conn.execute('UPDATE ingest.agent_sessions SET state = %s WHERE session_id = %s', (Jsonb(state), session['id']))
        return service.session(session['id'])

    def test_controls_start_review_approve_and_poll_without_model(self):
        from ingestion.worker import event, set_status
        service = self.control_service()
        session = self.seed_planned(service)
        start = {'action': 'start', 'plan_sha256': session['plan_sha256']}
        with patch('ingestion.agent.spawn') as spawn:
            result = service.turn(session['id'], start, control=True)
            request_id = result['request']['id']
            self.assertEqual(result['request']['status'], 'queued')
            spawn.assert_called_once_with(['stage', request_id], request_id)
            with self.assertRaises(RevisionConflict):
                service.turn(session['id'], start, control=True)
        with self.connector() as conn:
            event(conn, request_id, 'staged', {'product_id': 'SB17', 'report_sha256': 'f'*64, 'report': 'Fixture check table'})
            set_status(conn, request_id, 'awaiting_approval')
        polled = service.session(session['id'])
        self.assertEqual(polled['request']['status'], 'awaiting_approval')
        self.assertEqual(len(polled['turns']), 1)  # polling never writes a turn
        approval = {'product_id': 'SB17', 'report_sha256': 'f'*64}
        with self.assertRaises(RevisionConflict):
            service.turn(session['id'], approval, approval=True)
        reviewed = service.turn(session['id'], {'text': '/status'})
        self.assertIn('Fixture check table', reviewed['turns'][-1]['reply'])
        self.assertEqual(reviewed['request']['reports']['SB17'], 'f'*64)
        with patch('ingestion.agent.spawn') as spawn:
            promoted = service.turn(session['id'], approval, approval=True)
            self.assertEqual(promoted['request']['status'], 'approved')
            service.turn(session['id'], approval, approval=True)
            self.assertEqual(spawn.call_count, 1)
        with self.connector() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM ingest.approvals WHERE request_id = %s', (request_id,)).fetchone()[0], 1)
            set_status(conn, request_id, 'done')
            self.assertEqual(conn.execute('SELECT count(*) FROM ingest.model_calls WHERE session_id = %s', (session['id'],)).fetchone()[0], 0)
        self.assertEqual(service.session(session['id'])['request']['status'], 'done')

    def test_control_selection_and_http_contract(self):
        from psycopg.types.json import Jsonb
        from test_workspace_http import fixture
        import json
        import urllib.request
        import urllib.error
        service = self.control_service()
        session = service.create()
        candidate = {'key': 'SB17', 'product_id': 'SB17', 'ingredient': 'ustekinumab', 'names': {'EU': 'Pyzchiva'},
                     'level': 'biosimilar_product', 'collected': True, 'searchable': False}
        state = session['state']
        state.update(stage='choosing', candidates=[candidate], pending_intent='coverage', jurisdictions=['EU'])
        with self.connector() as conn:
            conn.execute('UPDATE ingest.agent_sessions SET state = %s WHERE session_id = %s', (Jsonb(state), session['id']))
        with fixture(ingestion_service=service) as (server, _, __):
            route = f'http://127.0.0.1:{server.server_port}/api/ingestion/sessions/{session["id"]}/actions'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(payload):
                request = urllib.request.Request(route, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
                with opener.open(request) as response:
                    return json.load(response)
            result = post({'action': 'select', 'candidate_key': 'SB17'})
            self.assertEqual(result['state']['target']['product_id'], 'SB17')
            self.assertEqual(result['state']['jurisdictions'], ['EU'])
            for payload in ({'action': 'select', 'candidate_key': 'forged'}, {'action': 'shell', 'command': 'anything'}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post(payload)
                self.assertEqual(error.exception.code, 400)

    def test_worker_start_failure_does_not_leave_a_queued_job(self):
        service = self.control_service()
        session = self.seed_planned(service)
        with patch('ingestion.agent.spawn', side_effect=OSError('fixture launch failure')):
            result = service.turn(session['id'], {'action': 'start', 'plan_sha256': session['plan_sha256']}, control=True)
        self.assertEqual(result['request']['status'], 'failed')
        self.assertEqual(result['state']['stage'], 'planned')
        self.assertIn('시작하지 못했습니다', result['turns'][-1]['reply'])
        self.assertEqual(service.request(result['request']['id'])['events'][-1]['payload']['step'], 'worker_start')


if __name__ == '__main__':
    unittest.main()
