"""A pipeline's own internal documents: registered once, never edited, read as of a date.

Each document version is immutable. A later version is a new entry; which version applies on a date is decided from the
document status (an approved version applies until a later approved one; drafts never apply). Units carry the work
events that memo_workflow turns into the state board, so state is computed from sources rather than written by anyone.
"""
from copy import deepcopy
import hashlib
import json
import re

from rag_service import memo_workflow

KINDS = {'protocol': '계획서', 'report': '시험 보고서', 'email': '메일', 'minutes': '회의록', 'quality_record': 'QA 기록',
         'memo': '내부 메모', 'mock_review': '모의 심사 질의', 'draft_response': '답변 초안'}
STATUS = {'approved': '승인본', 'draft': '초안', 'issued': '발송', 'preliminary': '잠정', 'final': '최종본'}
SHARING = {'internal': '사내', 'partner': '협력사 공유'}
ROLE_LABELS = {'experiment_result': '시험 결과', 'analyst_opinion': '분석자 의견', 'partner_response': '협력사 회신',
               'partner_opinion': '협력사 의견·제안', 'internal_opinion': '사내 의견', 'protocol': '계획서 조항',
               'guideline': '공개 지침', 'simulated_agency_request': '모의 질의', 'sponsor_draft': '답변 초안 문구',
               'work_request': '업무 요청', 'work_record': '업무 기록(발주·발송)', 'meeting_record': '회의록 기록',
               'quality_decision': 'QA 판단', 'submission_record': '제출 기록', 'agency_decision': '기관 판단'}
UNIT_ID = re.compile(r'[A-Za-z0-9_.-]{1,40}')
MAX_DOCUMENTS = 500


def _text(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise ValueError(f'{name}을(를) {limit:,}자 이내로 입력하세요.')
    return value.strip()


def _choice(value, options, name):
    if value not in options:
        raise ValueError(f'{name}을(를) 확인하세요: {value}')
    return value


def content_hash(raw):
    """Identity of a document as submitted (before event ordering), so an identical re-import is recognized."""
    return hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize_bundle(bundle, known_scopes=None):
    """Validate an import bundle; return (documents, scopes, simulation). Nothing is stored here."""
    allowed = {'schema_version', 'scenario_id', 'product_alias', 'ingredient', 'disclosure', 'blocked_terms', 'scopes',
               'documents', 'checkpoints'}
    if not isinstance(bundle, dict) or set(bundle) - allowed or bundle.get('schema_version') != 'internal_document_bundle_v1':
        raise ValueError('내부 문서 묶음 형식(internal_document_bundle_v1)을 확인하세요.')
    scopes = deepcopy(known_scopes or {})
    for scope_id, scope in (bundle.get('scopes') or {}).items():
        if not isinstance(scope, dict) or set(scope) - {'label', 'aliases'}:
            raise ValueError('업무 범위 정의를 확인하세요.')
        label = _text(scope.get('label'), '업무 이름', 120)
        aliases = [_text(a, '업무 별칭', 60) for a in scope.get('aliases', [])]
        if scope_id in scopes and scopes[scope_id]['label'] != label:
            raise ValueError(f'이미 등록된 업무 {scope_id}의 이름과 다릅니다.')
        scopes[_text(scope_id, '업무 ID', 60)] = {'label': label, 'aliases': list(dict.fromkeys(aliases))}
    blocked = [_text(t, '차단어', 80) for t in bundle.get('blocked_terms', [])]
    documents = []
    for raw in bundle.get('documents') or []:
        keys = {'document_id', 'version', 'date', 'title', 'kind', 'status', 'author', 'sharing', 'units'}
        if not isinstance(raw, dict) or set(raw) != keys:
            raise ValueError('문서 필드(ID·버전·일자·제목·종류·상태·작성자·공유 범위·본문 단위)를 확인하세요.')
        document = {'document_id': _text(raw['document_id'], '문서 ID', 80), 'version': _text(raw['version'], '버전', 40),
                    'date': memo_workflow.day(raw['date']), 'title': _text(raw['title'], '문서 제목', 200),
                    'kind': _choice(raw['kind'], KINDS, '문서 종류'), 'status': _choice(raw['status'], STATUS, '문서 상태'),
                    'author': _text(raw['author'], '작성자', 120), 'sharing': _choice(raw['sharing'], SHARING, '공유 범위'),
                    'content_sha256': content_hash(raw), 'units': []}
        if not isinstance(raw['units'], list) or not 1 <= len(raw['units']) <= 40:
            raise ValueError('문서마다 본문 단위를 1~40개 넣으세요.')
        for unit in raw['units']:
            ukeys = {'unit_id', 'section', 'statement_role', 'speaker', 'scope_ids', 'events', 'text'}
            if not isinstance(unit, dict) or set(unit) != ukeys:
                raise ValueError('본문 단위 필드를 확인하세요.')
            unit_id = unit['unit_id']
            if not isinstance(unit_id, str) or not UNIT_ID.fullmatch(unit_id):
                raise ValueError('본문 단위 ID를 확인하세요.')
            text = _text(unit['text'], '본문', 4000)
            if any(term.casefold() in (text + document['title']).casefold() for term in blocked):
                raise ValueError(f'{unit_id}에 이 프로젝트에서 차단한 표현이 있습니다.')
            for scope_id in unit['scope_ids'] if isinstance(unit['scope_ids'], list) else []:
                if scope_id not in scopes:
                    raise ValueError(f'{unit_id}: 정의되지 않은 업무 {scope_id}')
            events = []
            for index, event in enumerate(unit['events'] if isinstance(unit['events'], list) else [None]):
                if not isinstance(event, dict) or set(event) - {'scope', 'axis', 'value', 'date'} or not {'scope', 'axis', 'value'} <= set(event):
                    raise ValueError(f'{unit_id}: 업무 이벤트 필드를 확인하세요.')
                if event['scope'] not in scopes:
                    raise ValueError(f'{unit_id}: 정의되지 않은 업무 {event["scope"]}')
                # The order inside a date is assigned when the document is stored (registration sequence).
                events.append({'id': f'{unit_id}:{index}', 'scope_id': event['scope'], 'scope_label': scopes[event['scope']]['label'],
                               'axis': event['axis'], 'value': event['value'], 'order': 0,
                               **({'date': event['date']} if 'date' in event else {})})
            context = memo_workflow.validate_context({
                'unit_id': unit_id, 'available_from': document['date'],
                'source': {'document_id': document['document_id'], 'version': document['version'], 'section': _text(unit['section'], '절', 120),
                           'source_kind': document['kind'], 'title': document['title'], 'status': document['status'],
                           'sharing': document['sharing'], 'date': document['date'], 'author': document['author']},
                'statement_role': unit['statement_role'], 'speaker': unit['speaker'], 'scope_ids': unit['scope_ids'], 'events': events})
            document['units'].append({'unit_id': unit_id, 'text': text, 'context': context})
        documents.append(document)
    if not documents:
        raise ValueError('등록할 문서가 없습니다.')
    simulation = None
    if bundle.get('checkpoints') or blocked or bundle.get('disclosure'):
        checkpoints = []
        for point in bundle.get('checkpoints') or []:
            if not isinstance(point, dict) or set(point) != {'id', 'as_of', 'task', 'audience', 'label', 'question'}:
                raise ValueError('시나리오 기준일 정의를 확인하세요.')
            checkpoints.append({'id': _text(point['id'], '기준일 ID', 20), 'as_of': memo_workflow.day(point['as_of']),
                                'task': _text(point['task'], '업무', 40), 'audience': _choice(point['audience'], SHARING, '대상'),
                                'label': _text(point['label'], '기준일 이름', 80), 'question': _text(point['question'], '질문', 2000)})
        simulation = {'scenario_id': _text(bundle.get('scenario_id', 'scenario'), '시나리오 ID', 80),
                      'disclosure': _text(bundle.get('disclosure', '합성 시나리오 자료'), '안내', 2000),
                      'blocked_terms': blocked, 'checkpoints': checkpoints}
    return documents, scopes, simulation


def merge(project, documents, scopes, simulation):
    """Append new document versions to a project record in place; identical re-imports are skipped."""
    stored = project.setdefault('documents', [])
    by_key = {(d['document_id'], d['version']): d for d in stored}
    units = {u['unit_id'] for d in stored for u in d['units']}
    order = max((e['order'] for d in stored for u in d['units'] for e in u['context']['events']), default=0)
    added, skipped = [], []
    for document in documents:
        key = (document['document_id'], document['version'])
        if key in by_key:
            if by_key[key]['content_sha256'] != document['content_sha256']:
                raise ValueError(f'{key[0]} v{key[1]}은(는) 다른 내용으로 이미 등록됐습니다. 새 버전으로 등록하세요.')
            skipped.append(key)
            continue
        clash = {u['unit_id'] for u in document['units']} & units
        if clash:
            raise ValueError('이미 쓰인 본문 단위 ID입니다: ' + ', '.join(sorted(clash)))
        units |= {u['unit_id'] for u in document['units']}
        document = deepcopy(document)
        for unit in document['units']:
            for event in unit['context']['events']:
                order += 1
                event['order'] = order
        stored.append(document)
        by_key[key] = document
        added.append(key)
    if len(stored) > MAX_DOCUMENTS:
        raise ValueError(f'파이프라인당 내부 문서는 {MAX_DOCUMENTS}건까지 등록할 수 있습니다.')
    project['work_scopes'] = scopes
    if simulation:
        project['simulation'] = simulation
    return {'added': [list(k) for k in added], 'skipped': [list(k) for k in skipped]}


def versions_as_of(project, as_of):
    """Per document ID, which version applies on the date and which are drafts or superseded."""
    as_of = memo_workflow.day(as_of)
    groups = {}
    for document in project.get('documents', []):
        if document['date'] <= as_of:
            groups.setdefault(document['document_id'], []).append(document)
    standing = {}
    for document_id, rows in groups.items():
        rows = sorted(rows, key=lambda d: (d['date'], d['version']))
        approved = [d for d in rows if d['status'] == 'approved']
        current = approved[-1] if approved else None
        for d in rows:
            if d['kind'] != 'protocol':
                state = 'current'
            elif d is current:
                state = 'applicable'
            elif d['status'] == 'approved':
                state = 'superseded'
            else:
                state = 'unapproved_revision' if current and d['date'] >= current['date'] else 'draft'
            standing[(document_id, d['version'])] = state
    return standing


STANDING_LABELS = {'current': '', 'applicable': '현재 기준', 'superseded': '이전 기준(대체됨)',
                   'unapproved_revision': '미승인 개정안(적용 안 됨)', 'draft': '미승인 초안'}


def units_as_of(project, as_of, audience='internal'):
    """Every unit available on the date (and shareable with the audience), with its document's metadata."""
    as_of = memo_workflow.day(as_of)
    _choice(audience, SHARING, '대상')
    standing = versions_as_of(project, as_of)
    rows = []
    for document in project.get('documents', []):
        if document['date'] > as_of or (audience == 'partner' and document['sharing'] != 'partner'):
            continue
        state = standing[(document['document_id'], document['version'])]
        for unit in document['units']:
            rows.append({'unit_id': unit['unit_id'], 'text': unit['text'], 'context': deepcopy(unit['context']),
                         'document_id': document['document_id'], 'version': document['version'], 'title': document['title'],
                         'date': document['date'], 'kind': document['kind'], 'status': document['status'],
                         'author': document['author'], 'sharing': document['sharing'], 'standing': state,
                         'section': unit['context']['source']['section'], 'statement_role': unit['context']['statement_role'],
                         'role_label': ROLE_LABELS[unit['context']['statement_role']],
                         'speaker': unit['context']['speaker'], 'scope_ids': unit['context']['scope_ids']})
    rows.sort(key=lambda r: (r['date'], r['unit_id']))
    return rows


def hidden_after(project, as_of):
    as_of = memo_workflow.day(as_of)
    return sum(1 for d in project.get('documents', []) if d['date'] > as_of)


def board(project, as_of, audience='internal'):
    """The work-state board on a date: computed from the source events, never edited directly."""
    units = units_as_of(project, as_of, audience)
    scopes = memo_workflow.project([{'context': u['context']} for u in units], as_of)
    return memo_workflow.compact(scopes)


DOCUMENT_AXES = ('approval', 'submission', 'agency_decision')
AXIS_ORDER = ('execution', 'scope_agreement', 'schedule', 'report', 'technical_review', 'approval', 'qa_approval', 'submission', 'agency_decision')
CONTENT_ROLES = {'protocol', 'sponsor_draft', 'simulated_agency_request'}
STEP = {'requested': '요청', 'planned': '계획', 'in_progress': '진행 중', 'result_reported': '결과 받음', 'not_performed': '미실시'}


def _md(iso):
    return iso[5:].replace('-', '/')


def _status(axes):
    """One status per work item: the approval chain for documents and answers, otherwise how far the work got."""
    if any(a in axes for a in DOCUMENT_AXES):
        for axis, value, label, tone in (('agency_decision', 'accepted', '기관 수용', 'done'), ('submission', 'submitted', '제출', 'done'),
                                         ('qa_approval', 'not_approved', 'QA 미승인', 'stop'), ('approval', 'approved', '승인', 'done'),
                                         ('approval', 'draft', '초안', 'open')):
            if axes.get(axis, {}).get('value') == value:
                return {'axis': axis, 'label': label, 'tone': tone}
    execution = axes.get('execution')
    if execution:
        value = execution['value']
        if value == 'result_reported' and ((axes.get('qa_approval') or {}).get('value') == 'approved' or (axes.get('report') or {}).get('value') == 'final'):
            return {'axis': 'execution', 'label': '완료', 'tone': 'done'}
        return {'axis': 'execution', 'label': STEP[value], 'tone': {'result_reported': 'done', 'not_performed': 'stop'}.get(value, 'open')}
    axis = next(a for a in AXIS_ORDER if a in axes)
    return {'axis': axis, 'label': axes[axis]['label'], 'tone': 'open'}


def _waiting(axes, as_of):
    """What the work item waits for next, only from recorded states and dates (nothing is predicted)."""
    execution = (axes.get('execution') or {}).get('value')
    agreement = (axes.get('scope_agreement') or {}).get('value')
    schedule, report = axes.get('schedule'), (axes.get('report') or {}).get('value')
    if agreement == 'awaiting_response':
        return '협력사 답변 대기'
    if execution in ('requested', 'planned', 'in_progress') and schedule:
        text = f"결과 예정 {_md(schedule['date'])}({'서면' if schedule['value'] == 'written_due' else '구두'})"
        previous = (schedule.get('previous') or {}).get('date')
        return text + (f' · {_md(previous)}에서 변경' if previous else '')
    if agreement == 'proposed':
        return '견적·발주 대기'
    if execution == 'result_reported' and report == 'draft':
        if schedule and schedule['date'] > as_of:
            return f"최종 보고서 예정 {_md(schedule['date'])}"
        return '정식 보고서 여부 미정' if agreement == 'informal' else '최종 보고서 대기'
    return None


def _phrase(event):
    label = memo_workflow.AXES[event['axis']][event['value']]
    if event['axis'] == 'schedule':
        return f"{label} {_md(event['date'])}"
    if event['axis'] == 'report':
        return f'보고서 {label}'
    return label


def work_items(project, as_of, audience='internal'):
    """The work list on a date: per work item one status, a one-line summary, what it waits for, its dated history,
    its result texts and the opinions recorded about it. Everything is read from the documents' records."""
    units = units_as_of(project, as_of, audience)
    by_id = {u['unit_id']: u for u in units}
    scopes = memo_workflow.project([{'context': u['context']} for u in units], as_of)
    order = [s['scope_id'] for s in memo_workflow.compact(scopes)]
    by_scope = {s['scope_id']: s for s in scopes}
    items = []
    for scope_id in order:
        scope = by_scope[scope_id]
        axes = scope['axes']
        status = _status(axes)
        document = any(a in axes for a in DOCUMENT_AXES)
        done = (axes.get('execution') or {}).get('value') == 'result_reported'
        # The summary line repeats neither the status, nor a date already shown as "waiting", nor a past due date.
        shown = [a for a in AXIS_ORDER if a in axes and a != status['axis']
                 and not (a == 'schedule' and (done or _waiting(axes, as_of)))
                 and not (document and a not in (*DOCUMENT_AXES, 'qa_approval'))]
        now = ' · '.join(('보고서 ' if a == 'report' else '') + axes[a]['label'] + (f" {_md(axes[a]['date'])}" if axes[a].get('date') else '')
                         for a in shown)
        events, last = [], {}
        for event in scope['history']:
            unit = by_id[event['unit_id']]
            # A record that only restates the current value stays in the history but is not progress.
            changed = last.get(event['axis']) != (event['value'], event.get('date'))
            last[event['axis']] = (event['value'], event.get('date'))
            if events and events[-1]['unit_id'] == event['unit_id']:
                if _phrase(event) not in events[-1]['phrases']:
                    events[-1]['phrases'].append(_phrase(event))
                events[-1]['changed'] = events[-1]['changed'] or changed
                continue
            events.append({'date': event['available'], 'unit_id': event['unit_id'], 'phrases': [_phrase(event)],
                           'who': unit['speaker'], 'source': KINDS[unit['kind']], 'changed': changed})
        related = [u for u in units if scope_id in u['scope_ids']]
        # A test's results; for a document or answer, its own text (not the criteria that currently apply).
        results = [u for u in related if u['statement_role'] == 'experiment_result'] if not document else \
            [u for u in related if u['statement_role'] in CONTENT_ROLES and u['standing'] not in ('applicable', 'superseded')]
        opinions = [u for u in related if u['statement_role'] in memo_workflow.OPINION_ROLES]
        pick = lambda u: {'unit_id': u['unit_id'], 'date': u['date'], 'title': u['title'], 'speaker': u['speaker'], 'text': u['text']}
        items.append({'scope_id': scope_id, 'label': scope['label'], 'kind': 'document' if document else 'work',
                      'status': {k: status[k] for k in ('label', 'tone')}, 'now': now, 'waiting': _waiting(axes, as_of),
                      'first_date': events[0]['date'], 'last_change': max(e['date'] for e in events if e['changed']), 'events': events,
                      'results': [pick(u) for u in results], 'opinions': [pick(u) for u in opinions]})
    return items


def catalog(project, as_of=None):
    """Documents for the screen (newest first), optionally only those available on a date."""
    standing = versions_as_of(project, as_of) if as_of else versions_as_of(project, '9999-12-31')
    rows = []
    for document in project.get('documents', []):
        if as_of and document['date'] > as_of:
            continue
        state = standing.get((document['document_id'], document['version']), 'current')
        rows.append({**{k: deepcopy(document[k]) for k in ('document_id', 'version', 'date', 'title', 'kind', 'status', 'author', 'sharing')},
                     'kind_label': KINDS[document['kind']], 'status_label': STATUS[document['status']],
                     'sharing_label': SHARING[document['sharing']], 'standing': state, 'standing_label': STANDING_LABELS[state],
                     'units': [{'unit_id': u['unit_id'], 'text': u['text'], 'section': u['context']['source']['section'],
                                'statement_role': u['context']['statement_role'], 'role_label': ROLE_LABELS[u['context']['statement_role']],
                                'speaker': u['context']['speaker'], 'scope_ids': u['context']['scope_ids']} for u in document['units']]})
    rows.sort(key=lambda r: (r['date'], r['document_id'], r['version']), reverse=True)
    return rows
