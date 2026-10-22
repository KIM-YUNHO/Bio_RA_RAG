"""Source-bound memo items. Classification never upgrades a model/source into experimental proof."""
from copy import deepcopy
import hashlib

TYPES = {'unclassified': '미분류', 'fact': '사실·결과', 'criterion': '적용 기준',
         'interpretation': '의견·해석', 'decision': '결정', 'action': '후속 조치', 'question': '미해결 질문'}
MODALITIES = {'unclassified': '미분류', 'reported': '원문 보고', 'observed': '관측',
              'predicted': '예상', 'planned': '계획', 'proposal': '제안', 'not_disclosed': '미공개'}
MATURITY = {'not_disclosed': '미공개', 'preliminary': '잠정', 'final': '확정', 'not_applicable': '해당 없음'}
VALIDITY = {'not_disclosed': '미공개', 'pending': '확인 대기', 'valid': '유효', 'invalid': '무효', 'not_applicable': '해당 없음'}
TASKS = {'result_review': ('결과 검토', ['fact', 'criterion', 'interpretation', 'question', 'decision', 'action']),
         'partner_meeting': ('협력사 미팅 준비', ['decision', 'action', 'question', 'fact', 'interpretation', 'criterion']),
         'ra_preparation': ('기관 질문 준비', ['question', 'fact', 'criterion', 'interpretation', 'decision', 'action'])}
EDITABLE = {'type', 'subject', 'modality', 'data_maturity', 'run_validity', 'note'}


def definitions():
    return {'types': TYPES, 'modalities': MODALITIES, 'data_maturity': MATURITY, 'run_validity': VALIDITY,
            'tasks': {k: v[0] for k, v in TASKS.items()}}


def build(memo_id, body, source, context=None):
    if context is not None:
        if source:
            raise ValueError('보고서 인용과 내부 문서 문맥을 혼합할 수 없습니다.')
        from rag_service.workspace.memo_workflow import validate_context
        context = validate_context(context)
    claims = source.get('claims', []) if source else []
    if source and not claims:
        return []  # Legacy/evidence-only text is not silently promoted to a classified claim.
    rows = claims if source else [{'id': 'manual', 'text': body, 'kind': 'user_note', 'sources': []}]
    items = []
    for claim in rows:
        key = claim['id']
        quotes = deepcopy(claim.get('sources') or [])
        evidence_ids = list(dict.fromkeys(q.get('evidence') for q in quotes if q.get('evidence')))
        items.append({'id': hashlib.sha256((memo_id + ':' + key).encode()).hexdigest()[:32], 'key': key,
            'text': claim['text'], 'type': 'interpretation' if claim.get('kind') == 'interpretation' else 'unclassified',
            'subject': '', 'modality': 'unclassified', 'data_maturity': 'not_disclosed', 'run_validity': 'not_disclosed',
            'note': '', 'review_status': 'unreviewed', 'statement_role': claim.get('kind', 'unclassified'),
            'claim_basis': deepcopy(claim.get('claim_basis')), 'task_ids': deepcopy(claim.get('task_ids') or []),
            'origin': ('source_replay' if source.get('report_origin') == 'public_source_replay' else 'rag_claim') if source else 'user_note', 'source_quotes': quotes,
            'evidence': [deepcopy(e) for e in (source or {}).get('evidence', []) if e['id'] in evidence_ids],
            'model_support_review': deepcopy(claim.get('support_review')),
            'source_locator': {k: deepcopy(source[k]) for k in ('job_id', 'result_index', 'report_sha256') if source and k in source},
            'claim_id': key if source else None})
    if context is not None:
        items[0].update(context=deepcopy(context), statement_role=context['statement_role'], origin='internal_document')
    return items


def annotate(items, payload):
    """Allow metadata/notes only; source text, quotes, identity and model review are server-owned."""
    if not isinstance(payload, list) or len(payload) > 64:
        raise ValueError('항목 분류는 목록으로 보내세요(최대 64개).')
    result = deepcopy(items)
    by_key = {item['key']: item for item in result}
    seen = set()
    enums = {'type': TYPES, 'modality': MODALITIES, 'data_maturity': MATURITY, 'run_validity': VALIDITY}
    for row in payload:
        if not isinstance(row, dict) or 'key' not in row or set(row) - ({'key'} | EDITABLE):
            raise ValueError('수정할 항목과 허용된 분류 필드를 확인하세요.')
        key = row['key']
        if not isinstance(key, str) or key not in by_key or key in seen:
            raise ValueError('알 수 없거나 중복된 항목입니다.')
        seen.add(key)
        item = by_key[key]
        for field, value in row.items():
            if field == 'key':
                continue
            if field in enums:
                if not isinstance(value, str) or value not in enums[field]:
                    raise ValueError('항목의 유형·자료 상태를 확인하세요.')
            elif not isinstance(value, str) or len(value) > (120 if field == 'subject' else 2000) or '\x00' in value:
                raise ValueError('대상은 120자, 추가 의견은 2,000자 이내로 입력하세요.')
            else:
                value = value.strip()
            item[field] = value
        if len(row) > 1:
            item['annotation_basis'] = 'user_entered_unverified'
    return result


def brief(project, task):
    if task not in TASKS:
        raise ValueError('지원하지 않는 업무 보기입니다.')
    title, order = TASKS[task]
    rows = []
    for memo in project.get('memos', []):
        if memo.get('archived'):
            continue
        for item in memo.get('items', []):
            rows.append({**deepcopy(item), 'memo_id': memo['id'], 'stage_ids': memo['stage_ids'],
                         'memo_revision': memo['revision']})
    return {'project_id': project['id'], 'project_revision': project['revision'], 'task': task, 'title': title,
        'draft_only': True, 'audience': 'internal', 'generated_by_model': False,
        'description': '저장한 항목을 유형별로 모은 내부 검토용 보기입니다. 외부 공유본이나 기관 제출본이 아닙니다.',
        'unstructured_memo_count': sum(not m.get('archived') and not m.get('items') for m in project.get('memos', [])),
        'groups': [{'type': kind, 'label': TYPES[kind], 'items': [r for r in rows if r['type'] == kind]}
                   for kind in [*order, 'unclassified']]}
