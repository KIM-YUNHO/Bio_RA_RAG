"""Source-bound work events. Receiving a result does not approve an experiment or submission."""
from copy import deepcopy
from datetime import date

ROLES = {'experiment_result', 'analyst_opinion', 'partner_response', 'internal_opinion', 'protocol',
         'guideline', 'simulated_agency_request', 'sponsor_draft', 'work_request',
         'quality_decision', 'submission_record', 'agency_decision',
         'partner_opinion', 'meeting_record', 'work_record'}
# Opinions are quoted with their speaker; they never move a work state (an analyst may only record a technical review).
OPINION_ROLES = {'analyst_opinion', 'internal_opinion', 'partner_opinion'}
AGREEMENT_ROLES = {'work_record', 'partner_response', 'meeting_record'}
# Plain labels shown on the screen and given to the model. "기록 없음" means no record, not that nothing happened.
AXES = {
    'execution': {'requested': '요청함', 'planned': '계획됨', 'in_progress': '진행 중',
                  'result_reported': '결과 받음', 'not_performed': '미실시'},
    'scope_agreement': {'awaiting_response': '답변 대기', 'informal': '구두로 진행(조건 미정)',
                        'proposed': '협의 중(발주 전)', 'agreed': '합의 완료(발주·서면)',
                        'not_agreed': '합의 안 됨', 'not_recorded': '합의 기록 없음'},
    'schedule': {'verbal_estimate': '구두 예상', 'written_due': '서면 예정'},
    'report': {'draft': '초안', 'final': '최종본'},
    'technical_review': {'pending': '검토 중', 'analyst_comment_recorded': '담당자 의견 있음', 'completed': '검토 완료'},
    'qa_approval': {'not_recorded': 'QA 기록 없음', 'approved': 'QA 승인', 'not_approved': 'QA 미승인'},
    'approval': {'draft': '초안(미승인)', 'approved': '승인됨'},
    'submission': {'not_submitted': '미제출', 'submitted': '제출함'},
    'agency_decision': {'no_actual_agency_process': '모의 질의(실제 아님)', 'pending': '기관 검토 중', 'accepted': '기관 수용'},
}
DATED_AXES = {'schedule'}
SOURCE_KEYS = {'document_id', 'version', 'section', 'source_kind'}
SOURCE_OPTIONAL = {'title', 'status', 'sharing', 'date', 'author'}


def _text(value, limit=256):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise ValueError('문서·발언 주체·업무 식별자를 확인하세요.')
    return value.strip()


def day(value):
    """An ISO calendar date (YYYY-MM-DD); returns the same string."""
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError('날짜는 YYYY-MM-DD로 입력하세요.')
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('날짜는 YYYY-MM-DD로 입력하세요.') from exc
    return value


def moment(value):
    """A point on the scenario clock: a relative stage (1, 2, ...) or a calendar date."""
    if type(value) is int:
        if value < 1:
            raise ValueError('자료 공급 시점을 확인하세요.')
        return value
    return day(value)


def comparable(a, b):
    if type(a) is not type(b):
        raise ValueError('단계 번호와 날짜 기준을 섞을 수 없습니다.')
    return a <= b


def validate_context(value):
    """Trusted importer input, never an editable memo annotation or an LLM-produced status."""
    required = {'unit_id', 'source', 'available_from', 'statement_role', 'speaker', 'scope_ids', 'events'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('정보 단위의 원문·주체·업무 문맥을 확인하세요.')
    context = deepcopy(value)
    context['unit_id'] = _text(context['unit_id'])
    context['available_from'] = moment(context['available_from'])
    if not isinstance(context['statement_role'], str) or context['statement_role'] not in ROLES:
        raise ValueError('원문 발언 역할을 확인하세요.')
    context['speaker'] = _text(context['speaker'], 120)
    source = context['source']
    if not isinstance(source, dict) or not SOURCE_KEYS <= set(source) or set(source) - SOURCE_KEYS - SOURCE_OPTIONAL:
        raise ValueError('문서 버전과 절을 확인하세요.')
    context['source'] = {k: _text(v, 300) for k, v in source.items()}
    scopes = context['scope_ids']
    if not isinstance(scopes, list) or len(scopes) > 16:
        raise ValueError('업무 범위 목록을 확인하세요.')
    context['scope_ids'] = [_text(s) for s in scopes]
    if len(set(context['scope_ids'])) != len(scopes):
        raise ValueError('업무 범위가 중복됩니다.')
    if not isinstance(context['events'], list) or len(context['events']) > 32:
        raise ValueError('원문 업무 이벤트 목록을 확인하세요.')
    seen = set()
    role = context['statement_role']
    for event in context['events']:
        keys = {'id', 'scope_id', 'scope_label', 'axis', 'value', 'order'}
        if not isinstance(event, dict) or not keys <= set(event) or set(event) - keys - {'date'}:
            raise ValueError('업무 이벤트 필드를 확인하세요.')
        for key in ('id', 'scope_id', 'scope_label', 'axis', 'value'):
            event[key] = _text(event[key])
        if event['id'] in seen or event['scope_id'] not in context['scope_ids']:
            raise ValueError('이벤트 식별자와 적용 업무를 확인하세요.')
        seen.add(event['id'])
        if event['axis'] not in AXES or event['value'] not in AXES[event['axis']]:
            raise ValueError('업무 상태의 축과 값을 확인하세요.')
        if (event['axis'] in DATED_AXES) != ('date' in event):
            raise ValueError('일정 이벤트에만 날짜를 기록하세요.')
        if 'date' in event:
            event['date'] = day(event['date'])
        if role == 'analyst_opinion' and event['axis'] != 'technical_review':
            raise ValueError('분석자 의견은 기술 검토 의견으로만 기록하세요.')
        if role in OPINION_ROLES - {'analyst_opinion'}:
            raise ValueError('의견은 업무 상태를 바꾸지 않습니다. 발언 주체와 함께 인용하세요.')
        if event['axis'] == 'scope_agreement' and event['value'] == 'agreed' and role not in AGREEMENT_ROLES:
            raise ValueError('합의 기록은 발주·회신·회의록 원문이 필요합니다.')
        if event['axis'] == 'qa_approval' and event['value'] == 'approved' and role != 'quality_decision':
            raise ValueError('QA 승인 원문이 필요합니다.')
        if event['axis'] == 'submission' and event['value'] == 'submitted' and role != 'submission_record':
            raise ValueError('제출 증빙 원문이 필요합니다.')
        if event['axis'] == 'agency_decision' and event['value'] == 'accepted' and role != 'agency_decision':
            raise ValueError('기관 판단 원문이 필요합니다.')
        if type(event['order']) is not int or event['order'] < 0:
            raise ValueError('동일 시점 내 이벤트 순서를 확인하세요.')
    return context


def _state(event):
    state = {'value': event['value'], 'label': AXES[event['axis']][event['value']],
             'source_unit_ids': [event['unit_id']], 'speaker': event['speaker'], 'at': [event['available'], event['order']]}
    if 'date' in event:
        state['date'] = event['date']
    return state


def project(items, as_of):
    """Resolve only the named scope/axis as of a stage or date, retain history, refuse conflicting simultaneous facts."""
    as_of = moment(as_of)
    events = []
    seen = set()
    for item in items:
        raw = item.get('context')
        if not raw:
            continue
        context = validate_context(raw)
        if not comparable(context['available_from'], as_of):
            continue
        for event in context['events']:
            if event['id'] in seen:
                raise ValueError('중복된 업무 이벤트입니다.')
            seen.add(event['id'])
            events.append({**event, 'phase': context['available_from'], 'available': context['available_from'],
                           'unit_id': context['unit_id'], 'speaker': context['speaker'],
                           'statement_role': context['statement_role']})
    events.sort(key=lambda e: (e['available'], e['order'], e['id']))
    scopes = {}
    for event in events:
        scope = scopes.setdefault(event['scope_id'], {'scope_id': event['scope_id'], 'label': event['scope_label'], 'axes': {}, 'history': [],
                                                      'first_at': [event['available'], event['order']]})
        if scope['label'] != event['scope_label']:
            raise ValueError('동일 업무의 이름이 일치하지 않습니다.')
        old = scope['axes'].get(event['axis'])
        stamp = [event['available'], event['order']]
        if old and old['at'] == stamp and (old['value'], old.get('date')) != (event['value'], event.get('date')):
            raise ValueError('동시점의 업무 상태가 충돌합니다. 원문 확인이 필요합니다.')
        state = _state(event)
        if old and (old['value'], old.get('date')) == (state['value'], state.get('date')):
            # A later record confirming the same state is more evidence, not a change: keep when the state began.
            state['since'] = old.get('since', old['at'])
            state['source_unit_ids'] = list(dict.fromkeys([*old['source_unit_ids'], *state['source_unit_ids']]))
            if 'previous' in old:
                state['previous'] = deepcopy(old['previous'])
        else:
            state['since'] = stamp
            if old:
                state['previous'] = {k: deepcopy(old[k]) for k in ('value', 'label', 'source_unit_ids', 'date') if k in old}
        scope['axes'][event['axis']] = state
        scope['history'].append(event)
    return [scopes[key] for key in sorted(scopes)]


def compact(scopes):
    """Scopes without event history, in order of their first recorded event (when the work started)."""
    rows = [{k: deepcopy(scope[k]) for k in ('scope_id', 'label', 'axes', 'first_at') if k in scope} for scope in scopes]
    return sorted(rows, key=lambda s: (s.get('first_at') or [0, 0], s['scope_id']))
