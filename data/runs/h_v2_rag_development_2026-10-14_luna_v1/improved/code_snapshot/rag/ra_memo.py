"""Traceable issue memos from checked claims; no extra LLM or new facts."""
from copy import deepcopy
import re

VERSION = 'ra-issue-review-memo-1'
ROLES = {'source_fact': '원문 사실', 'applicant_position': '신청자 설명',
         'regulator_position': '기관 판단', 'interpretation': '해석'}


def build(report):
    result = report['results'][0]
    evidence = result.get('evidence', {})
    tasks = report['understanding']['tasks']
    known_tasks = {t['id'] for t in tasks}
    claims = result.get('claims', [])
    if any(not set(c['task_ids']) <= known_tasks for c in claims):
        raise ValueError('Memo claim references an unknown research task')
    checked = {r['task_id']: r for r in result.get('request_coverage', {}).get('tasks', [])}
    entries = []
    for claim in claims:
        refs = []
        for source in claim['sources']:
            block = evidence[source['evidence']]
            # Document identity is copied from the cited packet. It is not a
            # substitute for a clinical assertion or current-validity proof.
            refs.append({'evidence_id': source['evidence'], 'citation_span_id': source.get('citation_span_id'),
                'document_id': block.get('document_id'), 'version_id': block.get('version_id'),
                'title': block.get('title', ''), 'pdf_page': block.get('pdf_page'),
                'jurisdiction': block.get('jurisdiction'), 'product_id': block.get('product_id'),
                'quote': source['quote'], 'source_range': deepcopy(source.get('source_range')),
                'source_url': block.get('source_url'), 'review_links': deepcopy(block.get('review_links', [])),
                'source_role': block.get('role') or block.get('doc_group'),
                'statement_role': claim['kind']})
        entries.append({'claim_id': claim['id'], 'text': claim['text'], 'statement_role': claim['kind'],
            'products': deepcopy(claim['products']), 'task_ids': deepcopy(claim['task_ids']), 'sources': refs})
    issues, questions = [], []
    unresolved = list(dict.fromkeys(result.get('gaps', [])))
    for task in tasks:
        own = [e for e in entries if task['id'] in e['task_ids']]
        check = checked.get(task['id'], {})
        failures = [deepcopy(c) for c in check.get('checks', []) if not c['satisfied']]
        complete = result['status'] == 'answered' or (
            bool(check.get('completion_checkable')) and bool(check.get('minimum_requirements_met'))
            and not unresolved)
        # A free-form global gap has no verified task attribution. Keep it at
        # memo level rather than copying another issue's missing facts here.
        gaps = [] if complete else [task['request']]
        issue = {'issue_id': task['id'], 'request': task['request'],
            'status': 'complete' if complete else 'partial' if own else 'missing',
            'claims': own, 'unresolved_requests': gaps, 'failed_request_checks': failures,
            'potential_conflicts': [c for c in failures if c['kind'] == 'unambiguous_regimen'],
            'scope': {'source_roles': task.get('source_roles', []),
                      'products': report['understanding'].get('products', []),
                      'jurisdictions': report['understanding'].get('jurisdictions', [])}}
        issues.append(issue)
        if gaps:
            questions.append({'issue_id': task['id'], 'request': task['request'],
                'question_ko': f'“{task["request"]}”의 미확인 항목을 판단하려면 어떤 자료와 적용 조건을 추가로 확인해야 하는가?',
                'unresolved_requests': gaps, 'supporting_claim_ids': [e['claim_id'] for e in own],
                'draft_only': True, 'domain_expert_reviewed': False})
    memo = {'version': VERSION, 'original_question': report['question'], 'answer_status': result['status'],
        'issues': issues, 'unresolved_requests': unresolved, 'meeting_questions': questions, 'created_at': report.get('created_at'),
        'provenance': {'pipeline_version': report.get('pipeline_version'),
            'index': deepcopy(report.get('index')), 'research': deepcopy(report.get('research', {})),
            'generation_models': sorted({c.get('model_reported') or c.get('model_requested', '')
                                       for c in report.get('generation_telemetry', [])})},
        'domain_expert_reviewed': False, 'semantic_accuracy_certified': False,
        'conflict_detection': 'observable_request_checks_only_not_general_semantic_detection'}
    memo['markdown'] = render(memo)
    return memo


def render(memo):
    def plain(text):
        return re.sub(r'[\r\n]+', ' ', str(text or '')).replace('<', '&lt;').replace('>', '&gt;')
    lines = ['# RA 검토 메모', '', plain(memo['original_question']), '',
        '상태: ' + {'answered': '요청 충족 검사 통과', 'partial': '추가 확인 필요', 'not_found': '근거 미확보'}.get(memo['answer_status'], memo['answer_status']),
        '', '자동 생성 초안이며 RA 전문가 검토는 아직 이뤄지지 않았다.']
    for issue in memo['issues']:
        lines += ['', '## 쟁점 ' + issue['issue_id'], '', plain(issue['request'])]
        for claim in issue['claims']:
            lines += ['', f'### {claim["claim_id"]} · {ROLES.get(claim["statement_role"], claim["statement_role"])}',
                      '', plain(claim['text'])]
            for source in claim['sources']:
                lines += ['', f'- 출처: {plain(source["title"])} · p.{source["pdf_page"]} · {plain(source["version_id"])}',
                          '- 원문: ' + plain(source['quote'])]
        if issue['unresolved_requests']:
            lines += ['', '### 미확인 항목', ''] + ['- ' + plain(g) for g in issue['unresolved_requests']]
        if issue['potential_conflicts']:
            lines += ['', '서로 다른 투여 조건이 검사에서 포착됐다. 동일 시험·분석 조건의 실제 상충 여부를 검토해야 한다.']
    if memo['unresolved_requests']:
        lines += ['', '## 전체 질문의 추가 확인 항목', '']
        lines += ['- ' + plain(g) for g in memo['unresolved_requests']]
    if memo['meeting_questions']:
        lines += ['', '## 미팅 전 검토할 질문 초안', '']
        lines += ['- ' + plain(q['question_ko']) + ' [' + q['issue_id'] + ']' for q in memo['meeting_questions']]
    return '\n'.join(lines) + '\n'
