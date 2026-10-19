"""Small HTTP-independent adapter for project notebooks, internal documents and work drafts."""
import re

from rag_service.workspaces import source_packet
from rag_service import internal_documents, memo_items, memo_workflow, work_assist

PROJECT = re.compile(r'/api/projects/([0-9a-f]{32})')
MEMOS = re.compile(r'/api/projects/([0-9a-f]{32})/memos')
REVISION = re.compile(r'/api/projects/([0-9a-f]{32})/memos/([0-9a-f]{32})/revisions')
STRUCTURE = re.compile(r'/api/projects/([0-9a-f]{32})/memos/([0-9a-f]{32})/structure')
BRIEF = re.compile(r'/api/projects/([0-9a-f]{32})/briefs/([a-z_]+)')
DOCUMENTS = re.compile(r'/api/projects/([0-9a-f]{32})/documents')
BOARD = re.compile(r'/api/projects/([0-9a-f]{32})/workboard/(\d{4}-\d{2}-\d{2})')
DRAFTS = re.compile(r'/api/projects/([0-9a-f]{32})/work-drafts')
DRAFT = re.compile(r'/api/projects/([0-9a-f]{32})/work-drafts/([0-9a-f]{32})')
ISSUES = re.compile(r'/api/projects/([0-9a-f]{32})/issues')
GUIDES = re.compile(r'/api/projects/([0-9a-f]{32})/issue-guides')
GUIDE = re.compile(r'/api/projects/([0-9a-f]{32})/issue-guides/([0-9a-f]{32})')


def public(record):
    """A project as the memo screen reads it: internal documents are served by their own routes."""
    if not isinstance(record, dict) or 'documents' not in record:
        return record
    return {**{k: v for k, v in record.items() if k not in ('documents', 'issues')}, 'document_count': len(record['documents']),
            'issue_count': len(record.get('issues', []))}


def overview(project, as_of):
    """Everything the work screen shows for a date, computed from the stored sources."""
    as_of = memo_workflow.day(as_of)
    dates = sorted({d['date'] for d in project.get('documents', [])})
    return {'project_id': project['id'], 'as_of': as_of, 'states': internal_documents.board(project, as_of),
            'items': internal_documents.work_items(project, as_of),
            'issues': internal_documents.issue_list(project, as_of),
            'documents': internal_documents.catalog(project, as_of),
            'hidden_after_count': internal_documents.hidden_after(project, as_of),
            'document_dates': dates, 'simulation': project.get('simulation'),
            'scopes': project.get('work_scopes', {}), 'axis_labels': work_assist.AXIS_LABELS, 'axis_help': work_assist.AXIS_HELP,
            'tasks': {k: {'label': v['label'], 'template': v['template']} for k, v in work_assist.TASKS.items()}}


def get(jobs, path):
    if path == '/api/projects':
        return {**jobs.workspaces.list(), 'item_definitions': memo_items.definitions()}
    match = PROJECT.fullmatch(path)
    if match:
        return {**public(jobs.workspaces.get(match[1])), 'item_definitions': memo_items.definitions()}
    match = BRIEF.fullmatch(path)
    if match:
        return memo_items.brief(jobs.workspaces.get(match[1]), match[2])
    match = DOCUMENTS.fullmatch(path)
    if match:
        project = jobs.workspaces.get(match[1])
        return {'project_id': project['id'], 'documents': internal_documents.catalog(project),
                'scopes': project.get('work_scopes', {}), 'simulation': project.get('simulation')}
    match = BOARD.fullmatch(path)
    if match:
        return overview(jobs.workspaces.get(match[1]), match[2])
    match = DRAFTS.fullmatch(path)
    if match:
        jobs.workspaces.get(match[1])
        return {'drafts': jobs.work_drafts.list(match[1])}
    match = DRAFT.fullmatch(path)
    if match:
        return jobs.work_drafts.get(match[1], match[2])
    match = ISSUES.fullmatch(path)
    if match:
        return {'issues': internal_documents.issue_list(jobs.workspaces.get(match[1]))}
    match = GUIDES.fullmatch(path)
    if match:
        jobs.workspaces.get(match[1])
        return {'guides': jobs.issue_guides.list(match[1])}
    match = GUIDE.fullmatch(path)
    if match:
        return jobs.issue_guides.get(match[1], match[2])
    return None


def post(jobs, path, payload):
    if path == '/api/projects':
        inventory = jobs.inventory()
        ingredients = {i['name'] for i in inventory['ingredients']}
        if not isinstance(payload, dict) or payload.get('ingredient') not in ingredients:
            raise ValueError('등록된 성분을 선택하세요.')
        return public(jobs.workspaces.create(payload))
    match = MEMOS.fullmatch(path)
    if match:
        source = source_packet(jobs, payload['source']) if isinstance(payload, dict) and payload.get('source') is not None else None
        return public(jobs.workspaces.add(match[1], payload, source))
    match = REVISION.fullmatch(path)
    if match:
        return public(jobs.workspaces.revise(match[1], match[2], payload))
    match = STRUCTURE.fullmatch(path)
    if match:
        if not isinstance(payload, dict) or set(payload) != {'expected_revision'}:
            raise ValueError('메모 버전만 보내세요.')
        project = jobs.workspaces.get(match[1])
        memo = next((m for m in project['memos'] if m['id'] == match[2]), None)
        if memo is None:
            raise KeyError(match[2])
        old = memo.get('source') or {}
        locator = {k: old[k] for k in ('job_id', 'result_index', 'kind', 'claim_id', 'claim_ids', 'evidence_id') if k in old}
        # Verify before resolving/registering any source files.
        if not locator or jobs.reviews.report_hash(jobs.get(old['job_id'])) != old.get('report_sha256'):
            raise ValueError('저장 당시 보고서와 일치하지 않습니다. 기존 메모는 보존했습니다.')
        return public(jobs.workspaces.structure(match[1], match[2], payload['expected_revision'], source_packet(jobs, locator)))
    match = DOCUMENTS.fullmatch(path)
    if match:
        return jobs.workspaces.register_documents(match[1], payload)
    match = DRAFTS.fullmatch(path)
    if match:
        return jobs.work_drafts.submit(match[1], payload)
    match = GUIDES.fullmatch(path)
    if match:
        return jobs.issue_guides.submit(match[1], payload)
    raise KeyError(path)
