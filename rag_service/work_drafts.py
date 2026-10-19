"""Background runs of work drafts: one model call each, one at a time, kept as JSON records.

The record holds what the model was given (selected units and work states), its answer, and the audit, so a draft can be
reviewed later without re-reading the project. A draft is never fed back as evidence for another draft.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

from rag_service import work_assist

ID = re.compile(r'[0-9a-f]{32}')


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkDraftService:
    def __init__(self, store, directory, model='gpt-5.6-luna', chat_factory=None, context_tokens=32768):
        self.store = store
        self.directory = Path(directory).resolve() if directory is not None else None
        self.model = model
        self.chat_factory = chat_factory
        self.context_tokens = context_tokens
        self.lock = Lock()
        self.busy = False
        self.records = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='work-draft')

    def ready(self):
        if self.directory is None:
            raise ValueError('업무 초안 저장 경로가 설정되지 않았습니다.')
        self.directory.mkdir(parents=True, exist_ok=True)

    def client(self, folder):
        if self.chat_factory:
            return self.chat_factory(folder)
        from rag.openai_chat import EvaluationAPIChat
        client = EvaluationAPIChat(folder, max_new_calls=1, model=self.model)
        # Project reading budget (prompt + output), as for the RA pipeline; one call, no retry.
        client.config = SimpleNamespace(model=self.model, num_ctx=self.context_tokens, num_predict=3000)
        return client

    def persist(self, record):
        path = self.directory / (record['id'] + '.json')
        temp = path.with_suffix('.' + uuid4().hex + '.tmp')
        try:
            with temp.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def submit(self, project_id, payload):
        self.ready()
        if not isinstance(payload, dict) or set(payload) - {'task', 'question', 'as_of', 'audience'} or not {'task', 'question', 'as_of'} <= set(payload):
            raise ValueError('업무·질문·기준일을 보내세요.')
        project = self.store.get(project_id)
        prepared = work_assist.prepare(project, payload['task'], payload['question'], payload['as_of'], payload.get('audience', 'internal'))
        prompt = work_assist.messages(prepared)
        aliases = {k: v.get('aliases', []) for k, v in (project.get('work_scopes') or {}).items()}
        with self.lock:
            if self.busy:
                raise BlockingIOError('다른 업무 초안을 작성 중입니다. 완료 후 다시 시도하세요.')
            self.busy = True
        record = {'id': uuid4().hex, 'kind': 'internal_work_draft', 'state': 'running', 'created_at': now(),
                  'project_id': project['id'], 'project_name': project['name'], 'model': self.model,
                  **{k: prepared[k] for k in ('task', 'question', 'as_of', 'audience', 'project_revision', 'eligible_unit_ids',
                                              'selected', 'workflow_states', 'retrieval', 'hidden_after_count')},
                  'task_label': work_assist.TASKS[prepared['task']]['label'],
                  'status_summary': work_assist.status_summary(prepared['workflow_states'])}
        try:
            self.persist(record)
            with self.lock:
                self.records[record['id']] = record
            self.executor.submit(self.run, record['id'], prepared, prompt, aliases)
        except Exception:
            with self.lock:
                self.busy = False
            raise
        return self.summary(record)

    def run(self, draft_id, prepared, prompt, aliases):
        with self.lock:
            record = deepcopy(self.records[draft_id])
        client = None
        try:
            client = self.client(self.directory / (draft_id + '_api'))
            if hasattr(client, 'case'):
                client.case = 'internal_work_draft'
            ids = [u['unit_id'] for u in prepared['selected']]
            response = client(prompt, work_assist.schema(ids))
            draft = work_assist.normalize(json.loads(response['text']), ids)
            record.update(state='completed', draft=draft, audit=work_assist.audit(draft, prepared, aliases),
                          usage={'input_tokens': response.get('prompt_tokens', 0), 'output_tokens': response.get('completion_tokens', 0)},
                          model_reported=response.get('model_reported'), completed_at=now())
        except Exception as exc:  # noqa: BLE001 - the record must end in a terminal state
            record.update(state='failed', error=f'{type(exc).__name__}: {exc}', completed_at=now())
        finally:
            if client is not None and hasattr(client, 'close'):
                client.close()
            try:
                self.persist(record)
            finally:
                with self.lock:
                    self.records[draft_id] = record
                    self.busy = False

    def load(self, draft_id):
        if not isinstance(draft_id, str) or not ID.fullmatch(draft_id):
            raise KeyError(draft_id)
        with self.lock:
            if draft_id in self.records:
                return deepcopy(self.records[draft_id])
        self.ready()
        path = self.directory / (draft_id + '.json')
        if not path.is_file():
            raise KeyError(draft_id)
        return json.loads(path.read_text(encoding='utf-8'))

    def get(self, project_id, draft_id):
        record = self.load(draft_id)
        if record.get('project_id') != project_id:
            raise KeyError(draft_id)
        return record

    @staticmethod
    def summary(record):
        keys = ('id', 'state', 'created_at', 'completed_at', 'project_id', 'task', 'task_label', 'question', 'as_of', 'audience', 'error')
        result = {k: record.get(k) for k in keys}
        result['review_status'] = (record.get('audit') or {}).get('review_status')
        result['warning_count'] = len((record.get('audit') or {}).get('warnings', []))
        return result

    def list(self, project_id):
        self.ready()
        rows = {}
        for path in self.directory.glob('*.json'):
            if not ID.fullmatch(path.stem):
                continue
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if record.get('project_id') == project_id:
                rows[record['id']] = record
        with self.lock:
            rows.update({k: deepcopy(v) for k, v in self.records.items() if v['project_id'] == project_id})
        return sorted((self.summary(r) for r in rows.values()), key=lambda r: r['created_at'], reverse=True)

    def close(self):
        self.executor.shutdown(wait=True)
