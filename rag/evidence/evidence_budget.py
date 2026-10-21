"""Allocate whole source chunks fairly across retrieved physical pages."""


def select(queue, fits, dependencies=None):
    """Core hit from each page first, then other chunks and linked visuals.

    fits receives the complete proposed selection so metadata, prompt framing and
    output reservation can be included by the caller. No source text is clipped.
    """
    core, extras, seen = [], [], set()
    for chunk, role in queue:
        page = (chunk['document_id'], chunk['version_id'], chunk['pdf_page'])
        if role == 'retrieved' and page not in seen:
            core.append((chunk,role)); seen.add(page)
        else:
            extras.append((chunk,role))
    # Linked figures must not consume the budget before all physical hits.
    extras.sort(key=lambda x: {'source_heading':0,'retrieved':1}.get(x[1],2))
    dependencies=dependencies or {};lookup={c['chunk_id']:(c,r) for c,r in queue}
    result, omitted = [], []
    for chunk, role in core+extras:
        selected_ids={c['chunk_id'] for c,_ in result}
        if chunk['chunk_id'] in selected_ids:continue
        package=[(chunk,role)]
        for key in dependencies.get(chunk['chunk_id'],[]):
            if key not in lookup:raise ValueError('Required source heading is absent from candidate queue')
            if key not in selected_ids and key!=chunk['chunk_id']:package.append(lookup[key])
        candidate = result+package
        if fits(candidate):
            result = candidate
        else:
            omitted.append({'chunk_id':chunk['chunk_id'], 'pdf_page':chunk['pdf_page'],
                'retrieval_role':role, 'reason':'whole_source_chunk_and_required_heading_exceeds_reserved_token_budget' if len(package)>1 else 'whole_source_chunk_exceeds_reserved_token_budget'})
    return result, omitted
