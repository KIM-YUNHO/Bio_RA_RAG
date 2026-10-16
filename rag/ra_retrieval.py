"""Task-wise hybrid retrieval and bounded, source-preserving reading context."""
from copy import deepcopy
import itertools
import os
import re

from rag.search import filter_idx, dense_hits, rrf, Bm25
from rag.ra_sources import permitted
from rag.service_scope import allowed
from rag import ra_answer

VERSION = 'ra-task-hybrid-context-7-topic-queries-slot-terms'


def diverse_hits(rankings, k, consensus_only=()):
    """Give each query a first reading opportunity before consensus ranking.

    A broad second query must not erase the best passage for a specific first
    query. This changes source selection only, never source facts or values.
    Rankings listed in consensus_only (by position) take part in fusion only.
    """
    first = [ranking[0] for n, ranking in enumerate(rankings) if ranking and n not in consensus_only]
    fused = rrf(rankings, top_n=max(40, k))
    out, seen = [], set()
    for score, key in first + fused:
        if key not in seen:
            seen.add(key)
            out.append((score, key))
        if len(out) >= k:
            break
    return out


def oracle_groups(corpus, understanding, guideline_index, source_ids, documents):
    """Evaluation-only source IDs; no expected values or gold prose is supplied."""
    if not isinstance(source_ids, list) or not source_ids or len(source_ids) > 80 or len(set(source_ids)) != len(source_ids):
        raise ValueError('Oracle context needs 1..80 unique source IDs')
    eligible = {corpus.chunks[int(i)]['chunk_id']: corpus.chunks[int(i)]
                for i in filter_idx(corpus.chunks, include_nondefault=True)
                if allowed(corpus.chunks[int(i)]) and permitted(corpus.chunks[int(i)].get('document_metadata') or {})}
    eligible.update({c['chunk_id']: c for c in guideline_index.chunks
                     if permitted(c.get('document_metadata') or {})} if guideline_index else {})
    selected = []
    for key in source_ids:
        if key not in eligible:
            raise ValueError('Oracle source is absent or protected: ' + str(key))
        c = eligible[key]
        if ((c['document_id'], c['version_id']) not in documents or c['jurisdiction'] not in understanding['jurisdictions']
                or c['product_id'] is not None and c['product_id'] not in understanding['products']):
            raise ValueError('Oracle source crosses the selected scope')
        selected.append(c)
    return [{'task_id': t['id'], 'product': p, 'jurisdiction': j, 'doc_groups': t['source_roles'],
             'studies': [], 'queries': t['queries'], 'context_origin': 'evaluation_selected_source_ids',
             'hits': [{'score': 1., 'chunk': c} for c in selected if c['product_id'] == p and c['jurisdiction'] == j]}
            for t in understanding['tasks'] for p, j in dict.fromkeys((c['product_id'], c['jurisdiction']) for c in selected)]


# Retrieval knobs; the default is production. 2026-10-14 grid on release 04: stripping slot-constant
# words from BM25 queries raised silver recall 0.855->0.882; lower BM25 weight and an added
# original-question ranking lost recall or stability (data/runs/retrieval_stability_2026-10-14).
DEFAULT_PROFILE = {'bm25_weight': 1.0, 'question_query': None, 'strip_slot_terms': True,
                   # Experimental cross-encoder selection of a slot's k hits from its fused candidate pool; off by
                   # default (BIOSIM_RERANK=on enables it in the service). Same model as rag/rerank.py.
                   'rerank': os.environ.get('BIOSIM_RERANK', '').lower() == 'on', 'rerank_pool': 40, 'rerank_max_per_doc': None,
                   # The service setting keeps each query's first hit (2026-10-15: +12/-4 vs +12/-6 without).
                   'rerank_keep_first_slots': os.environ.get('BIOSIM_RERANK', '').lower() == 'on',
                   'rerank_max_length': 512,
                   # Optional cap on all served hits of a question: with many searched groups (no product/agency
                   # picked, comparisons) each group gets total_hits // groups, never below min_group_hits.
                   'total_hits': None, 'min_group_hits': 4}
# Words every chunk of a product x agency slot shares; in a slot they only add BM25 noise.
SLOT_TERMS = re.compile(r'(?<![A-Za-z0-9])(?:EMA|FDA|Health\s+Canada|EPAR|European\s+Medicines\s+Agency|assessment\s+reports?|'
                        r'product\s+monographs?|summary\s+basis\s+of\s+decision|multi-?discipline\s+reviews?|labell?ing|label)(?![A-Za-z0-9])', re.I)


_RERANKERS = {}


def reranked_hits(rankings, k, consensus, chunks, queries, P, keep_first=False):
    """Choose k hits from the fused pool by cross-encoder score (max over the task's queries).

    The pool is the same first-slot + consensus order diverse_hits builds, only longer.
    keep_first keeps the first statistical-table candidate ahead of reranking; rerank_keep_first_slots
    keeps every query's first hit (diverse_hits' first slots) and reranks only the rest. Optional
    rerank_max_per_doc caps hits per document; rerank_max_length is the cross-encoder input length.
    Relevance ordering only; no source text changes.
    """
    length = P['rerank_max_length']
    if length not in _RERANKERS:
        from rag.rerank import LocalReranker
        _RERANKERS[length] = LocalReranker(max_length=length)
    reranker = _RERANKERS[length]
    pool = diverse_hits(rankings, max(k, P['rerank_pool']), consensus_only=consensus)
    if not pool:  # a group with no candidates (e.g. no document of that agency) has nothing to rerank
        return []
    scores = {}
    for query in queries:
        for score, i in reranker.rank(query, pool, chunks, limit=len(pool)):
            scores[i] = max(scores.get(i, float('-inf')), score)
    if P['rerank_keep_first_slots']:
        first = [r[0] for n, r in enumerate(rankings) if r and n not in consensus]
    else:
        first = [pool[0]] if keep_first and pool else []
    ordered = first + sorted(((scores[i], i) for _, i in pool), key=lambda x: -x[0])
    out, seen, per_doc = [], set(), {}
    for score, i in ordered:
        doc = chunks[i]['document_id']
        if i in seen or (P['rerank_max_per_doc'] and per_doc.get(doc, 0) >= P['rerank_max_per_doc']):
            continue
        seen.add(i)
        per_doc[doc] = per_doc.get(doc, 0) + 1
        out.append((score, i))
        if len(out) >= k:
            break
    return out


def strip_slot_terms(query):
    from rag import study_registry
    names = sorted({n for p, m in study_registry.products().items() for n in [p, *m.get('names', [])]}, key=len, reverse=True)
    for name in names:
        query = re.sub(r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])', ' ', query, flags=re.I)
    query = re.sub(r'\([^()]*\)', lambda m: m[0] if re.search(r'[A-Za-z]{3,}', re.sub(r'[\s,()]', '', m[0])) else ' ', query)
    return re.sub(r'\s+', ' ', SLOT_TERMS.sub(' ', query)).strip() or query


def retrieve(corpus, understanding, guideline_index, documents=None, include_nondefault=False, k=5, topic_queries=True,
             profile=None):
    # Repeated product/document/agency header words are shared by every chunk.
    # BM25 should discriminate the passage and its local structural context.
    cached = bool(getattr(corpus, 'shared_cache', False))
    bm = getattr(corpus, '_ra_bm25', None) if cached else None
    if bm is None:
        bm = Bm25([{**c, 'header': ' '.join(c.get('structure', {}).get('section_path', []))
                          + ' ' + (c.get('structure', {}).get('caption') or '')} for c in corpus.chunks], thread_safe=cached)
        if cached: corpus._ra_bm25 = bm
    try:
        return _retrieve(corpus, understanding, guideline_index, bm, documents, include_nondefault, k, topic_queries, profile)
    finally:
        if not cached: bm.db.close()


def _retrieve(corpus, understanding, guideline_index, bm, documents=None, include_nondefault=False, k=5, topic_queries=True,
              profile=None):
    tasks = understanding['tasks']
    P = {**DEFAULT_PROFILE, **(profile or {})}
    weights = [1.0, P['bm25_weight']]
    bm_text = strip_slot_terms if P['strip_slot_terms'] else (lambda q: q)
    from rag import ra_topic_queries
    question = understanding.get('source_question', '')
    fixed = {t['id']: ra_topic_queries.for_task(question, t, len(tasks)) if topic_queries else ([], [])
             for t in tasks}
    queries = list(dict.fromkeys([q for task in tasks for q in task['queries']]
                                 + [q for _, qs in fixed.values() for q in qs]
                                 + ([question] if P['question_query'] and question else [])))
    product_needed = any(set(t['source_roles']) - {'guideline'} for t in tasks)
    vectors = corpus.encode(queries) if product_needed else None
    query_vectors = dict(zip(queries, vectors)) if vectors is not None else {}
    eligible = [int(i) for i in filter_idx(corpus.chunks, include_nondefault=True)
                if allowed(corpus.chunks[int(i)]) and permitted(corpus.chunks[int(i)].get('document_metadata') or {})]
    default_ids = set(int(i) for i in filter_idx(corpus.chunks))
    slot_index = {}

    def slot(product, agency, roles):
        key = (product, agency, frozenset(roles))
        if key not in slot_index:
            slot_index[key] = [i for i in eligible if corpus.chunks[i]['product_id'] == product
                               and corpus.chunks[i]['jurisdiction'] == agency
                               and (documents is None or (corpus.chunks[i]['document_id'], corpus.chunks[i]['version_id']) in documents)
                               and (i in default_ids or include_nondefault or corpus.chunks[i]['doc_group'] in roles)]
        return slot_index[key]

    if P['total_hits']:
        searched = sum(bool(slot(p, a, set(t['source_roles']))) for t in tasks if set(t['source_roles']) - {'guideline'}
                       for p in understanding['products'] for a in understanding['jurisdictions'])
        searched += sum(len(understanding['jurisdictions']) for t in tasks if 'guideline' in t['source_roles'])
        k = max(min(k, P['min_group_hits']), min(k, P['total_hits'] // max(searched, 1)))
    groups = []
    for task in tasks:
        roles = set(task['source_roles'])
        if roles - {'guideline'}:
            for product in understanding['products']:
                for agency in understanding['jurisdictions']:
                    idx = slot(product, agency, roles)
                    rankings = []
                    for query in task['queries']:
                        rankings.append(rrf([dense_hits(query_vectors[query], corpus.vec, idx, top_n=40),
                                             bm.hits(bm_text(query), idx, top_n=40)], top_n=40, weights=weights))
                    topic_names, topic_qs = fixed[task['id']]
                    topic_pos = None
                    if topic_qs:
                        # One fused ranking for all fixed topic queries: it adds a
                        # stable reading opportunity without outvoting the planner.
                        rankings.append(rrf([rrf([dense_hits(query_vectors[q], corpus.vec, idx, top_n=40),
                                                  bm.hits(bm_text(q), idx, top_n=40)], top_n=40, weights=weights)
                                             for q in topic_qs], top_n=40))
                        topic_pos = len(rankings) - 1
                    question_pos = None
                    if P['question_query'] and question:
                        # The original question is identical on every run (dense only: it is Korean).
                        rankings.append(dense_hits(query_vectors[question], corpus.vec, idx, top_n=40))
                        question_pos = len(rankings) - 1
                    # Document roles guide relevance; they do not replace source
                    # reading or silently delete alternative admissible evidence.
                    rankings = [rrf([hits, [(s, i) for s, i in hits if corpus.chunks[i]['doc_group'] in roles]],
                                    top_n=40) for hits in rankings]
                    from rag.ra_statistical_tables import hits as statistic_table_hits
                    # Equal header matches are ordered by this task's own dense
                    # query similarity, never by their position in the document.
                    table_candidates=statistic_table_hits(task['request'],idx,corpus.chunks,limit=k,
                        relevance=lambda i, qs=task['queries']: max(float(corpus.vec[i] @ query_vectors[q]) for q in qs))
                    if table_candidates:
                        rankings.insert(0,table_candidates)
                        topic_pos = None if topic_pos is None else topic_pos + 1
                        question_pos = None if question_pos is None else question_pos + 1
                    # 'fuse': the ranking only adds consensus weight, never takes a first slot.
                    consensus = ((topic_pos,) if topic_queries == 'fuse' else ()) + ((question_pos,) if P['question_query'] == 'fuse' else ())
                    hits = (reranked_hits(rankings, k, consensus, corpus.chunks, task['queries'], P,
                                          keep_first=bool(table_candidates)) if P['rerank']
                            else diverse_hits(rankings, k, consensus_only=consensus))
                    groups.append({'task_id': task['id'], 'product': product, 'jurisdiction': agency,
                        'doc_groups': list(roles), 'studies': [], 'queries': task['queries'],
                        'topic_queries': {'topics': topic_names, 'queries': topic_qs,
                                          'version': ra_topic_queries.VERSION},
                        **({'selection': {'reranker': 'bge-reranker-v2-m3', 'pool': P['rerank_pool'],
                                          'max_per_doc': P['rerank_max_per_doc']}} if P['rerank'] else {}),
                        **({'served_k': k, 'total_hits': P['total_hits']} if P['total_hits'] else {}),
                        'hits': [{'score': s, 'chunk': corpus.chunks[i]} for s, i in hits]})
        if 'guideline' in roles:
            guideline_queries=list(task['queries'])
            expansion=[]
            if re.search(r'비교\s*(?:임상)?\s*효능\s*시험|comparative(?:\s+clinical)?\s+efficacy\s+stud(?:y|ies)',task['request'],re.I):
                # Literal topic translation, no expected result or gold value.
                literal='comparative clinical efficacy studies'
                if literal not in guideline_queries:
                    guideline_queries.append(literal);expansion.append({'query':literal,'basis':'explicit_requested_topic_translation'})
            for agency in understanding['jurisdictions']:
                hits = guideline_index.search(guideline_queries, [agency], documents, k) if guideline_index else []
                groups.append({'task_id': task['id'], 'product': None, 'jurisdiction': agency,
                    'doc_groups': ['guideline'], 'studies': [], 'queries': guideline_queries,'query_expansion':expansion,
                    'hits': [{'score': s, 'chunk': c} for s, c in hits],
                    'scope_gap': None if guideline_index else 'guideline_snapshot_not_connected',
                    **({'served_k': k, 'total_hits': P['total_hits']} if P['total_hits'] else {})})
    return groups


def packet(corpus, chunk, role):
    if chunk.get('role') == 'guideline':
        return [(deepcopy(chunk), role)]
    adapter = corpus.evidence_adapter
    result = []
    # Retain actual section/study headings as independently quotable source
    # blocks. Metadata alone cannot assign a numerical result to a study.
    anchors = chunk.get('structure', {}).get('heading_anchors', [])
    # The deepest literal heading and nearest study title locate the passage.
    # Repeating the full ancestor tree for every hit consumes the reading budget
    # without supplying additional body evidence.
    chosen = [h for h in anchors if h.get('kind') == 'study'][-1:]
    if chunk.get('doc_group')=='label':
        # Adult/pediatric route subheadings repeat across indications. Include
        # the actual nearest numbered indication heading from its source unit.
        chosen.extend(h for h in [next((a for a in reversed(anchors) if a.get('kind')=='numbered'),None)]
                      if h and h not in chosen)
    if anchors and anchors[-1] not in chosen: chosen.append(anchors[-1])
    for heading in chosen:
        location = heading.get('heading_location') or {}
        uid = location.get('block_id')
        unit = adapter.bundle.units.get(uid)
        if not unit or not unit.get('retrieval_eligible') or unit.get('review_required'):
            continue
        if (unit['document_id'], unit['version_id']) != (chunk['document_id'], chunk['version_id']):
            raise ValueError('Reading context heading crosses a source version')
        start, end = location.get('representation_start', 0), location.get('representation_end', len(unit['text']))
        if not 0 <= start < end <= len(unit['text']):
            raise ValueError('Invalid source heading range')
        if ra_answer.source_quote(chunk['text'], unit['text'][start:end]) is not None:
            continue  # The body already supplies this literal heading.
        context = deepcopy(chunk)
        context.update(chunk_id=f'heading:{uid}:{start}:{end}', text=unit['text'][start:end], pdf_page=location['pdf_page'])
        context['provenance']['source_refs'] = unit['source_refs']
        context['provenance']['source_spans'] = [{'unit_index': adapter.unit_index[uid], 'unit_id': uid,
            'representation_start': start, 'representation_end': end, 'separator_before': '', 'role': 'content'}]
        context['representation'].update(unit_ids=[uid], origins={uid: unit['content_origin']}, description_review_required=False)
        context['structure'].update(kind='text', caption=None, table_id=None, figure_id=None)
        result.append((context, 'source_heading'))
    result.append((chunk, role))
    return result


def blocks(corpus, selected):
    result = {}
    for chunk, role in selected:
        if chunk.get('role') == 'guideline':
            block = {**deepcopy(chunk), 'retrieval_role': role, 'evidence_status': 'native_unreviewed_guideline'}
        else:
            block = corpus.evidence_adapter.blocks([(chunk, role)])['E1']
            block['source'] = chunk.get('source')
            block['doc_group'] = chunk.get('doc_group')
            block['doc_date'] = chunk.get('doc_date')
        result[f'E{len(result) + 1}'] = block
    return result


def context(corpus, groups, question, understanding, client, citation_mode='quote', *, guideline_index=None):
    selected, omitted, seen = [], [], set()
    task_map = {t['id']: t for t in understanding['tasks']}
    reading_groups = []
    for group in groups:
        reading_roles = task_map[group['task_id']].get('reading_source_roles')
        if reading_roles == ['guideline'] and group.get('product') is not None:
            omitted.extend({'chunk_id': h['chunk']['chunk_id'], 'task_id': group['task_id'],
                            'product_id': group.get('product'), 'jurisdiction': group['jurisdiction'],
                            'reason': 'product_precedent_not_requested_for_general_guidance'} for h in group['hits'])
        else:
            reading_groups.append(group)
    if not any(g['hits'] for g in groups):
        return {}, {'packets': 0, 'omitted': [], 'source_clipping': 'none',
                    'estimated_prompt_and_schema_tokens': 0, 'semantic_approval': False}
    # Span review now receives only claims and their selected quotations,
    # without the original question, task plan or coverage output. Keep a
    # smaller cushion for that stage; both calls still have exact preflight.
    # Avoid spending the former question/coverage cushion on a larger answer
    # contract at the expense of previously admitted whole source packets.
    from rag.ra_obligations import build as question_obligations
    focused = citation_mode == 'spans' and bool(question_obligations(question))
    reserve = min((128 if focused else 600) if citation_mode == 'spans' else 1200,
                  max(256, client.input_limit // 4))
    budget = client.input_limit - reserve
    product_allowed = {corpus.chunks[int(i)]['chunk_id'] for i in filter_idx(corpus.chunks, include_nondefault=True)
                       if allowed(corpus.chunks[int(i)]) and permitted(corpus.chunks[int(i)].get('document_metadata') or {})
                       and (corpus.allowed_documents is None or (corpus.chunks[int(i)]['document_id'],
                            corpus.chunks[int(i)]['version_id']) in corpus.allowed_documents)}

    def add(chunk, role, group):
        if chunk['chunk_id'] in seen:
            return
        if chunk.get('role') != 'guideline' and chunk['chunk_id'] not in product_allowed:
            return
        package = [(c, r) for c, r in packet(corpus, chunk, role) if c['chunk_id'] not in seen]
        candidate = blocks(corpus, selected + package)
        total = ra_answer.cost(client, ra_answer.messages(question, understanding, candidate, citation_mode),
                               ra_answer.schema(candidate, understanding['tasks'], citation_mode, question=question))
        if total <= budget:
            selected.extend(package)
            seen.update(c['chunk_id'] for c, _ in package)
            return True
        else:
            omitted.append({'chunk_id': chunk['chunk_id'], 'task_id': group['task_id'],
                'product_id': chunk.get('product_id'), 'jurisdiction': chunk['jurisdiction'],
                'reason': 'whole_source_and_heading_package_exceeds_input_budget'})

    # Fairness across tasks/products/agencies precedes more hits from one group.
    for row in itertools.zip_longest(*(g['hits'] for g in reading_groups)):
        for group, hit in zip(reading_groups, row):
            if hit:
                add(hit['chunk'], 'retrieved', group)
    # Restore explicit visual references only inside the authorized corpus.
    for chunk, role in list(selected):
        if chunk.get('role') == 'guideline' or role != 'retrieved':
            continue
        for neighbour in corpus.evidence_adapter.linked_candidates(chunk, product_allowed):
            add(neighbour, 'linked_visual_candidate', {'task_id': 'linked'})
    # Preserve every admitted baseline packet before spending spare budget on
    # expansion. Rankings and stored retrieval candidates are never replaced.
    baseline_ids = [c['chunk_id'] for c, _ in selected]
    from rag.ra_parent_context import candidates, guideline_candidates
    from rag.ra_requirements import study_identity
    expansions, expanded, spent = [], set(), {}
    for row in itertools.zip_longest(*(g['hits'] for g in reading_groups)):
        for group, hit in zip(reading_groups, row):
            if not hit or hit['chunk']['chunk_id'] not in seen:
                continue
            seed = hit['chunk']
            is_guideline = seed.get('role') == 'guideline'
            identity = ((seed['document_id'], seed['version_id'], 'guideline') if is_guideline else study_identity(seed))
            key = (group['task_id'], identity)
            if identity is None or key in expanded:
                continue
            expanded.add(key)
            task = task_map[group['task_id']]
            neighbours = (guideline_candidates(guideline_index, seed, question) if is_guideline else
                          candidates(corpus, seed, product_allowed, task['request'], group['queries']))
            for neighbour in neighbours:
                if neighbour['chunk_id'] in seen:
                    continue
                # A group gets at most 1,200 source tokens of expansion. Exact
                # prompt/schema accounting in add() still enforces the total.
                group_key = ((group['task_id'], seed['document_id'], seed['version_id'], group['jurisdiction'])
                             if is_guideline else (group['task_id'], group['product'], group['jurisdiction']))
                size = client.tokenizer.one(neighbour['text'])
                if spent.get(group_key, 0) + size > 1200:
                    omitted.append({'chunk_id': neighbour['chunk_id'], 'task_id': group['task_id'],
                                    'reason': 'bounded_study_expansion_budget'})
                    continue
                if add(neighbour, 'same_document_guideline_context' if is_guideline else 'same_study_context', group):
                    spent[group_key] = spent.get(group_key, 0) + size
                    expansions.append({'seed_chunk_id': seed['chunk_id'], 'chunk_id': neighbour['chunk_id'],
                                       'task_id': group['task_id'], 'study_context_id': identity[-1],
                                       'source_tokens': size, 'context_kind': 'guideline' if is_guideline else 'study'})
    evidence = blocks(corpus, selected)
    tokens = ra_answer.cost(client, ra_answer.messages(question, understanding, evidence, citation_mode),
                            ra_answer.schema(evidence, understanding['tasks'], citation_mode, question=question)) if evidence else 0
    return evidence, {'input_limit': client.input_limit, 'review_reserved_tokens': reserve,
        'estimated_prompt_and_schema_tokens': tokens, 'source_clipping': 'none_whole_chunks_and_literal_headings',
        'packets': len(evidence), 'omitted': omitted, 'semantic_approval': False,
        'baseline_packet_ids': baseline_ids, 'study_expansions': expansions,
        'retrieval_rankings_changed_by_context': False}
