"""Task-wise hybrid retrieval and bounded, source-preserving reading context."""
from copy import deepcopy
import itertools
import re

from rag.search import filter_idx, dense_hits, rrf, Bm25
from rag.ra_sources import permitted
from rag.service_scope import allowed
from rag import ra_answer

VERSION = 'ra-task-hybrid-context-7-topic-queries-slot-terms'


def diverse_hits(rankings, k, consensus_only=(), same=None):
    """Give each query a first reading opportunity before consensus ranking.

    A broad second query must not erase the best passage for a specific first
    query. This changes source selection only, never source facts or values.
    Rankings listed in consensus_only (by position) take part in fusion only.
    same(a, b): a near-duplicate of an already selected passage does not take a slot.
    """
    first = [ranking[0] for n, ranking in enumerate(rankings) if ranking and n not in consensus_only]
    fused = rrf(rankings, top_n=max(40, k))
    out, seen = [], set()
    for score, key in first + fused:
        if key not in seen:
            seen.add(key)
            if same is not None and any(same(key, kept) for _, kept in out):
                continue
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
                   'suppress_boilerplate': False, 'collapse_duplicates': False, 'role_boost': True,
                   'extra_default_groups': ('correspondence',), 'rerank': 'ask_unit', 'rerank_pool': 40, 'rerank_fuse': True,
                   # Optional cap on all served hits of a question: with many searched groups (no product/agency
                   # picked, comparisons) each group gets total_hits // groups, never below min_group_hits.
                   # Off: on release 04 it saved prompt tokens but lost 3 gold units (post-retrieval record 4-6).
                   'total_hits': None, 'min_group_hits': 4}
# Reranker on by default since 2026-10-16: queries are the slot's product + agency + the planner's
# per-fact ask, fused with the hybrid order (RRF). Retrieval gold (100 items) recall@8 0.715 -> 0.804
# with no bucket below baseline; 29-question answers 278 -> 311 (docs/RA_QUERY_HANDLING_LOG.md).
# correspondence (FDA approval letters/packages) is searched by default since 2026-10-16: with the
# planner's document inventory, Q4 (FDA approval dates) 10/20 -> 15/20 and the 29-question total
# 269 -> 278 (data/runs/planner_facets_2026-10-15/e2e_final).
# Options measured 2026-10-15 on release 04 with frozen plans (data/runs/retrieval_scope_2026-10-15),
# all off by default: boilerplate suppression (no gain once short single facts are protected; it also
# shifts role-boosted fusion), near-duplicate collapsing (no gain), role_boost 0.25-0.5 (same as 1.0),
# role_boost 0 / correspondence by default (trade-offs, see eval records).
# Words every chunk of a product x agency slot shares; in a slot they only add BM25 noise.
SLOT_TERMS = re.compile(r'(?<![A-Za-z0-9])(?:EMA|FDA|Health\s+Canada|EPAR|European\s+Medicines\s+Agency|assessment\s+reports?|'
                        r'product\s+monographs?|summary\s+basis\s+of\s+decision|multi-?discipline\s+reviews?|labell?ing|label)(?![A-Za-z0-9])', re.I)


def boilerplate_ids(corpus):
    """Chunks that carry no passage: running headers/footers, logos, bare IDs.

    Content-free text (no alphabetic word at all), or short text that repeats,
    digits ignored, on at least 5 pages and 20% of its document's pages. A short
    fact such as "Date Filed: 2022-11-15" printed once is never boilerplate.
    Source text is unchanged; these chunks only stop competing for reading slots.
    """
    cached = getattr(corpus, '_ra_boilerplate', None)
    if cached is not None:
        return cached
    pages, seen = {}, {}
    for i, c in enumerate(corpus.chunks):
        pages.setdefault(c['document_id'], set()).add(c['pdf_page'])
        sig = (c['document_id'], re.sub(r'[\W\d_]+', ' ', c['text'].lower()).strip())
        seen.setdefault(sig, set()).add(c['pdf_page'])
    result = set()
    for i, c in enumerate(corpus.chunks):
        words = re.findall(r'[A-Za-z]{2,}', re.sub(r'<[^>]+>', ' ', c['text']))  # 'b' in '(b) (4)' is no word
        sig = (c['document_id'], re.sub(r'[\W\d_]+', ' ', c['text'].lower()).strip())
        repeated = len(seen[sig]) >= max(5, .2 * len(pages[c['document_id']]))
        if not words or (repeated and c.get('n_tokens', 0) < 40):
            result.add(i)
    corpus._ra_boilerplate = result
    return result


def near_duplicate(corpus, threshold=.8):
    """Word 3-gram Jaccard over passage text; label versions repeat whole sections."""
    cache = {}
    def grams(i):
        if i not in cache:
            words = re.findall(r'[a-z0-9]+', re.sub(r'<[^>]+>', ' ', corpus.chunks[i]['text'].lower()))
            cache[i] = {tuple(words[n:n + 3]) for n in range(max(1, len(words) - 2))}
        return cache[i]
    def same(a, b):
        x, y = grams(a), grams(b)
        return bool(x and y) and len(x & y) / len(x | y) >= threshold
    return same


AGENCY_LABEL = {'EU': 'EMA', 'US': 'FDA', 'CA': 'Health Canada'}


def unit_prefix(product, agency):
    """The slot's own product names and agency (the group is already one product x agency)."""
    from rag import study_registry
    key = lambda n: re.sub(r'[^a-z0-9]', '', n.lower())
    meta = study_registry.products().get(product, {}) if product else {}
    brands = [n.split('(')[0].strip() for n in meta.get('names', []) if key(n.split('(')[0]) != key(product or '')]
    names = [product, *list(dict.fromkeys(brands))[:2]] if product else []
    return ' '.join(dict.fromkeys(n for n in [*names, AGENCY_LABEL.get(agency, '')] if n)) + ': '


def rerank(corpus, task, hits, k, mode, product=None, agency=None, fuse=False):
    """Reorder a broad candidate pool with the local cross-encoder; keep the top k.

    mode: 'request' (the task's Korean request), 'queries' (planner queries), 'both' or
    'ask' (the planner's per-fact restatements); '<mode>_unit' prefixes each query with the
    slot's product names and agency. A passage scores the best of its queries. With fuse the
    hybrid and reranker ranks are combined (RRF, k=60): the reranker can promote a passage but
    cannot bury a strong hybrid hit. Scores order sources only.
    """
    model = getattr(corpus, '_ra_reranker', None)
    if model is None:
        from rag.rerank import LocalReranker
        model = corpus._ra_reranker = LocalReranker(batch_size=8, max_length=1024)
    # 'ask': the planner's restatement of each requested facet (+ separate item), or the request.
    asks = list(dict.fromkeys(f['ask'] + (' ' + item if item else '')
                              for f in task.get('answer_facets', []) if f.get('ask')
                              for item in (f.get('items') or [None])))
    unit = mode.endswith('_unit')
    base = mode[:-5] if unit else mode
    queries = {'request': [task['request']], 'queries': list(task['queries']),
               'both': [task['request'], *task['queries']], 'ask': asks or [task['request']]}[base]
    if unit:
        queries = [unit_prefix(product, agency) + q for q in queries]
    best = {}
    for query in queries:
        for score, i in model.rank(query, hits, corpus.chunks, limit=len(hits)):
            best[i] = max(best.get(i, score), score)
    reranked = sorted(((s, i) for i, s in best.items()), key=lambda x: -x[0])
    if not fuse:
        return reranked[:k]
    hybrid = {i: n for n, (_, i) in enumerate(hits, 1)}
    model_rank = {i: n for n, (_, i) in enumerate(reranked, 1)}
    fused = [(1 / (60 + n) + 1 / (60 + model_rank.get(i, len(hits) + 1)), i) for i, n in hybrid.items()]
    return sorted(fused, key=lambda x: -x[0])[:k]


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
    if P['suppress_boilerplate']:
        skip = boilerplate_ids(corpus)
        eligible = [i for i in eligible if i not in skip]
    same = near_duplicate(corpus) if P['collapse_duplicates'] else None
    slot_index = {}

    def slot(product, agency, roles):
        key = (product, agency, frozenset(roles))
        if key not in slot_index:
            slot_index[key] = [i for i in eligible if corpus.chunks[i]['product_id'] == product
                               and corpus.chunks[i]['jurisdiction'] == agency
                               and (documents is None or (corpus.chunks[i]['document_id'], corpus.chunks[i]['version_id']) in documents)
                               and (i in default_ids or include_nondefault or corpus.chunks[i]['doc_group'] in roles
                                    or corpus.chunks[i]['doc_group'] in P['extra_default_groups'])]
        return slot_index[key]

    def task_agencies(task):
        # A task bound to some agencies reads only those (planner's task.jurisdictions).
        return ([j for j in understanding['jurisdictions'] if j in (task.get('jurisdictions') or [])]
                or understanding['jurisdictions'])

    if P['total_hits']:
        searched = sum(bool(slot(p, a, set(t['source_roles']))) for t in tasks if set(t['source_roles']) - {'guideline'}
                       for p in understanding['products'] for a in task_agencies(t))
        searched += sum(len(task_agencies(t)) for t in tasks if 'guideline' in t['source_roles'])
        k = max(min(k, P['min_group_hits']), min(k, P['total_hits'] // max(searched, 1)))
    groups = []
    for task in tasks:
        roles = set(task['source_roles'])
        agencies = task_agencies(task)
        if roles - {'guideline'}:
            for product in understanding['products']:
                for agency in agencies:
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
                    # role_boost: weight of the planned-role sublist (True = 1.0, False = 0.0).
                    if float(P['role_boost']):
                        rankings = [rrf([hits, [(s, i) for s, i in hits if corpus.chunks[i]['doc_group'] in roles]],
                                        top_n=40, weights=[1.0, float(P['role_boost'])]) for hits in rankings]
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
                    if P['rerank']:
                        pool = diverse_hits(rankings, P['rerank_pool'], consensus_only=consensus, same=same)
                        hits = rerank(corpus, task, pool, k, P['rerank'], product=product, agency=agency,
                                      fuse=P['rerank_fuse']) if pool else pool
                    else:
                        hits = diverse_hits(rankings, k, consensus_only=consensus, same=same)
                    groups.append({'task_id': task['id'], 'product': product, 'jurisdiction': agency,
                        'doc_groups': list(roles), 'studies': [], 'queries': task['queries'],
                        'topic_queries': {'topics': topic_names, 'queries': topic_qs,
                                          'version': ra_topic_queries.VERSION},
                        **({'selection': {'reranker': 'bge-reranker-v2-m3', 'mode': P['rerank'], 'pool': P['rerank_pool'],
                                          'fuse': P['rerank_fuse']}} if P['rerank'] else {}),
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
            for agency in agencies:
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


def checklist_first(corpus, groups, question, understanding):
    """For each checklist entry (or open task request) x product x agency, the closest retrieved candidate.

    The entry text is the planner's restatement (ask) plus its item; closeness is the dense
    embedding similarity already used for retrieval. Only retrieved hits are candidates, so
    this changes the admission order, never the corpus.
    """
    import os
    from rag.ra_obligations import checklist
    # Measured without effect (2026-10-16, input budget does not bind); off unless RA_CHECKLIST_PACKING=1.
    if os.environ.get('RA_CHECKLIST_PACKING', '0') != '1' or not hasattr(corpus, 'encode'):
        return []
    entries = [e for e in checklist(question, understanding['tasks']) if e.get('product')]
    if not entries:
        # An open request (no facets, e.g. approval dates): the task request is the entry.
        entries = [{'product': p, 'need': t['request']} for t in understanding['tasks']
                   if set(t['source_roles']) - {'guideline'} for p in understanding.get('products') or []]
    if not entries:
        return []
    texts = list(dict.fromkeys(e['need'] + (' ' + e['item'] if e.get('item') else '') for e in entries))
    vectors = dict(zip(texts, corpus.encode(texts)))
    index = getattr(corpus, '_chunk_index', None)
    if index is None:
        index = corpus._chunk_index = {c['chunk_id']: i for i, c in enumerate(corpus.chunks)}
    picks = []
    for entry in entries:
        vector = vectors[entry['need'] + (' ' + entry['item'] if entry.get('item') else '')]
        for group in groups:
            if group.get('product') != entry['product'] or not group['hits']:
                continue
            scored = [(float(corpus.vec[index[h['chunk']['chunk_id']]] @ vector), n, h)
                      for n, h in enumerate(group['hits']) if h['chunk']['chunk_id'] in index]
            if scored:
                picks.append((max(scored, key=lambda x: (x[0], -x[1]))[2]['chunk'], group))
    return picks


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
    from rag.ra_obligations import slot_rows
    focused = citation_mode == 'spans' and bool(slot_rows(question, understanding['tasks']))
    reserve = min((128 if focused else 600) if citation_mode == 'spans' else 1200,
                  max(256, client.input_limit // 4))
    # A larger generation output (many enumerated claims) comes out of the input budget.
    reserve += ra_answer.extra_generation_tokens(client, understanding, question)
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
                               ra_answer.schema(candidate, understanding['tasks'], citation_mode, question=question,
                               claim_budget=ra_answer.claim_budget(understanding)))
        if total <= budget:
            selected.extend(package)
            seen.update(c['chunk_id'] for c, _ in package)
            return True
        else:
            omitted.append({'chunk_id': chunk['chunk_id'], 'task_id': group['task_id'],
                'product_id': chunk.get('product_id'), 'jurisdiction': chunk['jurisdiction'],
                'reason': 'whole_source_and_heading_package_exceeds_input_budget'})

    # Checklist coverage first: each requested entry gets its closest candidate per product x agency.
    for chunk, group in checklist_first(corpus, reading_groups, question, understanding):
        add(chunk, 'retrieved', group)
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
                            ra_answer.schema(evidence, understanding['tasks'], citation_mode, question=question,
                               claim_budget=ra_answer.claim_budget(understanding))) if evidence else 0
    return evidence, {'input_limit': client.input_limit, 'review_reserved_tokens': reserve,
        'estimated_prompt_and_schema_tokens': tokens, 'source_clipping': 'none_whole_chunks_and_literal_headings',
        'packets': len(evidence), 'omitted': omitted, 'semantic_approval': False,
        'baseline_packet_ids': baseline_ids, 'study_expansions': expansions,
        'retrieval_rankings_changed_by_context': False}
