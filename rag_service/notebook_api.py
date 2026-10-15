"""Small HTTP-independent adapter for project notebooks."""
import re

from rag_service.workspaces import source_packet

PROJECT = re.compile(r'/api/projects/([0-9a-f]{32})')
MEMOS = re.compile(r'/api/projects/([0-9a-f]{32})/memos')
REVISION = re.compile(r'/api/projects/([0-9a-f]{32})/memos/([0-9a-f]{32})/revisions')


def get(jobs, path):
    if path == '/api/projects':
        return jobs.workspaces.list()
    match = PROJECT.fullmatch(path)
    if match:
        return jobs.workspaces.get(match[1])
    return None


def post(jobs, path, payload):
    if path == '/api/projects':
        inventory = jobs.inventory()
        ingredients = {i['name'] for i in inventory['ingredients']}
        if not isinstance(payload, dict) or payload.get('ingredient') not in ingredients:
            raise ValueError('등록된 성분을 선택하세요.')
        return jobs.workspaces.create(payload)
    match = MEMOS.fullmatch(path)
    if match:
        source = source_packet(jobs, payload['source']) if isinstance(payload, dict) and payload.get('source') is not None else None
        return jobs.workspaces.add(match[1], payload, source)
    match = REVISION.fullmatch(path)
    if match:
        return jobs.workspaces.revise(match[1], match[2], payload)
    raise KeyError(path)
