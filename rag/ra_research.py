"""One bounded local search round for missing requested-facet candidates.

This runs before synthesis, adds no LLM calls, never replaces admitted evidence,
and does not treat a lexical candidate as proof of semantic sufficiency.
"""
from copy import deepcopy
import re

from rag import ra_answer, ra_obligations, ra_retrieval
from rag.ra_sources import permitted
from rag.service_scope import allowed

VERSION = 'ra-bounded-missing-facet-research-3-reading-scope'
TERMS = {
    'dose_route': 'actual administered dose route', 'comparator': 'reference comparator treatment arms',
    'design': 'randomised double blind parallel group study design', 'population': 'healthy study population',
    'stratification': 'stratification criteria baseline timing body weight', 'purpose': 'primary objective purpose',
    'reference_selection': 'reference medicinal product comparator selection',
    'analytical_bridge': 'analytical bridging structural functional all three products',
    'clinical_bridge': 'clinical PK PD bridging case by case',
    'assay_targets': 'assay analyte serum concentration immunogenicity measurement',
    'document_status_scope': 'adoption coming into effect scope',
    'follow_up': 'follow up sampling duration period',
    'eligibility': 'inclusion eligibility body weight BMI screening',
    'table_statistics': 'PK statistical summary Table Mean SD Median Range',
    'dosing_schedule': 'first subcutaneous maintenance dosing schedule interval',
}


def facet_candidate(row, evidence):
    """A paragraph mentioning an endpoint is not the requested statistical table."""
    if row['facet'] == 'table_statistics':
        from rag import ra_table_citations, ra_spans
        spans = ra_spans.catalog(evidence)[0]
        return any((row['product'] is None or evidence[r['evidence']].get('product_id') == row['product']) and
            (not row.get('endpoint') or re.search(re.escape(row['endpoint']),
             '\n'.join(spans[a]['quote'] for a in r['required_span_ids']),re.I))
            for r in ra_table_citations.bindings(evidence,spans).values())
    if row['facet'] == 'dosing_schedule':
        return any((row['product'] is None or b.get('product_id') == row['product']) and
                   ra_obligations.schedule_candidate(b['text'],row.get('context_pattern')) for b in evidence.values())
    return None


def extend(corpus, groups, evidence, budget, question, understanding, client, guideline_index,
           *, max_rounds=1, citation_mode='spans'):
    if type(max_rounds) is not int or not 0 <= max_rounds <= 1:
        raise ValueError('Local research round cap must be 0 or 1')
    result, info = deepcopy(evidence), deepcopy(budget)
    rows = ra_obligations.build(question)
    pointers = ra_obligations.pointers(question, evidence, rows)
    missing = [r for r in rows if r['facet'] in TERMS and
               not (facet_candidate(r,evidence) if r['facet'] in ('table_statistics','dosing_schedule')
                    else pointers.get(r['id']))]
    # Prioritize source structures which lexical paragraph pointers cannot supply.
    missing.sort(key=lambda r:r['facet'] not in ('table_statistics','dosing_schedule'))
    trace = {'version': VERSION, 'policy': 'pre_generation_missing_facet_candidates_only',
        'round_cap': max_rounds, 'search_cap': 2, 'new_packet_cap': 6, 'new_source_token_cap': 1200,
        'additional_llm_calls': 0, 'rounds': 0, 'searches': [], 'added_packets': [],
        'missing_candidate_facets': [r['id'] for r in missing],
        'semantic_sufficiency_certified': False, 'original_evidence_preserved': True}
    if not max_rounds or not missing:
        trace['stop_reason'] = 'disabled' if not max_rounds else 'no_missing_recognized_facet_candidate'
        return result, info, trace
    seen = {b.get('chunk_id') for b in evidence.values()}
    spent = 0
    trace['rounds'] = 1
    # The initial context builder excludes product precedents when the task
    # asks to read general guidance only. Reapply that boundary here: the broad
    # retrieval plan can still contain correspondence/product groups which
    # must not return through the supplementary search path.
    reading_roles = {t['id']: t.get('reading_source_roles') for t in understanding['tasks']}
    original_groups = {(g['task_id'], g.get('product'), g.get('jurisdiction')) for g in groups
                       if not (reading_roles.get(g['task_id']) == ['guideline']
                               and g.get('product') is not None)}
    for row in missing[:2]:
        if row['facet'] in ('table_statistics','dosing_schedule') and facet_candidate(row,result):
            continue
        plans = []
        for task in understanding['tasks']:
            modified = deepcopy(task)
            seed=task['queries'][-1] if row['facet']=='dosing_schedule' else task['queries'][0]
            query = seed + ' ' + TERMS[row['facet']]
            if row.get('endpoint'):
                query += ' ' + row['endpoint']
            if row['part']:
                query += ' Part ' + row['part']
            modified['queries'] = [query[:800]]
            plans.append(modified)
        # One selected investigation task per missing facet bounds the total
        # retrieval calls. Scope stays a subset of the original search plan.
        suitable = [t for t in plans if any(g['task_id'] == t['id'] and
            (g.get('product') == row['product'] if row['product'] is not None else
             g.get('product') is None) for g in groups)]
        plans = suitable[:1]
        if not plans:
            continue
        plan = {**understanding, 'tasks': plans}
        if row['product'] is not None:
            if row['product'] not in understanding['products']:
                continue
            plan['products'] = [row['product']]
        candidates = ra_retrieval.retrieve(corpus, plan, guideline_index,
            documents=corpus.allowed_documents, include_nondefault=False,
            k=12 if row['facet'] in ('table_statistics','dosing_schedule') else 5)
        search = {'requirement_id': row['id'], 'facet': row['facet'], 'queries': plans[0]['queries'], 'hits': []}
        trace['searches'].append(search)
        for group in candidates:
            if (group['task_id'], group.get('product'), group.get('jurisdiction')) not in original_groups:
                continue
            hits=group['hits']
            if row['facet'] in ('table_statistics','dosing_schedule'):
                hits=sorted(hits,key=lambda h:not facet_candidate(row,{'E1':h['chunk']}))
            for hit in hits:
                chunk = hit['chunk']
                if (not allowed(chunk) or not permitted(chunk.get('document_metadata') or {})
                        or chunk.get('product_id') != group.get('product')
                        or chunk.get('jurisdiction') not in understanding['jurisdictions']
                        or (corpus.allowed_documents is not None and
                            (chunk['document_id'], chunk['version_id']) not in corpus.allowed_documents)):
                    continue
                search['hits'].append({'chunk_id': chunk['chunk_id'], 'document_id': chunk['document_id'],
                    'version_id': chunk['version_id'], 'pdf_page': chunk['pdf_page']})
                if chunk['chunk_id'] in seen:
                    continue
                if row['facet'] in ('table_statistics','dosing_schedule') and not facet_candidate(row,{'E1':chunk}):
                    continue
                package = [(c, role) for c, role in ra_retrieval.packet(corpus, chunk, 'bounded_research_candidate')
                           if c['chunk_id'] not in seen]
                if not package:
                    continue
                size = sum(client.tokenizer.one(c['text']) for c, _ in package)
                if spent + size > 1200 or len(trace['added_packets']) + len(package) > 6:
                    continue
                additions = ra_retrieval.blocks(corpus, package)
                trial = deepcopy(result)
                for block in additions.values():
                    trial[f'E{len(trial)+1}'] = block
                cost = ra_answer.cost(client, ra_answer.messages(question, understanding, trial, citation_mode),
                    ra_answer.schema(trial, understanding['tasks'], citation_mode, question=question))
                if cost > client.input_limit - budget.get('review_reserved_tokens', 600):
                    continue
                result = trial
                spent += size
                for c, _ in package:
                    seen.add(c['chunk_id'])
                    trace['added_packets'].append({'chunk_id': c['chunk_id'], 'requirement_id': row['id']})
    trace['new_source_tokens'] = spent
    trace['stop_reason'] = 'one_round_cap_reached' if trace['added_packets'] else 'no_admissible_new_packet_within_budget'
    assert all(result[k] == v for k, v in evidence.items())
    info.update(packets=len(result), bounded_research_added_packets=deepcopy(trace['added_packets']))
    if result:
        info['estimated_prompt_and_schema_tokens'] = ra_answer.cost(client,
            ra_answer.messages(question, understanding, result, citation_mode),
            ra_answer.schema(result, understanding['tasks'], citation_mode, question=question))
    return result, info, trace
