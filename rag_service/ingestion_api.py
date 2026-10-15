"""Local operating window for the existing ingestion agent; no shell endpoint."""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
from uuid import UUID

from rag_service.reviews import RevisionConflict


def sid(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(UUID(value))
    except (ValueError, AttributeError):
        raise ValueError('적재 세션·요청 ID를 확인하세요.') from None


def serial(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def public(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=serial))


class IngestionAPI:
    def __init__(self, connector=None, agent_factory=None):
        from storage.postgres import connect
        self.connector = connector or connect
        self.agent_factory = agent_factory

    @contextmanager
    def connection(self):
        with self.connector(connect_timeout=5) as conn:
            yield conn

    def overview(self, summary):
        with self.connection() as conn:
            sessions = conn.execute("""SELECT session_id::text, created_at, updated_at, state->>'stage', state->'target', state->>'request_id'
                FROM ingest.agent_sessions ORDER BY updated_at DESC LIMIT 40""").fetchall()
            requests = conn.execute("""SELECT request_id::text, created_at, kind, status, payload->'products'
                FROM ingest.requests ORDER BY created_at DESC LIMIT 40""").fetchall()
            releases = conn.execute("""SELECT release_id, n_documents, n_chunks, loaded_at
                FROM rag.releases ORDER BY loaded_at DESC""").fetchall()
        return public({'sessions': [dict(zip(('id', 'created_at', 'updated_at', 'stage', 'target', 'request_id'), r)) for r in sessions],
            'requests': [dict(zip(('id', 'created_at', 'kind', 'status', 'products'), r)) for r in requests],
            'releases': [dict(zip(('id', 'documents', 'chunks', 'loaded_at'), r)) for r in releases],
            'service': {k: summary.get(k) for k in ('pipeline_version', 'run_name', 'product_index_chunks')},
            'capabilities': {'new_product': True, 'refresh': False, 'new_guideline': False, 'worker_cancel': False,
                             'automatic_search_publish': False}, 'database_ready': True})

    def session(self, session_id):
        with self.connection() as conn:
            return self._session(conn, sid(session_id))

    @staticmethod
    def _session(conn, session_id):
        from ingestion.agent import plan_digest
        row = conn.execute('SELECT state, created_at, updated_at FROM ingest.agent_sessions WHERE session_id = %s', (session_id,)).fetchone()
        if row is None:
            raise KeyError(session_id)
        turns = conn.execute('SELECT turn, at, user_text, reply FROM ingest.agent_turns WHERE session_id = %s ORDER BY turn', (session_id,)).fetchall()
        state, request = row[0], None
        if state.get('request_id'):
            req = conn.execute('SELECT status FROM ingest.requests WHERE request_id = %s', (state['request_id'],)).fetchone()
            if req:
                reports = conn.execute("""SELECT DISTINCT ON (payload->>'product_id') payload->>'product_id', payload->>'report_sha256'
                    FROM ingest.request_events WHERE request_id = %s AND event = 'staged'
                    ORDER BY payload->>'product_id', event_id DESC""", (state['request_id'],)).fetchall()
                request = {'id': state['request_id'], 'status': req[0], 'reports': dict(reports)}
        return public({'id': session_id, 'state': row[0], 'created_at': row[1], 'updated_at': row[2],
            'request': request, 'plan_sha256': plan_digest(state['plan']) if state.get('stage') == 'planned' and state.get('plan') else None,
            'turns': [dict(zip(('turn', 'at', 'user_text', 'reply'), t)) for t in turns]})

    def create(self):
        from ingestion.agent import Agent
        with self.connection() as conn:
            agent = (self.agent_factory or Agent)(conn)
            return self._session(conn, agent.session_id)

    def turn(self, session_id, payload, approval=False, control=False):
        from ingestion.agent import Agent
        session_id = sid(session_id)
        if not isinstance(payload, dict):
            raise ValueError('적재 요청을 확인하세요.')
        if control:
            action = payload.get('action')
            if action == 'select':
                valid = set(payload) == {'action', 'candidate_key'} and isinstance(payload['candidate_key'], str) and 1 <= len(payload['candidate_key']) <= 150
            elif action == 'start':
                valid = set(payload) == {'action', 'plan_sha256'} and isinstance(payload['plan_sha256'], str) and bool(re.fullmatch(r'[0-9a-f]{64}', payload['plan_sha256']))
            else:
                valid = False
            if not valid:
                raise ValueError('적재 버튼 요청을 확인하세요.')
        elif approval:
            if set(payload) != {'product_id', 'report_sha256'} or not isinstance(payload['product_id'], str) or not re.fullmatch(r'[A-Z0-9]{1,40}', payload['product_id']):
                raise ValueError('승인할 제품과 확인표를 확인하세요.')
            message = '/approve ' + payload['product_id']
        else:
            if set(payload) != {'text'} or not isinstance(payload['text'], str) or not 1 <= len(payload['text'].strip()) <= 2000:
                raise ValueError('요청을 2,000자 이내로 입력하세요.')
            message = payload['text'].strip()
            if message.startswith('/') and message not in ('/status', '/help', '/reset'):
                raise ValueError('지원하지 않는 명령입니다. 자료 반영은 확인표의 승인 버튼을 사용하세요.')
        with self.connection() as conn:
            # Across both local servers as well as simultaneous browser requests.
            if not conn.execute('SELECT pg_try_advisory_lock(20261015, 2)').fetchone()[0]:
                raise RevisionConflict('다른 적재 요청을 처리 중입니다. 완료 후 다시 시도하세요.')
            try:
                self._session(conn, session_id)  # Validate before Agent (its CLI raises SystemExit).
                agent = (self.agent_factory or Agent)(conn, session_id)
                if control:
                    if payload['action'] == 'start' and payload['plan_sha256'] != self._session(conn, session_id)['plan_sha256']:
                        raise RevisionConflict('이미 시작했거나 계획이 바뀌었습니다. 최신 계획을 확인하세요.')
                    agent.control(payload)
                elif approval:
                    shown = agent.state.get('shown_reports', {}).get(payload['product_id'])
                    if not shown or shown != payload['report_sha256']:
                        raise RevisionConflict('먼저 최신 확인표를 열고 승인하세요.')
                    agent.handle(message)
                else:
                    agent.handle(message)
                return self._session(conn, session_id)
            finally:
                conn.execute('SELECT pg_advisory_unlock(20261015, 2)')

    def request(self, request_id):
        request_id = sid(request_id)
        with self.connection() as conn:
            row = conn.execute('SELECT created_at, kind, status, payload FROM ingest.requests WHERE request_id = %s', (request_id,)).fetchone()
            if row is None:
                raise KeyError(request_id)
            events = conn.execute('SELECT event_id, at, event, payload FROM ingest.request_events WHERE request_id = %s ORDER BY event_id', (request_id,)).fetchall()
        return public({'id': request_id, 'created_at': row[0], 'kind': row[1], 'status': row[2], 'payload': row[3],
            'events': [dict(zip(('id', 'at', 'event', 'payload'), e)) for e in events],
            'search_ready': None, 'search_note': '자료 등록 완료와 전처리·임베딩·검색 반영은 별도입니다.'})

    def logs(self, request_id):
        from ingestion.worker import RUNS
        folder = RUNS / sid(request_id)
        self.request(request_id)
        logs = []
        if folder.is_dir():
            for path in sorted(folder.glob('*.log'))[:10]:
                # Fixed request directory, no client paths. Tail only, no full-file reads.
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 16000))
                    content = stream.read(16000).decode('utf-8', errors='replace')
                logs.append({'name': path.name, 'text': content})
        return {'logs': logs, 'note': '하위 작업 로그는 작업 종료 후 기록될 수 있습니다.'}


SESSION = re.compile(r'/api/ingestion/sessions/([0-9a-f-]{36})(?:/(turns|approve|actions))?')
REQUEST = re.compile(r'/api/ingestion/requests/([0-9a-f-]{36})(?:/(logs))?')


def get(jobs, path):
    if path == '/api/ingestion':
        return jobs.ingestion.overview(jobs.pipeline.summary())
    match = SESSION.fullmatch(path)
    if match and match[2] is None:
        return jobs.ingestion.session(match[1])
    match = REQUEST.fullmatch(path)
    if match:
        return jobs.ingestion.logs(match[1]) if match[2] else jobs.ingestion.request(match[1])
    return None


def post(jobs, path, payload):
    if path == '/api/ingestion/sessions':
        if payload != {}:
            raise ValueError('새 세션 요청을 확인하세요.')
        return jobs.ingestion.create()
    match = SESSION.fullmatch(path)
    if match and match[2] in ('turns', 'approve', 'actions'):
        return jobs.ingestion.turn(match[1], payload, approval=match[2] == 'approve', control=match[2] == 'actions')
    raise KeyError(path)
