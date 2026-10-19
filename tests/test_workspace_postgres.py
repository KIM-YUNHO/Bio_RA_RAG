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

    def test_item_annotations_and_history_persist_across_connections(self):
        store = PostgresWorkspaceStore(self.connector)
        project = store.create({'name': 'Memo unit persistence', 'ingredient': 'ustekinumab'})
        saved = store.add(project['id'], {'request_id': uuid4().hex, 'body': 'Hypothetical result',
            'stage_ids': ['cmc'], 'products': [], 'tags': [],
            'annotations': [{'key': 'manual', 'type': 'interpretation', 'modality': 'predicted', 'subject': 'FcRn'}]})
        memo = saved['memos'][0]
        changes = {k: memo[k] for k in ('body', 'stage_ids', 'products', 'tags', 'archived')}
        store.revise(project['id'], memo['id'], {**changes, 'expected_revision': 1,
            'annotations': [{'key': 'manual', 'note': 'Check raw data first'}]})
        restored = PostgresWorkspaceStore(self.connector).get(project['id'])['memos'][0]
        self.assertEqual(restored['items'][0]['modality'], 'predicted')
        self.assertEqual(restored['items'][0]['run_validity'], 'not_disclosed')
        self.assertEqual(restored['items'][0]['note'], 'Check raw data first')
        self.assertEqual(restored['history'][0]['items'], memo['items'])

    def test_document_item_keeps_speaker_and_events_after_reopen_and_body_edit(self):
        from eval.build_pre_disclosure_v3 import fixture
        store = PostgresWorkspaceStore(self.connector)
        project = store.create({'name': 'Imported source context', 'ingredient': 'ustekinumab'})
        unit = next(u for u in fixture()['units'] if u['id'] == 'U12')
        saved = store.add(project['id'], {'request_id': uuid4().hex, 'body': unit['text'],
            'stage_ids': ['cmc'], 'products': [], 'tags': []}, item_context=unit['context'])
        memo = saved['memos'][0]
        changes = {k: memo[k] for k in ('body', 'stage_ids', 'products', 'tags', 'archived')}
        store.revise(project['id'], memo['id'], {**changes, 'expected_revision': 1, 'body': 'Separate reviewer working note'})
        restored = PostgresWorkspaceStore(self.connector).get(project['id'])['memos'][0]
        self.assertEqual(restored['items'][0]['context'], unit['context'])
        self.assertEqual(restored['items'][0]['text'], unit['text'])
        self.assertEqual(restored['items'][0]['statement_role'], 'analyst_opinion')
        self.assertEqual(restored['body'], 'Separate reviewer working note')

    def test_internal_documents_persist_and_reimport_is_a_no_op(self):
        from test_internal_work import BUNDLE
        from rag_service import internal_documents
        store = PostgresWorkspaceStore(self.connector)
        project = store.create({'name': 'Internal document persistence', 'ingredient': 'ustekinumab'})
        self.assertEqual(len(store.register_documents(project['id'], BUNDLE)['added']), 20)
        reopened = PostgresWorkspaceStore(self.connector)
        self.assertEqual(reopened.register_documents(project['id'], BUNDLE)['added'], [])
        restored = reopened.get(project['id'])
        board = {s['scope_id']: s for s in internal_documents.board(restored, '2026-04-21')}
        self.assertEqual(board['W-IV-CPB']['axes']['schedule']['date'], '2026-05-08')
        self.assertEqual(restored['revision'], project['revision'] + 1)

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
        with patch('ingestion.agent.spawn') as spawn, patch('ingestion.worker.check_here', return_value='f'*64) as check:
            promoted = service.turn(session['id'], approval, approval=True)
            self.assertEqual(promoted['request']['status'], 'approved')
            check.assert_called_once_with('SB17', request_id)
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

    def test_approval_requires_the_check_table_from_this_folders_staging(self):
        from ingestion.worker import event, set_status
        service = self.control_service()
        session = self.seed_planned(service)
        with patch('ingestion.agent.spawn'):
            request_id = service.turn(session['id'], {'action': 'start', 'plan_sha256': session['plan_sha256']}, control=True)['request']['id']
        with self.connector() as conn:
            event(conn, request_id, 'staged', {'product_id': 'SB17', 'report_sha256': 'f'*64, 'report': 'Fixture check table',
                                               'stage_root': r'C:\other_project'})
            set_status(conn, request_id, 'awaiting_approval')
        service.turn(session['id'], {'text': '/status'})
        approval = {'product_id': 'SB17', 'report_sha256': 'f'*64}
        # Staged by another project folder that shares the database, then a staging copy that changed after review.
        for here, expected in ((None, r'스테이징한 폴더: C:\other_project'), ('e'*64, '확인표와 달라')):
            with self.subTest(here=here), patch('ingestion.agent.spawn') as spawn, patch('ingestion.worker.check_here', return_value=here):
                result = service.turn(session['id'], approval, approval=True)
                self.assertIn(expected, result['turns'][-1]['reply'])
                self.assertEqual(result['request']['status'], 'awaiting_approval')
                spawn.assert_not_called()
        with self.connector() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM ingest.approvals WHERE request_id = %s', (request_id,)).fetchone()[0], 0)

    def test_worker_start_failure_does_not_leave_a_queued_job(self):
        service = self.control_service()
        session = self.seed_planned(service)
        with patch('ingestion.agent.spawn', side_effect=OSError('fixture launch failure')):
            result = service.turn(session['id'], {'action': 'start', 'plan_sha256': session['plan_sha256']}, control=True)
        self.assertEqual(result['request']['status'], 'failed')
        self.assertEqual(result['state']['stage'], 'planned')
        self.assertIn('시작하지 못했습니다', result['turns'][-1]['reply'])
        self.assertEqual(service.request(result['request']['id'])['events'][-1]['payload']['step'], 'worker_start')

    def test_session_deletion_hides_history_without_erasing_audit_records(self):
        from ingestion.agent import Agent
        service = self.control_service()
        session = service.create()
        reply = service.turn(session['id'], {'text': '/status'})
        self.assertTrue(reply['can_delete'])
        service.delete(session['id'])
        self.assertEqual(service.delete(session['id']), {'id': session['id'], 'deleted': True})
        self.assertNotIn(session['id'], [s['id'] for s in service.overview({})['sessions']])
        with self.assertRaises(KeyError):
            service.session(session['id'])
        with self.assertRaises(KeyError):
            service.turn(session['id'], {'text': '/status'})
        with self.connector() as conn:
            self.assertIsNotNone(conn.execute('SELECT deleted_at FROM ingest.agent_sessions WHERE session_id = %s', (session['id'],)).fetchone()[0])
            self.assertEqual(conn.execute('SELECT user_text, reply FROM ingest.agent_turns WHERE session_id = %s', (session['id'],)).fetchall(), [('/status', reply['turns'][0]['reply'])])
            with self.assertRaises(SystemExit):
                Agent(conn, session['id'])

    def test_deletion_blocks_active_requests_and_preserves_completed_request(self):
        from ingestion.worker import set_status
        service = self.control_service()
        session = self.seed_planned(service)
        with patch('ingestion.agent.spawn'):
            started = service.turn(session['id'], {'action': 'start', 'plan_sha256': session['plan_sha256']}, control=True)
        request_id = started['request']['id']
        with self.connector() as conn:
            for status in ('queued', 'staging', 'awaiting_approval', 'approved', 'promoting'):
                with self.subTest(status=status):
                    set_status(conn, request_id, status)
                    self.assertFalse(service.session(session['id'])['can_delete'])
                    listed = next(s for s in service.overview({})['sessions'] if s['id'] == session['id'])
                    self.assertFalse(listed['can_delete'])
                    with self.assertRaises(RevisionConflict):
                        service.delete(session['id'])
                    self.assertIsNone(conn.execute('SELECT deleted_at FROM ingest.agent_sessions WHERE session_id = %s', (session['id'],)).fetchone()[0])
            set_status(conn, request_id, 'done')
        original_request = service.request(request_id)
        self.assertTrue(service.session(session['id'])['can_delete'])
        service.delete(session['id'])
        self.assertEqual(service.request(request_id), original_request)
        self.assertIn(request_id, [r['id'] for r in service.overview({})['requests']])

    def test_session_deletion_http_contract_and_turn_lock(self):
        import json
        import urllib.request
        import urllib.error
        from test_workspace_http import fixture
        service = self.control_service()
        session = service.create()
        with self.connector() as lock_conn:
            lock_conn.execute('SELECT pg_advisory_lock(20261015, 2)')
            with self.assertRaises(RevisionConflict):
                service.delete(session['id'])
            lock_conn.execute('SELECT pg_advisory_unlock(20261015, 2)')
        with fixture(ingestion_service=service) as (server, _, __):
            base = f'http://127.0.0.1:{server.server_port}/api/ingestion/sessions/{session["id"]}'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(suffix='', payload=None, origin=None):
                headers = {'Content-Type': 'application/json'}
                if origin:
                    headers['Origin'] = origin
                req = urllib.request.Request(base+suffix, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
                with opener.open(req) as response:
                    return json.load(response)
            for suffix, payload, origin, status in [('/delete', {}, 'https://example.com', 403),
                    ('/delete', {'request_id': 'forged'}, None, 400), ('/delete', None, None, 404)]:
                with self.subTest(status=status), self.assertRaises(urllib.error.HTTPError) as error:
                    request(suffix, payload, origin)
                self.assertEqual(error.exception.code, status)
            self.assertEqual(request('/delete', {}), {'id': session['id'], 'deleted': True})
            for suffix, payload in [('', None), ('/turns', {'text': '/status'})]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request(suffix, payload)
                self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
