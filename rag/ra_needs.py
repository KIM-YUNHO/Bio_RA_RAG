"""Atomic information needs: one retrieval unit per requested fact x product x agency.

The planner lists each requested fact (answer_facets: kind, Korean ask, English query,
separate items). A need-level task copies its parent task's roles and scope and searches
with that fact's own query; retrieval then makes one group per need x product x agency.
Open tasks (no facets) stay as they are, and every faceted task keeps its own request as
a safety net in case the planner missed a fact. Rankings only; no answer content.
"""


def atomic_tasks(understanding):
    """The plan with each faceted task expanded into need-level tasks (ids T1.1, T1.2, ..., T1.req)."""
    tasks = []
    for task in understanding['tasks']:
        facets = [f for f in task.get('answer_facets', []) if f['facet'] != 'calculation'
                  and (f.get('query') or f.get('ask'))]
        if not facets:
            tasks.append({**task, 'need_of': task['id']})
            continue
        n = 0
        for facet in facets:
            for item in facet.get('items') or [None]:
                n += 1
                suffix = ' ' + item if item else ''
                tasks.append({**task, 'id': f"{task['id']}.{n}", 'need_of': task['id'],
                              'request': (facet.get('ask') or task['request']) + suffix,
                              'queries': [(facet.get('query') or task['queries'][0]) + suffix],
                              'answer_facets': [facet], 'topics': []})
        tasks.append({**task, 'id': f"{task['id']}.req", 'need_of': task['id']})
    return {**understanding, 'tasks': tasks}
