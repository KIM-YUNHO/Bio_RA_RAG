"""Local upload progress and source-bound result reports; never call inference."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
import json
import os
from pathlib import Path
import uuid

from .ingest import sha256
from .files import write_json

KST = timezone(timedelta(hours=9))
VERSION = 'preprocessing-status-1'
LABELS = {'running': '처리 중', 'failed': '실패', 'partial_failure': '일부 실패',
    'awaiting_enrichment': '보완 처리 대기', 'review_required': '검토 필요',
    'completed': '처리 완료'}
FILES = ('extracted_assets.jsonl', 'extraction_pages.jsonl', 'source_units.jsonl',
    'visual_groups.jsonl', 'extraction_review_queue.jsonl', 'extraction_summary.json',
    'extraction_manifest.json', 'paddle_worker.log')
REASONS = {
    'ambiguous_table_context_owner': '각주·출처를 어느 표에 연결할지 확인 필요',
    'labels_without_textual_support': '라벨이 원문 문자 대조를 통과하지 못함',
    'numbers_without_textual_support': '숫자가 원문 문자 대조를 통과하지 못함',
    'no_textual_grounding_reference': '문자 대조에 사용할 원문이 없음',
    'model_reported_uncertainty': '모델이 판독 불확실성을 보고함',
    'continuation_semantics_require_review': '연속 표의 행·열 의미 확인 필요',
    'inconsistent_row_width': '행마다 열 수가 다름',
    'source_merged_cells_require_review': '원본 병합 셀 확인 필요',
    'redacted_cells_require_review': '비공개 셀 확인 필요',
    'source_redactions_require_review': '원본에 비공개 표시가 있음',
    'source_redaction_marker_not_preserved': '원본 비공개 표시가 후보에서 누락됨',
    'rowspan_in_redacted_table_requires_review': '비공개 영역으로의 병합 확인 필요',
    'visible_text_spans_redacted_rows': '보이는 문자가 비공개 행으로 확장됨',
    'generation_incomplete': '모델 응답이 끝까지 생성되지 않음',
    'invalid_response_schema': '판독 응답 형식 오류',
    'invalid_panel_order_or_identity': '그림 패널 순서·번호 확인 필요',
    'panel_count_differs_from_layout_regions': '그림 패널 수와 탐지 영역 수가 다름',
    'image_is_table_requires_reclassification': '그림으로 탐지된 영역이 표일 수 있음',
    'diagram_has_plot_scale': '설계도의 숫자 축 스케일 확인 필요',
    'logarithmic_scale_without_printed_label': '로그 스케일의 인쇄된 표기 확인 필요',
    'chart_interpretation_outside_description_task': '라벨 판독 범위를 넘는 곡선 해석이 있음',
    'unreadable_cells': '판독 불가 셀이 있음',
    'blank_cell_contains_text': '빈 셀 상태와 문자 내용이 맞지 않음',
    'visible_cell_has_no_text': '문자 셀 상태이나 내용이 없음',
    'redacted_cell_contains_visible_text': '비공개 셀에 일반 문자가 들어 있음',
    'source_numbers_missing': '원본 숫자가 후보에서 누락됨',
    'numbers_not_in_native_reference': '후보 숫자를 원문에서 찾지 못함',
    'native_row_labels_not_preserved': '원문 행 라벨이 보존되지 않음',
    'continuation_needs_context': '앞뒤 표 문맥 확인 필요',
    'first_row_spans_entire_table_needs_context': '표 머리글 병합 범위 확인 필요',
    'overlapping_spans': '병합 셀이 겹침', 'span_outside_table': '병합 범위가 표를 벗어남',
    'expected_one_html_table': '표 표현을 하나로 확인할 수 없음',
    'truncated_html': '표 표현이 잘림', 'table_has_no_body': '표 본문을 찾지 못함',
    'table_exceeds_limits': '표 크기가 자동 검사 한도를 넘음',
    'pending': '보완 처리 대기', 'pending_budget': '설정한 호출 한도에 도달해 대기',
    'pending_model': '로컬 모델을 사용할 수 없어 대기', 'failed': '보완 처리 실패',
}


def now():
    return datetime.now(KST).isoformat(timespec='seconds')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def outcome(failed, successful, pending, review):
    if failed:
        return 'partial_failure' if successful else 'failed'
    if pending:
        return 'awaiting_enrichment'
    return 'review_required' if review else 'completed'


class Progress:
    """One writer per output directory. Atomic lightweight checkpoints, not a job scheduler."""
    def __init__(self, out, documents, mode):
        self.out = Path(out).resolve()
        self.value = {'schema_version': VERSION, 'run_id': uuid.uuid4().hex, 'mode': mode,
            'lifecycle': 'running', 'stage': 'preparing', 'started_at': now(),
            'updated_at': now(), 'pid': os.getpid(), 'current': None,
            'documents': [{'document_id': d.document_id, 'version_id': d.version_id,
                'title': d.metadata.get('title') or Path(d.raw_path).name,
                'source_paths': d.source_paths, 'source_page_count': None,
                'planned_pages': None, 'requested_pages': None, 'page_counts': {}, 'error': None}
                for d in documents], 'qwen': {'total': 0, 'resolved': 0, 'statuses': {}}}
        self.documents = {(d['document_id'], d['version_id']): d for d in self.value['documents']}
        self.flush()
        # Replace a previous operation's completed report before starting new work.
        save_report(build_report(self.out), self.out)

    def flush(self):
        self.value['updated_at'] = now()
        write_json(self.out / 'processing_status.json', self.value)

    def plan(self, doc, total, selected=None, error=None):
        d = self.documents[(doc.document_id, doc.version_id)]
        d.update(source_page_count=total, planned_pages=len(selected) if selected is not None else total,
            requested_pages=selected, error=error)
        self.flush()

    def page(self, doc, number, result=None):
        self.value.update(stage='extracting', current={'document_id': doc.document_id, 'pdf_page': number})
        if result:
            counts = self.documents[(doc.document_id, doc.version_id)]['page_counts']
            counts[result] = counts.get(result, 0) + 1
        self.flush()

    def enrichment(self, total, statuses=None, current=None):
        statuses = dict(statuses or {})
        self.value.update(stage='enriching', current=current,
            qwen={'total': total, 'resolved': sum(statuses.values()), 'statuses': statuses})
        self.flush()

    def finish(self):
        self.value.update(lifecycle='finished', stage='finished', current=None, finished_at=now())
        self.flush()
        report = build_report(self.out)
        save_report(report, self.out)
        return report

    def fail(self, exc):
        self.value.update(lifecycle='failed', last_active=self.value['current'], current=None, finished_at=now(),
            error=f'{type(exc).__name__}: {exc}')
        self.flush()
        save_report(build_report(self.out), self.out)


def build_report(out):
    out = Path(out).resolve()
    progress_path = out / 'processing_status.json'
    manifest_path = out / 'extraction_manifest.json'
    progress = read_json(progress_path) if progress_path.exists() else None
    live = progress and progress['lifecycle'] in {'running', 'failed'}
    manifest = read_json(manifest_path) if manifest_path.exists() and not live else None
    if progress is None and manifest is None:
        raise ValueError('No PDF preprocessing run found; inventory has a separate report')
    base = {'schema_version': VERSION, 'generated_at': now(), 'output_directory': str(out),
        'run_id': progress['run_id'] if progress else None, 'progress': progress,
        'numeric_evidence_eligible': False, 'accuracy_certified': False,
        'artifacts': {name: str(out / name) for name in FILES if (out / name).exists()}}
    # A live operation may overwrite several extraction files. Never read a mixed snapshot
    # or present an earlier run's output as the current operation's completed result.
    if progress and progress['lifecycle'] in {'running', 'failed'}:
        base.update(state=progress['lifecycle'], state_label=LABELS[progress['lifecycle']],
            details_available=False, documents=progress['documents'], error=progress.get('error'),
            notice='실행 상태는 마지막 저장 시점 기준입니다. 처리 완료는 정확도 인증을 뜻하지 않습니다.')
        return base
    if progress is None and manifest.get('status') == 'running':
        base.update(state='running', state_label=LABELS['running'], details_available=False,
            documents=manifest['documents'], notice='이전 버전 작업의 마지막 상태는 처리 중입니다. 현재 실행 여부는 확인되지 않았습니다.')
        return base
    if manifest is None:
        raise ValueError('Finished progress has no extraction manifest')
    if progress and manifest.get('status_run_id') != progress['run_id']:
        raise ValueError('Progress and extraction snapshot belong to different operations')
    integrity = read_json(out / 'extraction_integrity.json')
    required = {'extracted_assets.jsonl', 'extraction_pages.jsonl', 'source_units.jsonl'}
    if manifest.get('grouping_version'):
        required.add('visual_groups.jsonl')
    if set(integrity['files']) != required:
        raise ValueError('Incomplete extraction checksum manifest')
    for name, digest in integrity['files'].items():
        if sha256(out / name) != digest:
            raise ValueError(f'Stored extraction checksum mismatch: {name}')
    assets, pages, units = (rows(out / name) for name in ('extracted_assets.jsonl', 'extraction_pages.jsonl', 'source_units.jsonl'))
    unit_by_id = {u['unit_id']: u for u in units}
    documents, review_items = [], []
    for doc in manifest['documents']:
        identity = (doc['document_id'], doc['version_id'])
        own_pages = [p for p in pages if (p['document_id'], p['version_id']) == identity]
        own_assets = [a for a in assets if (a['document_id'], a['version_id']) == identity]
        jobs = [a for a in own_assets if a.get('route', {}).get('task')]
        job_counts = Counter((a.get('enrichment') or {}).get('status', 'pending') for a in jobs)
        page_counts = Counter(p['status'] for p in own_pages)
        own_units = [unit_by_id[a['asset_id']] for a in own_assets]
        errors = [e for e in manifest.get('document_errors', []) if (e['document_id'], e['version_id']) == identity]
        failed = sum(n for s, n in page_counts.items() if s != 'parsed') + job_counts['failed'] + len(errors)
        pending = sum(n for s, n in job_counts.items() if s.startswith('pending'))
        for a in own_assets:
            u, e = unit_by_id[a['asset_id']], a.get('enrichment') or {}
            # Logical figure members are retained for provenance, not separate review tasks.
            if u.get('retrieval_role') == 'logical_figure_member':
                continue
            reasons = set(e.get('validation', {}).get('issues', []))
            reasons.update(a.get('context_association_issues', []))
            if a.get('continuation_status') == 'data_fragment_continuation_candidate':
                reasons.add('continuation_semantics_require_review')
            if e.get('status') == 'failed' or e.get('status', '').startswith('pending'):
                reasons.add(e['status'])
            if a.get('route', {}).get('task') and not e:
                reasons.add('pending')
            if u.get('review_required') or u.get('description_review_required') or reasons:
                review_items.append({'document_id': a['document_id'], 'version_id': a['version_id'],
                    'asset_id': a['asset_id'], 'pdf_page': a['pdf_page'], 'kind': a['asset_type'],
                    'group_label': a.get('group_label'), 'reasons': sorted(reasons),
                    'reason_labels': [REASONS.get(r, '추가 검토 필요') for r in sorted(reasons)],
                    'job_status': e.get('status', 'pending') if a.get('route', {}).get('task') else None,
                    'retrieval_eligible': u['retrieval_eligible'],
                    'content_origin': u['content_origin'], 'description_review_required': bool(u.get('description_review_required')),
                    'source_refs': a['source_refs'], 'error': e.get('error'),
                    'images': [{k: image[k] for k in ('path', 'role', 'pdf_page') if k in image}
                        for image in e.get('image_metadata', {}).get('images', [])],
                    **({'figure_labels': a['figure_labels']} if a.get('figure_labels') else {})})
        own_reviews = [r for r in review_items if (r['document_id'], r['version_id']) == identity]
        plan = next((d for d in (progress or {}).get('documents', []) if (d['document_id'], d['version_id']) == identity), {})
        selected_pages = manifest.get('pages_by_document', {}).get(doc['document_id'], manifest.get('pages'))
        state = outcome(failed, page_counts['parsed'], pending, len(own_reviews))
        if not own_pages and not errors:
            state = 'failed'
            errors = [{'error': 'No extraction page records'}]
        documents.append({'document_id': doc['document_id'], 'version_id': doc['version_id'],
            'title': doc['metadata'].get('title') or Path(doc['raw_path']).name,
            'source_paths': doc['source_paths'], 'state': state, 'state_label': LABELS[state],
            'scope': 'selected_pages' if selected_pages is not None else 'all_pages',
            'requested_pages': selected_pages, 'source_page_count': plan.get('source_page_count'),
            'planned_pages': plan.get('planned_pages', len(selected_pages) if selected_pages is not None else None),
            'page_counts': dict(page_counts), 'assets_by_type': dict(Counter(a['asset_type'] for a in own_assets)),
            'physical_assets': sum(not a.get('is_logical_group') for a in own_assets),
            'qwen_statuses': dict(job_counts), 'review_items': len(own_reviews),
            'retrieval_candidates': sum(u['retrieval_eligible'] for u in own_units),
            'blocked_units': sum(not u['retrieval_eligible'] for u in own_units),
            'description_reviews': sum(bool(u.get('description_review_required')) for u in own_units),
            'errors': errors + [{'pdf_page': p['pdf_page'], 'error': p.get('error'), 'invalid_blocks': p.get('invalid_blocks')}
                for p in own_pages if p['status'] != 'parsed']})
    states = Counter(d['state'] for d in documents)
    if states['failed'] or states['partial_failure']:
        state = 'partial_failure' if any(d['page_counts'].get('parsed', 0) for d in documents) else 'failed'
    elif states['awaiting_enrichment']:
        state = 'awaiting_enrichment'
    elif states['review_required']:
        state = 'review_required'
    else:
        state = 'completed'
    base.update(state=state, state_label=LABELS[state], details_available=True, documents=documents,
        review_items=review_items, counts={'documents': len(documents), 'pages': len(pages),
            'physical_assets': sum(not a.get('is_logical_group') for a in assets),
            'logical_figures': sum(bool(a.get('is_logical_group')) for a in assets),
            'review_items': len(review_items), 'retrieval_candidates': sum(u['retrieval_eligible'] for u in units),
            'blocked_units': sum(not u['retrieval_eligible'] for u in units),
            'figure_labels_supported': sum(a.get('figure_labels', {}).get('accepted_fields', 0) for a in assets),
            'figure_labels_pending_review': sum(a.get('figure_labels', {}).get('rejected_fields', 0) for a in assets)},
        snapshot_checksums_verified=True,
        notice='처리 완료는 선택한 범위의 실행 종료입니다. 검색 후보도 수치·의미 정확도 인증을 받지 않았습니다.')
    return base


def markdown(report):
    def cell(value):
        return escape(str(value)).replace('|', '\\|').replace('\n', ' ')
    lines = ['# 문서 전처리 결과', '', f"상태: **{report['state_label']}**", '', report['notice'], '',
        f"- 결과 폴더: `{report['output_directory']}`", f"- 보고서 시각: {report['generated_at']} (KST)"]
    if report.get('progress'):
        p = report['progress']
        lines += [f"- 실행 단계: {p['stage']}; 마지막 상태 저장: {p['updated_at']}",
            f"- Qwen 작업: {p['qwen']['resolved']}/{p['qwen']['total']} 상태 기록"]
    if not report['details_available']:
        if report.get('progress'):
            lines += ['', '| 문서 | 페이지 상태 기록 | 계획 페이지 |', '|---|---|---:|']
            for d in report['documents']:
                lines += [f"| {cell(d['title'])} | {cell(d['page_counts'])} | {d['planned_pages'] if d['planned_pages'] is not None else '?'} |"]
        lines += ['', '상세 결과는 실행 종료 후 생성됩니다. 강제 종료된 작업은 마지막 상태가 처리 중으로 남을 수 있습니다.']
        if report.get('error'):
            lines += ['', cell(report['error'])]
        return '\n'.join(lines) + '\n'
    lines += ['', '| 문서 | 범위 | 페이지 처리 | 상태 | 검토 항목 | 검색 후보 |', '|---|---|---|---|---:|---:|']
    for d in report['documents']:
        planned = d['planned_pages'] if d['planned_pages'] is not None else '?'
        lines += [f"| {cell(d['title'])} | {'선택 페이지' if d['scope']=='selected_pages' else '전체 페이지'} | {d['page_counts'].get('parsed', 0)}/{planned} | {d['state_label']} | {d['review_items']} | {d['retrieval_candidates']} |"]
    for d in report['documents']:
        for error in d['errors']:
            lines += [f"\n문서 오류 ({cell(d['title'])}): {cell(error)}\n"]
    lines += ['', '## 검토 항목', '', '| 문서 ID | 페이지 | 영역 | 검색 후보 | 사유 |', '|---|---:|---|---|---|']
    for item in report['review_items']:
        lines += [f"| {cell(item['document_id'])} | {item['pdf_page']} | {cell(item['group_label'] or item['asset_id'])} | {'캡션 등 후보 있음' if item['retrieval_eligible'] else '제외'} | {cell(', '.join(item['reason_labels']))} |"]
    lines += ['', '## 산출물 위치', '']
    lines += [f'- `{name}`: `{path}`' for name, path in report['artifacts'].items()]
    lines += ['', '논리 그림의 물리 패널 제외와 빈 표현도 검색 제외 수에 포함됩니다. 제외 수 전체가 실패 수는 아닙니다.',
        '그림 설명 검토가 남아도 원문 캡션만 검색 후보로 남을 수 있습니다. 보고서는 모델을 새로 호출하지 않습니다.']
    return '\n'.join(lines) + '\n'


def save_report(report, out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'processing_report.json', report)
    path = out / 'processing_report.md'
    temporary = path.with_suffix('.md.tmp')
    temporary.write_text(markdown(report), encoding='utf-8')
    temporary.replace(path)
