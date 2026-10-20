"""RAG guide for an internal issue, in three model steps around the RA service.

1. Plan: from the issue and its internal facts, the model lists candidate causes and writes public-source questions.
   Cause questions ask about the candidate attribute itself, not only the issue's test, because other products'
   assessments usually discuss an attribute difference under whatever function it touched there.
2. Search: the questions run through the RA service (cited answers). Products in the issue's `exclude_products` are
   left out of every search (evaluation: the real case is held out); a cited held-out product fails the guide.
3. Follow up: from the first answers, the model may add up to two questions on leads they revealed; they run too.
4. Write: one call organizes the cited answers into cause / experiments / response / mock review questions /
   precedents, citing P-IDs (cited pages) and U-IDs (internal units).

The answer key never reaches a model: a guide is compared with it when read, by a lexical check that is a triage signal,
not a grade. Guides are kept as JSON records, one run at a time.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
from threading import Lock
import time
from types import SimpleNamespace
from uuid import uuid4

from rag_service import internal_documents

ID = re.compile(r'[0-9a-f]{32}')
SECTIONS = [('cause', '원인 후보'), ('experiments', '실험 설계'), ('response', '기관 대응 논리'),
            ('mock_questions', '예상 심사 질의'), ('precedents', '다른 제품 사례')]
BASIS = {'public': '공개 근거', 'internal': '사내 자료', 'suggestion': 'AI 제안'}
LIKELIHOOD = {'high': '높음', 'medium': '중간', 'low': '낮음'}
TOPICS = {'precedents': '같은 시험 항목의 다른 제품 사례', 'cause': '원인 후보 확인', 'guideline': '지침 요구', 'followup': '추가 확인'}
QUOTE_LIMIT = 300
PLAN_QUESTIONS, FOLLOWUP_QUESTIONS = 5, 2

PLAN = """너는 바이오시밀러 RA·CMC 전문가다. 사내 이슈 하나를 받고, 이를 해결하기 위해 공개 심사 자료(EMA EPAR, FDA 심사 문서, Health Canada SBD)와 규제 지침에서 찾아볼 검색 계획을 세운다. 아직 답을 쓰지 않는다.
- hypotheses: 이 결과를 설명할 수 있는 후보 원인 3~5개를 가능성 높은 순서로. 품질 특성 차이라면 분석적 유사성 평가에서 흔히 차이가 나는 특성(당쇄, 전하 변이, 산화 등 번역후변형, 크기 변이·응집)과 시험법·제형 요인을 빠짐없이 검토한 뒤 고른다. 사내 사실(어느 시료·제형에서만 나타나는지, 시험 유효성)로 가능성을 가늠하되 확정하지 않는다. attribute는 후보 이름, reason은 그렇게 보는 이유 한 문장.
- questions: 공개 자료 검색 질문 4~5개. 한 질문은 한 특성에 대한 한 물음으로, 150자 안팎으로 쓴다.
  · precedents 1개: 같은 성분 바이오시밀러에서 이슈와 같은 시험 항목에 차이가 있었던 사례와 그 평가.
  · cause 2~3개: 유력한 후보 원인마다, 그 특성의 차이가 관찰됐을 때 심사 자료가 원인을 어떻게 확인했는지(추가 시험·시료 처리)와 기능·PK 영향을 어떻게 판단했는지. 다른 제품에서는 같은 특성 차이가 다른 시험 항목과 함께 평가됐을 수 있으므로, 이슈의 시험 항목 이름에 묶지 말고 그 특성 자체를 묻는다.
  · guideline 1개: 범위 밖 결과나 품질 차이를 정당화할 때 지침이 요구하는 것.
- 질문에 제품명·회사명은 쓰지 않는다. 성분은 '{ingredient} 바이오시밀러'라고 쓴다. 검색이 잘 되도록 핵심 용어는 영문을 함께 쓴다."""

FOLLOWUP = """너는 공개 자료 검색 계획을 이어서 다듬는 바이오시밀러 RA·CMC 전문가다. 후보 원인과 1차 검색 답변(인용된 주장)을 보고, 가이드를 쓰기 전에 더 확인할 검색 질문을 0~2개 만든다.
- 1차 답변에 새로 드러난 단서(예: 특정 품질 특성의 차이와 그것을 확인한 시험·처리)를 이 이슈의 후보 원인과 연결해 확인하는 질문을 우선한다.
- 근거를 하나도 얻지 못한 유력 후보가 있으면 표현을 바꿔 다시 묻는다.
- 이미 답을 얻은 내용은 다시 묻지 않는다. 필요 없으면 빈 목록을 낸다.
- 한 질문은 한 물음으로 150자 안팎. 제품명·회사명은 쓰지 않고 성분은 '{ingredient} 바이오시밀러'라고 쓴다. 핵심 용어는 영문을 함께 쓴다."""

WRITE = """너는 바이오시밀러 개발사의 RA·CMC 담당자를 돕는다. 사내 이슈 하나, 검색 전에 세운 후보 원인, 공개 심사 자료·지침을 검색해 얻은 답변(인용 포함)을 받는다. 이를 바탕으로 이슈 대응 가이드를 쓴다.
규칙:
- 공개 자료에서 온 내용은 basis="public"으로 쓰고 근거 P-ID를 source_ids에 넣는다. 공개 자료가 말하지 않은 내용을 공개 근거처럼 쓰지 않는다.
- 사내 사실은 basis="internal"로 쓰고 U-ID를 넣는다.
- 근거를 바탕으로 한 너의 제안·추론은 basis="suggestion"으로 쓴다. 기대는 근거가 있으면 그 ID를 넣는다.
- 다른 제품의 사례는 제품 이름을 밝혀 쓰고, 이 제품에서 확인된 사실처럼 쓰지 않는다.
- 기관의 판단을 예측하거나 승인을 보장하지 않는다. 공개 근거가 부족한 부분은 부족하다고 쓴다.
출력:
- situation: 확인된 사실만 1~2문장. 원인을 단정하거나 대응 방향 하나를 고르지 않는다.
- candidates: 원인 후보 3~5개를 가능성 높은 순서로. 하나로 좁히지 않는다. 사내 사실과 공개 근거를 함께 따져 순서를 정한다.
  · name: 후보 이름(25자 이내). likelihood: high/medium/low.
  · support: 이 후보를 지지하는 근거 1~2문장. against: 반대 근거나 불확실성 1~2문장.
  · confirm: 이 후보를 확인하거나 배제하는 결정적 시험 한 줄(60자 이내). 공개 자료에서 다른 제품이 그 특성 차이를 확인할 때 쓴 방법(예: 특정 변이를 제거하거나 분리한 시료로 다시 측정)이 있으면, 그 방법을 이 이슈의 시험 항목에 옮겨 적용한다.
  · source_ids: 근거 ID.
- sections의 각 항목은 title(핵심 한 줄, 30자 이내)과 text(설명 1~3문장)로 쓴다.
  · experiments: 실험 설계. 무엇을 어떤 시료·비교로 시험하고, 어떤 결과가 나오면 어느 후보를 지지·배제하는지.
  · response: 기관 대응 논리. 차이를 어떤 근거의 조합으로 설명·정당화할지. 범위 밖 결과라면 비교 기준(범위 산정에 쓴 대조약 lot 수·제형·통계 방법)의 적절성도 점검 대상에 넣는다. 같은 제품의 다른 제형·시료 결과가 범위 안이라면 그 차이가 무엇을 뜻하는지, 정당화 근거로 쓸 수 있는지 검토한다.
  · mock_questions: 예상 심사 질의. question(질의), background(질의 배경: 기관이 이것을 확인하려는 이유), prepare(준비할 자료).
  · precedents: 다른 제품 사례. title은 '제품 · 기관 · 핵심', text는 무엇이 달랐고 어떻게 확인·평가됐는지.
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


def issue_payload(issue, fact_rows, ingredient):
    return {'ingredient': ingredient, 'issue': {'title': issue['title'], 'date': issue['date'], 'summary': issue['summary']},
            'internal_facts': [{'id': f['id'], 'source': f"{f['title']} · {f['date']}", 'speaker': f['speaker'], 'text': f['text']}
                               for f in fact_rows]}


def plan_messages(issue, fact_rows, ingredient):
    return [{'role': 'system', 'content': PLAN.format(ingredient=ingredient)},
            {'role': 'user', 'content': json.dumps(issue_payload(issue, fact_rows, ingredient), ensure_ascii=False, indent=1)}]


def plan_schema():
    question = {'type': 'object', 'additionalProperties': False, 'required': ['topic', 'text'],
                'properties': {'topic': {'type': 'string', 'enum': ['precedents', 'cause', 'guideline']}, 'text': {'type': 'string'}}}
    hypothesis = {'type': 'object', 'additionalProperties': False, 'required': ['attribute', 'reason'],
                  'properties': {'attribute': {'type': 'string'}, 'reason': {'type': 'string'}}}
    return {'name': 'issue_search_plan', 'max_output_tokens': 2000,
            'schema': {'type': 'object', 'additionalProperties': False, 'required': ['hypotheses', 'questions'],
                       'properties': {'hypotheses': {'type': 'array', 'minItems': 1, 'maxItems': 5, 'items': hypothesis},
                                      'questions': {'type': 'array', 'minItems': 1, 'maxItems': PLAN_QUESTIONS, 'items': question}}}}


def followup_messages(issue, fact_rows, ingredient, hypotheses, answers):
    payload = {**issue_payload(issue, fact_rows, ingredient), 'hypotheses': hypotheses,
               'first_answers': [{'question': a['text'], 'status': a.get('status') or a.get('state'),
                                  'claims': [c['text'] for c in a.get('claims', [])][:25]} for a in answers]}
    return [{'role': 'system', 'content': FOLLOWUP.format(ingredient=ingredient)},
            {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, indent=1)}]


def followup_schema():
    return {'name': 'issue_search_followup', 'max_output_tokens': 800,
            'schema': {'type': 'object', 'additionalProperties': False, 'required': ['questions'],
                       'properties': {'questions': {'type': 'array', 'maxItems': FOLLOWUP_QUESTIONS, 'items': {'type': 'string'}}}}}


def clean_questions(rows, blocked, start, round_no):
    """Model-written questions, numbered; one naming a blocked term (a held-out product) is dropped, not sent."""
    kept, dropped = [], []
    for row in rows:
        text = (row['text'] if isinstance(row, dict) else row).strip()[:600]
        topic = row.get('topic', 'followup') if isinstance(row, dict) else 'followup'
        if not text:
            continue
        hit = next((t for t in blocked if t.casefold() in text.casefold()), None)
        if hit:
            dropped.append({'text': text, 'reason': f'차단어 {hit}'})
            continue
        kept.append({'id': f'Q{start + len(kept)}', 'round': round_no, 'topic': topic if topic in TOPICS else 'followup',
                     'text': text, 'state': 'waiting'})
    return kept, dropped


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
    return {**{k: question[k] for k in ('id', 'round', 'topic', 'text')}, 'job_id': job['id'], 'state': job.get('state'),
            'status': result.get('status'), 'answer': result.get('answer_ko') or '', 'claims': claims, 'evidence': refs,
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


def messages(issue, fact_rows, ingredient, hypotheses, answers, sources, claim_ids):
    """The writing prompt: issue, internal facts, hypotheses, the RA claims with P-IDs, and the cited pages. No answer key.

    The answer prose is left out: it restates the claims, and 7 searches can cite 60-75 pages.
    """
    payload = {**issue_payload(issue, fact_rows, ingredient), 'hypotheses_before_search': hypotheses,
               'public_answers': [{'question_id': a['id'], 'question': a['text'], 'status': a.get('status'),
                                   'claims': [{'text': c['text'], 'source_ids': ids} for c, ids in zip(a.get('claims', []), mapped)]}
                                  for a, mapped in zip(answers, claim_ids)],
               'public_sources': [{'id': s['id'], 'product': s['product'] or '공통 지침', 'agency': s['agency'], 'title': s['title'],
                                   'page': s['page'], 'quote': s['quote']} for s in sources]}
    return [{'role': 'system', 'content': WRITE}, {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False, indent=1)}]


def schema(ids):
    refs = {'type': 'array', 'maxItems': 8, 'items': {'type': 'string', 'enum': list(ids)}}
    string = {'type': 'string'}
    candidate = {'type': 'object', 'additionalProperties': False, 'required': ['name', 'likelihood', 'support', 'against', 'confirm', 'source_ids'],
                 'properties': {'name': string, 'likelihood': {'type': 'string', 'enum': list(LIKELIHOOD)}, 'support': string,
                                'against': string, 'confirm': string, 'source_ids': refs}}
    item = {'type': 'object', 'additionalProperties': False, 'required': ['title', 'text', 'basis', 'source_ids'],
            'properties': {'title': string, 'text': string, 'basis': {'type': 'string', 'enum': list(BASIS)}, 'source_ids': refs}}
    question = {'type': 'object', 'additionalProperties': False, 'required': ['question', 'background', 'prepare', 'source_ids'],
                'properties': {'question': string, 'background': string, 'prepare': string, 'source_ids': refs}}
    sections = {key: {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': question if key == 'mock_questions' else item}
                for key, _ in SECTIONS[1:]}
    return {'name': 'issue_guide', 'max_output_tokens': 5000,
            'schema': {'type': 'object', 'additionalProperties': False, 'required': ['situation', 'candidates', 'sections'],
                       'properties': {'situation': string,
                                      'candidates': {'type': 'array', 'minItems': 2, 'maxItems': 5, 'items': candidate},
                                      'sections': {'type': 'object', 'additionalProperties': False,
                                                   'required': [key for key, _ in SECTIONS[1:]], 'properties': sections}}}}


INLINE_IDS = re.compile(r'\s*[\[(](?:[PU]\d{1,3}(?:\s*,\s*)?)+[\])]')
TEXT_KEYS = ('name', 'support', 'against', 'confirm', 'title', 'text', 'question', 'background', 'prepare')


def normalize(raw, ids):
    """The model's JSON as screen sections (ranked cause candidates first); refuses a shape the schema did not allow."""
    if (not isinstance(raw, dict) or not isinstance(raw.get('situation'), str) or not isinstance(raw.get('candidates'), list)
            or not raw['candidates'] or not isinstance(raw.get('sections'), dict)):
        raise ValueError('모델 응답 형식이 맞지 않습니다.')
    known = set(ids)
    clean = lambda text: INLINE_IDS.sub('', text).strip()
    refs = lambda row: [i for i in dict.fromkeys(row.get('source_ids') or []) if i in known]
    candidates = []
    for rank, row in enumerate(raw['candidates'], 1):
        if (not isinstance(row, dict) or row.get('likelihood') not in LIKELIHOOD
                or not all(isinstance(row.get(k), str) for k in ('name', 'support', 'against', 'confirm'))):
            raise ValueError('원인 후보 형식이 맞지 않습니다.')
        candidates.append({'rank': rank, 'name': clean(row['name']), 'likelihood': row['likelihood'],
                           'likelihood_label': LIKELIHOOD[row['likelihood']], 'support': clean(row['support']),
                           'against': clean(row['against']), 'confirm': clean(row['confirm']), 'source_ids': refs(row)})
    sections = [{'key': 'cause', 'label': SECTIONS[0][1], 'items': candidates}]
    for key, label in SECTIONS[1:]:
        rows = raw['sections'].get(key)
        if not isinstance(rows, list) or not rows:
            raise ValueError('모델 응답에 빈 섹션이 있습니다.')
        items = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('가이드 항목 형식이 맞지 않습니다.')
            if key == 'mock_questions':
                if not all(isinstance(row.get(k), str) for k in ('question', 'background', 'prepare')):
                    raise ValueError('예상 질의 항목 형식이 맞지 않습니다.')
                items.append({'question': clean(row['question']), 'background': clean(row['background']), 'prepare': clean(row['prepare']),
                              'source_ids': refs(row)})
            else:
                if row.get('basis') not in BASIS or not all(isinstance(row.get(k), str) for k in ('title', 'text')):
                    raise ValueError('가이드 항목 형식이 맞지 않습니다.')
                items.append({'title': clean(row['title']), 'text': clean(row['text']), 'basis': row['basis'],
                              'basis_label': BASIS[row['basis']], 'source_ids': refs(row)})
        sections.append({'key': key, 'label': label, 'items': items})
    return {'situation': clean(raw['situation']), 'sections': sections}


def item_text(item):
    return ' '.join(item.get(k, '') for k in TEXT_KEYS).strip()


def audit(guide, sources, excluded_names):
    """Narrow checks: public items without a public source, and names of held-out products in the text."""
    warnings = []
    public = {s['id'] for s in sources}
    for section in guide['sections']:
        for index, item in enumerate(section['items']):
            text = item_text(item)
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
                match = re.search(point['pattern'], item_text(item), re.I)
                if match and found is None:
                    where = f"{section['label']} {item['rank']}순위" if 'rank' in item else section['label']
                    found = {'section': where, 'match': match.group(0)}
        rows.append({'id': point['id'], 'text': point['text'], 'page': point['page'], 'covered': found is not None,
                     **({'where': found} if found else {})})
    return {'label': answer_key['label'], 'summary': answer_key['summary'], 'points': rows,
            'method': '어휘 일치 1차 분류(판정 아님). 가이드 본문을 직접 읽고 판단하세요.'}


class IssueGuideService:
    def __init__(self, jobs, directory, model='gpt-5.6-luna', chat_factory=None, context_tokens=65536, poll=2.0, wait_limit=1800):
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
        client = EvaluationAPIChat(folder, max_new_calls=3, model=self.model)
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
        with self.lock:
            if self.busy:
                raise BlockingIOError('다른 이슈 가이드를 만드는 중입니다. 완료 후 다시 시도하세요.')
            self.busy = True
        record = {'id': uuid4().hex, 'kind': 'issue_guide', 'version': 'issue-guide-3-ranked-candidates', 'state': 'running',
                  'stage': 'planning', 'created_at': now(), 'project_id': project['id'], 'project_name': project['name'],
                  'project_revision': project['revision'], 'issue_id': issue['id'], 'issue_title': issue['title'],
                  'model': self.model, 'ingredient': project.get('ingredient'),
                  'scope': {'products': [p for p in products if p not in excluded], 'excluded_products': excluded},
                  'facts': facts(project, issue), 'questions': [], 'answers': [], 'usage': []}
        # Held-out names never go out in a question, whether or not the project lists them as blocked terms.
        blocked = list(dict.fromkeys([*excluded, *((project.get('simulation') or {}).get('blocked_terms') or [])]))
        try:
            with self.lock:
                self.records[record['id']] = record
            self.persist(record)
            self.executor.submit(self.run, record['id'], deepcopy(issue), blocked)
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

    def call(self, client, guide_id, case, prompt, contract):
        if hasattr(client, 'case'):
            client.case = case
        response = client(prompt, contract)
        with self.lock:
            usage = self.records[guide_id]['usage'] + [{'step': case, 'input_tokens': response.get('prompt_tokens', 0),
                                                        'output_tokens': response.get('completion_tokens', 0)}]
        self.update(guide_id, usage=usage, model_reported=response.get('model_reported'))
        return json.loads(response['text'])

    def search(self, guide_id, questions, answers, scope):
        """Run questions in order, recording each; a cited held-out product stops the guide."""
        with self.lock:
            rows = deepcopy(self.records[guide_id]['questions'])
        for question in questions:
            index = next(i for i, q in enumerate(rows) if q['id'] == question['id'])
            rows[index]['state'] = 'running'
            self.update(guide_id, questions=deepcopy(rows))
            job = self.ask(question['text'], scope['products'])
            answer = answer_view(question, job)
            leaked = sorted({r['product'] for r in answer['evidence'].values()} & set(scope['excluded_products']))
            if leaked:
                raise RuntimeError('검색에서 뺀 제품의 문서가 인용됐습니다: ' + ', '.join(leaked))
            rows[index].update(state=job['state'], job_id=job['id'], status=answer['status'])
            answers.append(answer)
            self.update(guide_id, questions=deepcopy(rows), answers=deepcopy(answers))

    def run(self, guide_id, issue, blocked):
        client = None
        try:
            with self.lock:
                record = deepcopy(self.records[guide_id])
            ingredient, fact_rows, scope = record['ingredient'], record['facts'], record['scope']
            client = self.client(self.directory / (guide_id + '_api'))
            plan = self.call(client, guide_id, 'issue_search_plan', plan_messages(issue, fact_rows, ingredient), plan_schema())
            hypotheses = [{'attribute': str(h.get('attribute', '')).strip(), 'reason': str(h.get('reason', '')).strip()}
                          for h in plan.get('hypotheses') or [] if isinstance(h, dict)][:5]
            first, dropped = clean_questions(plan.get('questions') or [], blocked, 1, 1)
            if not first:
                raise RuntimeError('검색 질문을 만들지 못했습니다.')
            self.update(guide_id, stage='searching', plan={'hypotheses': hypotheses, 'dropped': dropped}, questions=first)
            answers = []
            self.search(guide_id, first, answers, scope)
            self.update(guide_id, stage='following_up')
            more = self.call(client, guide_id, 'issue_search_followup',
                             followup_messages(issue, fact_rows, ingredient, hypotheses, answers), followup_schema())
            second, dropped_more = clean_questions((more.get('questions') or [])[:FOLLOWUP_QUESTIONS], blocked, len(first) + 1, 2)
            if second:
                with self.lock:
                    asked = deepcopy(self.records[guide_id]['questions'])  # first round with its final states
                self.update(guide_id, stage='searching', questions=asked + second,
                            plan={'hypotheses': hypotheses, 'dropped': dropped + dropped_more})
                self.search(guide_id, second, answers, scope)
            sources, claim_ids = public_sources(answers)
            if not sources:
                raise RuntimeError('공개 자료에서 인용할 근거를 찾지 못했습니다.')
            self.update(guide_id, stage='writing', sources=sources)
            ids = [s['id'] for s in sources] + [f['id'] for f in fact_rows]
            guide = normalize(self.call(client, guide_id, 'issue_guide',
                                        messages(issue, fact_rows, ingredient, hypotheses, answers, sources, claim_ids), schema(ids)), ids)
            self.update(guide_id, state='completed', stage='completed', guide=guide,
                        warnings=audit(guide, sources, scope['excluded_products']), completed_at=now())
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
        result['searches'] = len(record.get('questions', []))
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

    def delete(self, project_id, guide_id):
        """Remove a finished guide and its model-call records; a guide still being made stays."""
        record = self.get(project_id, guide_id)
        with self.lock:
            if guide_id in self.records and self.records[guide_id]['state'] == 'running':
                raise ValueError('만드는 중인 가이드는 지울 수 없습니다.')
            self.records.pop(guide_id, None)
        (self.directory / (guide_id + '.json')).unlink(missing_ok=True)
        shutil.rmtree(self.directory / (guide_id + '_api'), ignore_errors=True)
        return {'deleted': record['id']}

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
