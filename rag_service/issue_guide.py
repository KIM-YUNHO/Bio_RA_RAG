"""RAG guide for an internal issue: the issue's public-source questions run through the RA service, then one model call
organizes the cited answers into cause / experiments / response / mock review questions / precedents.

The internal facts say what happened; the public answers (other products' assessments and guidelines) say how similar
differences were investigated and justified. Products in the issue's `exclude_products` are left out of every search
(evaluation: the real case is held out). The answer key never reaches a model: a guide is compared with it when read,
by a lexical check that is a triage signal, not a grade. Guides are kept as JSON records, one run at a time.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import Lock
import time
from types import SimpleNamespace
from uuid import uuid4

from rag_service import internal_documents

ID = re.compile(r'[0-9a-f]{32}')
SECTIONS = [('cause', '원인 규명 방향'), ('experiments', '실험 설계'), ('response', '기관 대응 논리'),
            ('mock_questions', '예상 심사 질의'), ('precedents', '다른 제품 사례')]
BASIS = {'public': '공개 근거', 'internal': '사내 자료', 'suggestion': 'AI 제안'}
QUOTE_LIMIT = 500

SYSTEM = """너는 바이오시밀러 개발사의 RA·CMC 담당자를 돕는다. 사내 이슈 하나와, 그 이슈에 대해 공개 심사 자료·지침을 검색해 얻은 답변(인용 포함)을 받는다. 이를 바탕으로 이슈 대응 가이드를 쓴다.
규칙:
- 공개 자료에서 온 내용은 basis="public"으로 쓰고 근거 P-ID를 source_ids에 넣는다. 공개 자료가 말하지 않은 내용을 공개 근거처럼 쓰지 않는다.
- 사내 사실은 basis="internal"로 쓰고 U-ID를 넣는다.
- 근거를 바탕으로 한 너의 제안·추론은 basis="suggestion"으로 쓴다. 기대는 근거가 있으면 그 ID를 넣는다.
- 다른 제품의 사례는 제품 이름을 밝혀 쓰고, 이 제품(P17)에서 확인된 사실처럼 쓰지 않는다.
- 기관의 판단을 예측하거나 승인을 보장하지 않는다. 공개 근거가 부족한 부분은 부족하다고 쓴다.
섹션:
- cause: 원인 규명 방향. 어떤 품질 특성이 원인일 수 있고 왜 그렇게 보는지.
- experiments: 실험 설계. 무엇을, 어떤 시료·비교로 시험하고, 어떤 결과가 나오면 무엇을 뜻하는지.
- response: 기관 대응 논리. 차이를 어떤 근거의 조합으로 설명·정당화할지.
- mock_questions: 예상 심사 질의. 질문, 기관이 왜 물을지, 준비할 자료.
- precedents: 다른 제품 사례. 제품·기관, 무엇이 달랐고 어떻게 평가됐는지.
각 섹션 2~4개 항목. 한국어로 짧고 구체적으로 쓴다. 본문에 ID를 괄호로 넣지 말고 source_ids에만 넣는다."""


def now():
    return datetime.now(timezone.utc).isoformat()


def find_issue(project, issue_id):
    issue = next((i for i in project.get('issues', []) if i['id'] == issue_id), None)
    if issue is None:
        raise KeyError(issue_id)
    return issue


def facts(project, issue):
    """The issue's internal units, as the guide and the screen read them."""
    index = {u['unit_id']: (d, u) for d in project.get('documents', []) for u in d['units']}
    rows = []
    for unit_id in issue['unit_ids']:
        document, unit = index[unit_id]
        rows.append({'id': unit_id, 'text': unit['text'], 'title': document['title'], 'date': document['date'],
                     'speaker': unit['context'].get('speaker'), 'kind': internal_documents.KINDS[document['kind']]})
    return rows


def answer_view(question, job):
    """What the guide keeps of one RA answer: status, answer text, claims and the evidence they cite."""
    report = job.get('report') or {}
    result = (report.get('results') or [{}])[0]
    evidence = result.get('evidence') or {}
    claims, cited = [], []
    for claim in result.get('claims') or []:
        sources = [s for s in claim.get('sources') or [] if s.get('evidence') in evidence]
        claims.append({'text': claim.get('text', ''), 'products': claim.get('products') or [],
                       'evidence': list(dict.fromkeys(s['evidence'] for s in sources)),
                       'quotes': {s['evidence']: (s.get('quote') or '')[:QUOTE_LIMIT] for s in sources}})
        cited += [s['evidence'] for s in sources]
    refs = {eid: {'document_id': e.get('document_id'), 'version_id': e.get('version_id'), 'page': e.get('pdf_page'),
                  'title': e.get('title'), 'product': e.get('product_id'), 'agency': e.get('source'),
                  'jurisdiction': e.get('jurisdiction'), 'group': e.get('doc_group')}
            for eid, e in evidence.items() if eid in cited}
    return {**question, 'job_id': job['id'], 'state': job.get('state'), 'status': result.get('status'),
            'answer': result.get('answer_ko') or '', 'claims': claims, 'evidence': refs,
            'error': job.get('error')}


def public_sources(answers):
    """One P-ID per cited page across all answers; each keeps the (job, evidence) the source viewer opens."""
    sources, by_page, claim_ids = [], {}, []
    for answer in answers:
        mapped = []
        for claim in answer.get('claims', []):
            ids = []
            for eid in claim['evidence']:
                ref = answer['evidence'][eid]
                key = (ref['document_id'], ref['version_id'], ref['page'])
                if key not in by_page:
                    by_page[key] = f'P{len(sources) + 1}'
                    sources.append({'id': by_page[key], **ref, 'job_id': answer['job_id'], 'evidence_id': eid,
                                    'question_id': answer['id'], 'quote': claim['quotes'].get(eid, '')})
                ids.append(by_page[key])
            mapped.append(list(dict.fromkeys(ids)))
        claim_ids.append(mapped)
    return sources, claim_ids


def messages(issue, fact_rows, answers, sources, claim_ids):
    """The prompt: issue, internal facts, the RA answers with P-IDs, and the cited pages. No answer key."""
    payload = {
        'issue': {'title': issue['title'], 'date': issue['date'], 'summary': issue['summary']},
        'internal_facts': [{'id': f['id'], 'source': f"{f['title']} · {f['date']}", 'speaker': f['speaker'], 'text': f['text']}
                           for f in fact_rows],
        'public_answers': [{'question_id': a['id'], 'topic': internal_documents.ISSUE_TOPICS[a['topic']], 'question': a['text'],
                            'status': a.get('status'), 'answer': a.get('answer', ''),
                            'claims': [{'text': c['text'], 'source_ids': ids} for c, ids in zip(a.get('claims', []), mapped)]}
                           for a, mapped in zip(answers, claim_ids)],
        'public_sources': [{'id': s['id'], 'product': s['product'] or '공통 지침', 'agency': s['agency'], 'title': s['title'],
                            'page': s['page'], 'quote': s['quote']} for s in sources]}
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, indent=1)}]


def schema(ids):
    refs = {'type': 'array', 'maxItems': 8, 'items': {'type': 'string', 'enum': list(ids)}}
    item = {'type': 'object', 'additionalProperties': False, 'required': ['text', 'basis', 'source_ids'],
            'properties': {'text': {'type': 'string'}, 'basis': {'type': 'string', 'enum': list(BASIS)}, 'source_ids': refs}}
    question = {'type': 'object', 'additionalProperties': False, 'required': ['question', 'why', 'prepare', 'source_ids'],
                'properties': {'question': {'type': 'string'}, 'why': {'type': 'string'}, 'prepare': {'type': 'string'},
                               'source_ids': refs}}
    sections = {key: {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': question if key == 'mock_questions' else item}
                for key, _ in SECTIONS}
    return {'name': 'issue_guide', 'max_output_tokens': 4000,
            'schema': {'type': 'object', 'additionalProperties': False, 'required': ['summary', 'sections'],
                       'properties': {'summary': {'type': 'string'},
                                      'sections': {'type': 'object', 'additionalProperties': False,
                                                   'required': [key for key, _ in SECTIONS], 'properties': sections}}}}


INLINE_IDS = re.compile(r'\s*[\[(](?:[PU]\d{1,3}(?:\s*,\s*)?)+[\])]')


def normalize(raw, ids):
    """The model's JSON as screen sections; refuses a shape the schema did not allow."""
    if not isinstance(raw, dict) or not isinstance(raw.get('summary'), str) or not isinstance(raw.get('sections'), dict):
        raise ValueError('모델 응답 형식이 맞지 않습니다.')
    known = set(ids)
    clean = lambda text: INLINE_IDS.sub('', text).strip()
    sections = []
    for key, label in SECTIONS:
        rows = raw['sections'].get(key)
        if not isinstance(rows, list) or not rows:
            raise ValueError('모델 응답에 빈 섹션이 있습니다.')
        items = []
        for row in rows:
            refs = [i for i in dict.fromkeys(row.get('source_ids') or []) if i in known] if isinstance(row, dict) else None
            if key == 'mock_questions':
                if refs is None or not all(isinstance(row.get(k), str) for k in ('question', 'why', 'prepare')):
                    raise ValueError('예상 질의 항목 형식이 맞지 않습니다.')
                items.append({'question': clean(row['question']), 'why': clean(row['why']), 'prepare': clean(row['prepare']),
                              'source_ids': refs})
            else:
                if refs is None or row.get('basis') not in BASIS or not isinstance(row.get('text'), str):
                    raise ValueError('가이드 항목 형식이 맞지 않습니다.')
                items.append({'text': clean(row['text']), 'basis': row['basis'], 'basis_label': BASIS[row['basis']], 'source_ids': refs})
        sections.append({'key': key, 'label': label, 'items': items})
    return {'summary': clean(raw['summary']), 'sections': sections}


def flatten(guide):
    lines = [guide['summary']]
    for section in guide['sections']:
        for item in section['items']:
            lines.append(' '.join(item.get(k, '') for k in ('text', 'question', 'why', 'prepare')).strip())
    return '\n'.join(lines)


def audit(guide, sources, excluded_names):
    """Narrow checks: public items without a public source, and names of held-out products in the text."""
    warnings = []
    public = {s['id'] for s in sources}
    for section in guide['sections']:
        for index, item in enumerate(section['items']):
            text = ' '.join(item.get(k, '') for k in ('text', 'question', 'why', 'prepare'))
            if item.get('basis') == 'public' and not set(item['source_ids']) & public:
                warnings.append({'section': section['key'], 'item': index, 'code': 'public_without_source',
                                 'detail': '공개 근거로 표시했지만 공개 자료 인용이 없습니다.'})
            for name in excluded_names:
                if name.casefold() in text.casefold():
                    warnings.append({'section': section['key'], 'item': index, 'code': 'held_out_product_named',
                                     'detail': f'검색에서 뺀 제품({name})이 언급됐습니다.'})
    return warnings


def review(guide, answer_key):
    """Answer-key coverage by lexical match: a triage signal for a human reader, not a grade."""
    if not answer_key:
        return None
    rows = []
    for point in answer_key['points']:
        found = None
        for section in guide['sections']:
            if point.get('sections') and section['key'] not in point['sections']:
                continue
            for item in section['items']:
                text = ' '.join(item.get(k, '') for k in ('text', 'question', 'why', 'prepare'))
                match = re.search(point['pattern'], text, re.I)
                if match and found is None:
                    found = {'section': section['label'], 'match': match.group(0)}
        rows.append({'id': point['id'], 'text': point['text'], 'page': point['page'], 'covered': found is not None,
                     **({'where': found} if found else {})})
    return {'label': answer_key['label'], 'summary': answer_key['summary'], 'points': rows,
            'method': '어휘 일치 1차 분류(판정 아님). 가이드 본문을 직접 읽고 판단하세요.'}


class IssueGuideService:
    def __init__(self, jobs, directory, model='gpt-5.6-luna', chat_factory=None, context_tokens=32768, poll=2.0, wait_limit=1800):
        self.jobs = jobs
        self.directory = Path(directory).resolve() if directory is not None else None
        self.model = model
        self.chat_factory = chat_factory
        self.context_tokens = context_tokens
        self.poll = poll
        self.wait_limit = wait_limit
        self.lock = Lock()
        self.busy = False
        self.records = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='issue-guide')

    def ready(self):
        if self.directory is None:
            raise ValueError('이슈 가이드 저장 경로가 설정되지 않았습니다.')
        self.directory.mkdir(parents=True, exist_ok=True)

    def client(self, folder):
        if self.chat_factory:
            return self.chat_factory(folder)
        from rag.openai_chat import EvaluationAPIChat
        client = EvaluationAPIChat(folder, max_new_calls=1, model=self.model)
        client.config = SimpleNamespace(model=self.model, num_ctx=self.context_tokens, num_predict=4000)
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

    def update(self, guide_id, **changes):
        with self.lock:
            self.records[guide_id].update(changes)
            record = deepcopy(self.records[guide_id])
        self.persist(record)
        return record

    def submit(self, project_id, payload):
        self.ready()
        if not isinstance(payload, dict) or set(payload) != {'issue_id'}:
            raise ValueError('이슈를 선택하세요.')
        project = self.jobs.workspaces.get(project_id)
        try:
            issue = find_issue(project, payload['issue_id'])
        except KeyError:
            raise ValueError('등록되지 않은 이슈입니다.') from None
        products = list(self.jobs.pipeline.products)
        excluded = issue['search']['exclude_products']
        if set(excluded) - set(products):
            raise ValueError('검색에서 뺄 제품이 등록된 제품이 아닙니다.')
        scope = [p for p in products if p not in excluded]
        with self.lock:
            if self.busy:
                raise BlockingIOError('다른 이슈 가이드를 만드는 중입니다. 완료 후 다시 시도하세요.')
            self.busy = True
        record = {'id': uuid4().hex, 'kind': 'issue_guide', 'state': 'running', 'stage': 'searching', 'created_at': now(),
                  'project_id': project['id'], 'project_name': project['name'], 'project_revision': project['revision'],
                  'issue_id': issue['id'], 'issue_title': issue['title'], 'model': self.model,
                  'scope': {'products': scope, 'excluded_products': excluded, 'note': issue['search']['note']},
                  'facts': facts(project, issue),
                  'questions': [{**q, 'state': 'waiting'} for q in issue['questions']]}
        try:
            with self.lock:
                self.records[record['id']] = record
            self.persist(record)
            self.executor.submit(self.run, record['id'], deepcopy(issue))
        except Exception:
            with self.lock:
                self.busy = False
            raise
        return self.summary(record)

    def ask(self, question, products):
        """One RA question through the service's own queue; waits while a user's question is running."""
        deadline = time.monotonic() + self.wait_limit
        while True:
            try:
                job_id = self.jobs.submit({'question': question, 'products': products})['id']
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError('검색 대기 시간이 지났습니다.') from None
                time.sleep(self.poll)
        while (job := self.jobs.get(job_id))['state'] == 'running':
            if time.monotonic() > deadline:
                raise TimeoutError('검색이 끝나지 않았습니다.')
            time.sleep(self.poll)
        return job

    def run(self, guide_id, issue):
        client = None
        try:
            with self.lock:
                record = deepcopy(self.records[guide_id])
            answers, questions = [], record['questions']
            for index, question in enumerate(issue['questions']):
                questions[index]['state'] = 'running'
                self.update(guide_id, questions=deepcopy(questions))
                job = self.ask(question['text'], record['scope']['products'])
                answer = answer_view(question, job)
                leaked = sorted({r['product'] for r in answer['evidence'].values()} & set(record['scope']['excluded_products']))
                if leaked:
                    raise RuntimeError('검색에서 뺀 제품의 문서가 인용됐습니다: ' + ', '.join(leaked))
                questions[index] = {**question, 'state': job['state'], 'job_id': job['id'], 'status': answer['status']}
                answers.append(answer)
                self.update(guide_id, questions=deepcopy(questions), answers=deepcopy(answers))
            sources, claim_ids = public_sources(answers)
            if not sources:
                raise RuntimeError('공개 자료에서 인용할 근거를 찾지 못했습니다.')
            self.update(guide_id, stage='writing', sources=sources)
            ids = [s['id'] for s in sources] + [f['id'] for f in record['facts']]
            client = self.client(self.directory / (guide_id + '_api'))
            if hasattr(client, 'case'):
                client.case = 'issue_guide'
            response = client(messages(issue, record['facts'], answers, sources, claim_ids), schema(ids))
            guide = normalize(json.loads(response['text']), ids)
            self.update(guide_id, state='completed', stage='completed', guide=guide,
                        warnings=audit(guide, sources, record['scope']['excluded_products']),
                        usage={'input_tokens': response.get('prompt_tokens', 0), 'output_tokens': response.get('completion_tokens', 0)},
                        model_reported=response.get('model_reported'), completed_at=now())
        except Exception as exc:  # noqa: BLE001 - the record must end in a terminal state
            self.update(guide_id, state='failed', stage='failed', error=f'{type(exc).__name__}: {exc}', completed_at=now())
        finally:
            if client is not None and hasattr(client, 'close'):
                client.close()
            with self.lock:
                self.busy = False

    def load(self, guide_id):
        if not isinstance(guide_id, str) or not ID.fullmatch(guide_id):
            raise KeyError(guide_id)
        with self.lock:
            if guide_id in self.records:
                return deepcopy(self.records[guide_id])
        self.ready()
        path = self.directory / (guide_id + '.json')
        if not path.is_file():
            raise KeyError(guide_id)
        return json.loads(path.read_text(encoding='utf-8'))

    def get(self, project_id, guide_id):
        record = self.load(guide_id)
        if record.get('project_id') != project_id:
            raise KeyError(guide_id)
        if record['state'] == 'running' and guide_id not in self.records:
            record.update(state='failed', stage='failed', error='서비스가 다시 시작되어 중단됐습니다.')
        if record['state'] == 'completed':
            # Compared on reading, against the answer key the project holds now (it may be refined after the run).
            try:
                key = find_issue(self.jobs.workspaces.get(project_id), record['issue_id']).get('answer_key')
            except KeyError:
                key = None
            record['review'] = review(record['guide'], key)
        return record

    @staticmethod
    def summary(record):
        keys = ('id', 'state', 'stage', 'created_at', 'completed_at', 'project_id', 'issue_id', 'issue_title', 'error')
        result = {k: record.get(k) for k in keys}
        result['failed_questions'] = sum(q.get('state') == 'failed' for q in record.get('questions', []))
        result['covered'] = sum(p['covered'] for p in (record.get('review') or {}).get('points', []))
        result['points'] = len((record.get('review') or {}).get('points', []))
        return result

    def list(self, project_id):
        self.ready()
        rows = []
        for path in self.directory.glob('*.json'):
            if not ID.fullmatch(path.stem):
                continue
            try:
                record = self.get(project_id, path.stem)
            except (KeyError, ValueError, OSError):
                continue
            rows.append(self.summary(record))
        return sorted(rows, key=lambda r: r['created_at'], reverse=True)[:20]

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
