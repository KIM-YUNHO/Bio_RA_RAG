"""Development pipeline notebooks: stage-linked memos, tags and durable citations.

Kept outside the replaceable collection catalog. Writes use both a process lock
and an OS file lock, so two local RA servers cannot overwrite one another.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
import time
from uuid import uuid4

from rag_service.reviews import RevisionConflict
from rag_service import memo_items

STAGES = [
    # The notebook follows the project's biosimilar evidence review scope.
    # Stages organize notes, not mandatory trials or automatic search filters.
    {'id': 'bridging', 'label': '개발 전략·가교 검토', 'short': '개발 전략·가교',
     'description': '개발 전략, 기관 협의, 대조약 가교 및 시험 구성·생략 근거 검토'},
    {'id': 'cmc', 'label': '품질·분석적 유사성', 'short': '품질·분석',
     'description': '품질·제조 자료와 분석적 유사성 근거 검토'},
    {'id': 'nonclinical', 'label': '비임상', 'short': '비임상',
     'description': '비임상 비교 자료와 규제기관의 검토 근거'},
    {'id': 'phase1', 'label': '임상 1상 · PK/PD', 'short': '임상 1상',
     'description': 'PK/PD 시험 설계, 대조약, 동등성 및 면역원성 근거 검토'},
    {'id': 'phase2', 'label': '임상 2상 · 설계·생략 검토', 'short': '임상 2상',
     'description': '제품별 임상 2상 해당 여부, 설계 및 생략 근거 검토. 시험 수행을 전제하지 않음'},
    {'id': 'phase3', 'label': '임상 3상 · 비교 임상', 'short': '임상 3상',
     'description': '비교 임상의 유효성·안전성·면역원성과 규제기관 평가 검토'},
    {'id': 'approval', 'label': '허가 심사·적응증 외삽', 'short': '허가·외삽',
     'description': '적응증 외삽, 기관별 심사 쟁점·질의·보완 및 허가 근거 검토'},
]
# Earlier UI versions exposed these additional categories. Keep their notes
# accessible using their original IDs, without adding empty nodes to new boards.
LEGACY_STAGES = [
    {'id': 'clinical_application', 'label': '임상시험 신청·개시 준비', 'short': '임상시험 신청', 'legacy': True},
    {'id': 'postmarketing', 'label': '허가 후 관리', 'short': '허가 후 관리', 'legacy': True},
]
STAGE_IDS = {s['id'] for s in STAGES + LEGACY_STAGES}


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('파이프라인·메모 ID를 확인하세요.')
    return value


def text(value, name, maximum, blank=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{name}은(는) {maximum:,}자 이내로 입력하세요.')
    value = value.strip()
    if not blank and not value:
        raise ValueError(f'{name}을(를) 입력하세요.')
    return value


def values(value, name, maximum=20):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(v, str) for v in value):
        raise ValueError(f'{name} 선택을 확인하세요.')
    return list(dict.fromkeys(value))


def tags(value):
    result, seen = [], set()
    for raw in values(value, '태그', 12):
        tag = text(raw.lstrip('#'), '태그', 60)
        if any(ord(c) < 32 for c in tag):
            raise ValueError('태그에는 줄바꿈이나 제어 문자를 사용할 수 없습니다.')
        if tag.casefold() not in seen:
            result.append(tag)
            seen.add(tag.casefold())
    return result


def atomic(path, value):
    temp = path.with_suffix('.' + uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class WorkspaceStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve() if directory is not None else None
        self.lock = RLock()
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)

    def ready(self):
        if self.directory is None:
            raise ValueError('파이프라인 저장 경로가 설정되지 않았습니다.')

    @contextmanager
    def writing(self):
        self.ready()
        with self.lock, (self.directory / '.write.lock').open('a+b') as stream:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0, 2)
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
            deadline = time.monotonic() + 5
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RevisionConflict('다른 창에서 저장 중입니다. 잠시 후 다시 저장하세요.')
                    time.sleep(.02)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def path(self, project_id):
        self.ready()
        return self.directory / (identity(project_id) + '.json')

    def get(self, project_id):
        path = self.path(project_id)
        if not path.is_file():
            raise KeyError(project_id)
        value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('id') != project_id or value.get('schema_version') != 1 or not isinstance(value.get('memos'), list):
            raise ValueError('파이프라인 기록 형식을 확인하세요. 기존 파일은 보존했습니다.')
        return self.pipeline_record(value)

    @staticmethod
    def pipeline_record(value):
        # Older records linked catalog comparison products to the development
        # product. Ignore that obsolete constraint without touching memo history.
        # Refresh the stage view using stable IDs, without writing on reads.
        used = {stage for memo in value.get('memos', []) for stage in memo['stage_ids']}
        stages = STAGES + [s for s in LEGACY_STAGES if s['id'] in used]
        return {**{k: v for k, v in value.items() if k != 'products'}, 'stages': deepcopy(stages)}

    def list(self):
        self.ready()
        rows = []
        for path in self.directory.glob('*.json'):
            if not re.fullmatch(r'[0-9a-f]{32}', path.stem):
                continue
            project = self.get(path.stem)
            rows.append({k: project[k] for k in ('id', 'name', 'ingredient', 'updated_at')})
            rows[-1]['memo_count'] = sum(not m.get('archived') for m in project['memos'])
        return {'projects': sorted(rows, key=lambda p: p['updated_at'], reverse=True), 'stages': deepcopy(STAGES), 'storage': 'server_files'}

    def create(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'name', 'ingredient'}:
            raise ValueError('파이프라인 이름과 원성분만 입력하세요. 비교 제품은 왼쪽 검색 범위에서 선택합니다.')
        name = text(payload['name'], '파이프라인 이름', 120)
        ingredient = text(payload['ingredient'], '성분', 120)
        with self.writing():
            if any(p['name'].casefold() == name.casefold() for p in self.list()['projects']):
                raise RevisionConflict('같은 이름의 파이프라인이 있습니다. 다른 이름을 입력하세요.')
            stamp = now()
            project = {'schema_version': 1, 'id': uuid4().hex, 'name': name, 'ingredient': ingredient,
                       'stages': deepcopy(STAGES), 'revision': 1,
                       'created_at': stamp, 'updated_at': stamp, 'memos': []}
            self.write(project)
            return project

    def write(self, project):
        atomic(self.path(project['id']), project)

    def delete(self, project_id):
        """Take a pipeline off the list. Its record moves to deleted/ unchanged, so memos stay recoverable."""
        with self.writing():
            path = self.path(project_id)
            if not path.is_file():
                raise KeyError(project_id)
            (self.directory / 'deleted').mkdir(exist_ok=True)
            os.replace(path, self.directory / 'deleted' / path.name)
            return {'id': project_id, 'deleted': True}

    @staticmethod
    def fields(payload):
        body = text(payload.get('body'), '메모', 8000)
        stage_ids = values(payload.get('stage_ids'), '단계', len(STAGE_IDS))
        products = [text(p, '참고 제품', 120) for p in values(payload.get('products', []), '참고 제품')]
        if not stage_ids or not set(stage_ids) <= STAGE_IDS:
            raise ValueError('저장할 단계를 한 개 이상 선택하세요.')
        return {'body': body, 'stage_ids': stage_ids, 'products': products, 'tags': tags(payload.get('tags', []))}

    def add(self, project_id, payload, source=None, *, item_context=None):
        if not isinstance(payload, dict) or set(payload) - {'body', 'stage_ids', 'products', 'tags', 'source', 'request_id', 'annotations'}:
            raise ValueError('메모 저장 요청을 확인하세요.')
        request_id = identity(payload.get('request_id'))
        key = ('rag:' + source['key']) if source else ('manual:' + request_id)
        with self.writing():
            project = self.get(project_id)
            fields = self.fields(payload)
            # Comparison products describe the saved evidence, never the
            # company's development pipeline. Resolve them on the server.
            reference_products = list(dict.fromkeys(e['product_id'] for e in source['evidence'] if e.get('product_id'))) if source else []
            if fields['products'] and fields['products'] != reference_products:
                raise ValueError('참고 제품은 저장하는 RAG 근거에서 자동으로 기록합니다.')
            fields['products'] = reference_products
            duplicate = next((m for m in project['memos'] if m['save_key'] == key), None)
            if duplicate:
                return {**project, 'saved_memo_id': duplicate['id'], 'duplicate': True}
            if len(project['memos']) >= 5000:
                raise ValueError('파이프라인당 메모는 5,000개까지 저장할 수 있습니다.')
            stamp = now()
            memo = {'id': uuid4().hex, **fields, 'origin': 'rag' if source else 'manual', 'source': source,
                    'save_key': key, 'archived': False, 'revision': 1, 'history': [], 'created_at': stamp, 'updated_at': stamp}
            memo['items'] = memo_items.annotate(memo_items.build(memo['id'], fields['body'], source, item_context), payload.get('annotations', []))
            project['memos'].insert(0, memo)
            self.save(project)
            return {**self.pipeline_record(project), 'saved_memo_id': memo['id'], 'duplicate': False}

    def revise(self, project_id, memo_id, payload):
        if not isinstance(payload, dict) or set(payload) - {'body', 'stage_ids', 'products', 'tags', 'archived', 'expected_revision', 'annotations'} or not {'body', 'stage_ids', 'products', 'tags', 'archived', 'expected_revision'} <= set(payload):
            raise ValueError('메모 수정 요청을 확인하세요.')
        if type(payload['expected_revision']) is not int or type(payload['archived']) is not bool:
            raise ValueError('메모 버전과 보관 상태를 확인하세요.')
        identity(memo_id)
        with self.writing():
            project = self.get(project_id)
            memo = next((m for m in project['memos'] if m['id'] == memo_id), None)
            if memo is None:
                raise KeyError(memo_id)
            if memo['revision'] != payload['expected_revision']:
                raise RevisionConflict('다른 창에서 메모가 바뀌었습니다. 입력을 보존한 뒤 다시 불러오세요.')
            fields = {**self.fields(payload), 'archived': payload['archived']}
            if 'annotations' in payload:
                fields['items'] = memo_items.annotate(memo.get('items', []), payload['annotations'])
            if memo['origin'] == 'manual' and memo.get('items') and not memo['items'][0].get('context') and fields['body'] != memo['body']:
                fields['items'] = deepcopy(fields.get('items', memo['items']))
                fields['items'][0]['text'] = fields['body']
            if fields['products'] != memo['products']:
                raise ValueError('저장 당시 참고 제품은 변경할 수 없습니다. 메모 내용과 태그를 수정하세요.')
            if any(memo.get(k) != v for k, v in fields.items()):
                memo['history'].append({k: deepcopy(memo.get(k)) for k in (*fields, 'revision', 'updated_at')})
                memo.update(fields, revision=memo['revision'] + 1, updated_at=now())
                self.save(project)
            return self.pipeline_record(project)

    def structure(self, project_id, memo_id, expected_revision, source):
        """Explicit, non-destructive conversion from the original hash-checked report."""
        if type(expected_revision) is not int:
            raise ValueError('메모 버전을 확인하세요.')
        with self.writing():
            project = self.get(project_id)
            memo = next((m for m in project['memos'] if m['id'] == identity(memo_id)), None)
            if memo is None:
                raise KeyError(memo_id)
            if memo['revision'] != expected_revision:
                raise RevisionConflict('다른 창에서 메모가 바뀌었습니다. 다시 불러오세요.')
            old = memo.get('source')
            if not old or not source or old.get('report_sha256') != source.get('report_sha256') or old.get('key') != source.get('key'):
                raise ValueError('저장 당시 보고서와 일치하지 않습니다. 기존 메모는 보존했습니다.')
            if memo.get('items'):
                return self.pipeline_record(project)
            items = memo_items.build(memo['id'], memo['body'], source)
            if not items:
                raise ValueError('원 보고서에 분리 가능한 주장이 없습니다. 원문 메모를 유지합니다.')
            memo['history'].append({k: deepcopy(memo.get(k)) for k in ('body', 'items', 'source', 'revision', 'updated_at')})
            memo.update(source=source, items=items, revision=memo['revision'] + 1, updated_at=now())
            self.save(project)
            return self.pipeline_record(project)

    def register_documents(self, project_id, bundle):
        """Add internal document versions (immutable); an identical re-import changes nothing."""
        from rag_service import internal_documents
        with self.writing():
            project = self.get(project_id)
            documents, scopes, simulation, issues = internal_documents.normalize_bundle(bundle, project.get('work_scopes'))
            settings = lambda: deepcopy((project.get('work_scopes'), project.get('simulation'), project.get('issues')))
            before = settings()
            result = internal_documents.merge(project, documents, scopes, simulation, issues)
            if result['added'] or before != settings():
                self.save(project)
            return {**result, 'project_id': project['id'], 'document_count': len(project['documents']),
                    'project_revision': project['revision']}

    def save(self, project):
        project['revision'] += 1
        project['updated_at'] = now()
        self.write(project)

    def remember_document(self, evidence, path):
        """Only called with a path already resolved by the server's source viewer."""
        from rag.portable_paths import resolve
        resolved = Path(resolve(path)).resolve(strict=True)
        with resolved.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        key = json.dumps([evidence['document_id'], evidence['version_id']], ensure_ascii=False)
        with self.writing():
            registry = self.directory / 'document_versions.json'
            records = json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else {}
            old = records.get(key)
            if old and old['sha256'] != digest:
                raise ValueError('같은 문서 버전의 원본이 변경됐습니다. 기존 근거를 보존했습니다.')
            records[key] = {'path': str(resolved), 'sha256': digest}
            atomic(registry, records)

    def document_path(self, evidence):
        self.ready()
        registry = self.directory / 'document_versions.json'
        key = json.dumps([evidence.get('document_id'), evidence.get('version_id')], ensure_ascii=False)
        records = json.loads(registry.read_text(encoding='utf-8')) if registry.exists() else {}
        record = records.get(key)
        if not record:
            raise KeyError('저장되지 않은 문서 버전입니다.')
        from rag.portable_paths import resolve
        path = Path(resolve(record['path'])).resolve(strict=True)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
                raise ValueError('보관한 근거 원본의 해시가 바뀌었습니다.')
        return str(path)


class PostgresWorkspaceStore(WorkspaceStore):
    """Same notebook contract in an independent app schema, not catalog mirrors.

    One JSON document per project is intentional for the first local notebook:
    memo revisions and source packets are committed together. The storage API
    remains independent of its representation for a later normalized schema.
    """
    def __init__(self, connector=None):
        from threading import local
        from storage.postgres import connect
        self.connector = connector or connect
        self.lock = RLock()
        self.local = local()
        self.directory = None

    def ready(self):
        pass

    @contextmanager
    def connection(self):
        current = getattr(self.local, 'conn', None)
        if current is not None:
            yield current
        else:
            with self.connector(connect_timeout=5) as conn:
                yield conn

    @contextmanager
    def writing(self):
        with self.lock, self.connector(connect_timeout=5) as conn, conn.transaction():
            conn.execute("SET LOCAL lock_timeout = '5s'")
            conn.execute('SELECT pg_advisory_xact_lock(20261015, 1)')
            self.local.conn = conn
            try:
                yield
            finally:
                self.local.conn = None

    def get(self, project_id):
        with self.connection() as conn:
            row = conn.execute('SELECT record FROM app.workspace_projects WHERE project_id = %s AND deleted_at IS NULL',
                               (identity(project_id),)).fetchone()
        if not row:
            raise KeyError(project_id)
        return self.pipeline_record(row[0])

    def list(self):
        with self.connection() as conn:
            rows = conn.execute("""SELECT record->>'id', record->>'name', record->>'ingredient',
                record->>'updated_at',
                (SELECT count(*) FROM jsonb_array_elements(record->'memos') m WHERE NOT (m->>'archived')::boolean)
                FROM app.workspace_projects WHERE deleted_at IS NULL ORDER BY updated_at DESC""").fetchall()
        projects = [dict(zip(('id', 'name', 'ingredient', 'updated_at', 'memo_count'), row)) for row in rows]
        return {'projects': projects, 'stages': deepcopy(STAGES), 'storage': 'postgres'}

    def write(self, project):
        from psycopg.types.json import Jsonb
        with self.connection() as conn:
            conn.execute("""INSERT INTO app.workspace_projects (project_id, name, record)
                VALUES (%s, %s, %s) ON CONFLICT (project_id) DO UPDATE
                SET name = EXCLUDED.name, record = EXCLUDED.record, updated_at = now()""",
                (project['id'], project['name'], Jsonb(project)))

    def delete(self, project_id):
        """Hide a pipeline (deleted_at); the row and its memos stay in the database."""
        with self.writing():
            row = self.local.conn.execute("""UPDATE app.workspace_projects SET deleted_at = now()
                WHERE project_id = %s AND deleted_at IS NULL RETURNING project_id""", (identity(project_id),)).fetchone()
            if not row:
                raise KeyError(project_id)
            return {'id': project_id, 'deleted': True}

    def remember_document(self, evidence, path):
        from rag.portable_paths import resolve
        resolved = Path(resolve(path)).resolve(strict=True)
        with resolved.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        with self.writing():
            conn = self.local.conn
            row = conn.execute('SELECT sha256 FROM app.memo_documents WHERE document_id = %s AND version_id = %s',
                               (evidence['document_id'], evidence['version_id'])).fetchone()
            if row and row[0] != digest:
                raise ValueError('같은 문서 버전의 원본이 변경됐습니다. 기존 근거를 보존했습니다.')
            conn.execute("""INSERT INTO app.memo_documents (document_id, version_id, path, sha256)
                VALUES (%s, %s, %s, %s) ON CONFLICT (document_id, version_id)
                DO UPDATE SET path = EXCLUDED.path""", (evidence['document_id'], evidence['version_id'], str(resolved), digest))

    def document_path(self, evidence):
        with self.connection() as conn:
            row = conn.execute('SELECT path, sha256 FROM app.memo_documents WHERE document_id = %s AND version_id = %s',
                               (evidence.get('document_id'), evidence.get('version_id'))).fetchone()
        if not row:
            raise KeyError('저장되지 않은 문서 버전입니다.')
        from rag.portable_paths import resolve
        path = Path(resolve(row[0])).resolve(strict=True)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != row[1]:
                raise ValueError('보관한 근거 원본의 해시가 바뀌었습니다.')
        return str(path)


def source_packet(jobs, request):
    """Resolve references from an archived report, never trust client-supplied quotes."""
    if not isinstance(request, dict) or set(request) - {'job_id', 'result_index', 'kind', 'claim_id', 'claim_ids', 'evidence_id'}:
        raise ValueError('근거 저장 요청을 확인하세요.')
    job_id = identity(request.get('job_id'))
    job = jobs.get(job_id)
    if job.get('state') != 'completed':
        raise ValueError('완료된 답변의 근거만 저장할 수 있습니다.')
    index = request.get('result_index')
    results = job['report'].get('results') or []
    if type(index) is not int or not 0 <= index < len(results):
        raise ValueError('답변 위치를 확인하세요.')
    result = results[index]
    kind = request.get('kind')
    selected = []
    if 'claim_ids' in request and kind != 'answer':
        raise ValueError('답변 항목 선택은 전체 답변 저장에서만 지원합니다.')
    if kind == 'claim':
        claim = next((c for c in result.get('claims', []) if c.get('id') == request.get('claim_id')), None)
        if claim is None:
            raise ValueError('저장할 주장을 찾지 못했습니다.')
        original, quotes = claim['text'], deepcopy(claim.get('sources') or [])
        selected = [claim]
    elif kind == 'answer':
        claims = result.get('claims') or []
        if 'claim_ids' in request:
            ids = request['claim_ids']
            if not isinstance(ids, list) or not ids or len(ids) > 64 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or not set(ids) <= {c['id'] for c in claims}:
                raise ValueError('저장할 답변 항목을 한 개 이상 선택하세요.')
            claims = [c for c in claims if c['id'] in ids]
        selected = claims
        original = result.get('answer_ko') or ''
        if 'claim_ids' in request:
            original = '\n\n'.join(c['text'] for c in claims)
        quotes = [deepcopy(s) for c in claims for s in c.get('sources', [])]
    elif kind == 'evidence':
        eid = request.get('evidence_id')
        evidence = (result.get('evidence') or {}).get(eid)
        if evidence is None:
            raise ValueError('저장할 인용을 찾지 못했습니다.')
        original = f"{evidence.get('title', '')} · p.{evidence.get('pdf_page')}"
        quotes = [{'evidence': eid, 'quote': ''}]
    else:
        raise ValueError('근거 저장 종류를 확인하세요.')
    evidence_ids = list(dict.fromkeys(s.get('evidence') for s in quotes))
    if not evidence_ids or any(eid not in (result.get('evidence') or {}) for eid in evidence_ids):
        raise ValueError('추적할 수 있는 근거가 없습니다.')
    evidence_rows = []
    for eid in evidence_ids:
        block = result['evidence'][eid]
        path = jobs.pages.path(block)
        jobs.workspaces.remember_document(block, path)
        keys = ('document_id', 'version_id', 'chunk_id', 'pdf_page', 'pdf_page_label', 'title', 'source', 'product_id',
                 'jurisdiction', 'source_url', 'source_refs', 'source_spans', 'structure', 'document_metadata')
        evidence_rows.append({'id': eid, **{k: deepcopy(block.get(k)) for k in keys}})
    locator = {k: request[k] for k in ('job_id', 'result_index', 'kind', 'claim_id', 'claim_ids', 'evidence_id') if k in request}
    if 'claim_ids' in locator:
        locator['claim_ids'] = sorted(locator['claim_ids'])
    claim_keys = ('id', 'text', 'kind', 'claim_basis', 'parent_claim_id', 'products', 'task_ids', 'sources', 'support_review')
    return {**locator, 'key': hashlib.sha256(json.dumps(locator, sort_keys=True).encode()).hexdigest(),
            'question': job.get('question') or job['report'].get('question') or '',
            'original_text': original, 'quotes': quotes, 'evidence': evidence_rows,
            'claims': [{k: deepcopy(c[k]) for k in claim_keys if k in c} for c in selected],
            'report_origin': job['report'].get('origin', 'rag'),
            'report_sha256': jobs.reviews.report_hash(job), 'index': deepcopy(job['report'].get('index'))}
