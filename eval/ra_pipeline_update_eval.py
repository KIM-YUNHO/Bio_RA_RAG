"""Frozen retrieval replay and mini-only, staged development verification."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy
from unittest.mock import patch

import numpy as np

from eval.ra_span_api_eval import ROOT, RELEASE, GUIDELINES, SUITE, read, digest
from rag import answer, ra_answer, ra_retrieval, ra_requirements
from rag.openai_chat import EvaluationAPIChat, OpenAIPipelineChat
from rag.ra_pipeline import RAPipeline, VERSION

OUT = ROOT / 'data/runs/ra_pipeline_update_2026-10-12_v1'
PREVIOUS = ROOT / 'data/runs/ra_bound_context_mini_2026-10-12_v1'
BASELINES = {'RA04': ROOT / 'data/runs/ra_span_openai_2026-10-11_v1/baseline/RA04.json',
             'RA10': PREVIOUS / 'full_RA10.json', 'RA14': PREVIOUS / 'full_RA14.json'}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def unchanged_index():
    baseline = read(OUT / 'baseline.json')
    if any(digest(ROOT / name) != value for name, value in baseline['index_sha256'].items()):
        raise RuntimeError('Frozen document/index artifact changed')


def selected(pipeline, scope):
    return {(d['document_id'], d['version_id']) for d in pipeline.documents
            if (not scope.get('products') or d['product_id'] in scope['products'] or d.get('role') == 'guideline')
            and (not scope.get('jurisdictions') or d['jurisdiction'] in scope['jurisdictions'])}


def local(reuse_vectors=None):
    unchanged_index()
    if (OUT / 'local_replay.json').exists():
        raise ValueError('Local replay already completed; preserve its result')
    baseline = runpy.run_path(str(OUT / 'baseline_code/rag/ra_retrieval.py'))
    baseline_answer = runpy.run_path(str(OUT / 'baseline_code/rag/ra_answer.py'))
    plans = {key: read(path)['understanding'] for key, path in BASELINES.items()}
    cases = {c['id']: c for c in read(SUITE)['cases'] if c['id'] in plans}
    # Only tokenizer/accounting is used in this phase; no provider calls.
    client = EvaluationAPIChat(OUT / 'local', max_new_calls=1, client=object())
    pipeline = RAPipeline(RELEASE, guideline_snapshot=GUIDELINES, chat_client=client)
    corpus = answer.Corpus('bge-m3', pipeline.bundle.candidate, local_only=True, bundle=pipeline.bundle, include_nondefault=True)
    rows = []
    try:
        queries = list(dict.fromkeys(q for p in plans.values() for t in p['tasks'] for q in t['queries']))
        if reuse_vectors:
            if read(reuse_vectors / 'queries.json') != queries:
                raise ValueError('Saved query-vector identities differ')
            vectors = np.load(reuse_vectors / 'query_vectors.npy')
            if vectors.shape != (len(queries), corpus.index_meta['dim']):
                raise ValueError('Saved query-vector dimensions differ')
            write(OUT / 'query_vector_receipt.json', {'source': str(reuse_vectors),
                'sha256': digest(reuse_vectors / 'query_vectors.npy'), 'new_query_embedding_calls': 0})
        else:
            vectors = corpus.encode(queries)
            corpus.release_encoder()
        np.save(OUT / 'query_vectors.npy', vectors)
        write(OUT / 'queries.json', queries)
        mapping = dict(zip(queries, vectors))
        class Saved:
            def encode_queries(self, values): return np.vstack([mapping[v] for v in values])
        corpus._emb = Saved()
        for key, original in plans.items():
            print(key, 'local replay', flush=True)
            case = cases[key]
            corpus.allowed_documents = selected(pipeline, case['scope'])
            groups_before = baseline['retrieve'](corpus, original, pipeline.guidelines, corpus.allowed_documents, k=5)
            candidate = deepcopy(original)
            candidate['tasks'] = [ra_requirements.route_task(case['question'], t) for t in original['tasks']]
            groups_after = ra_retrieval.retrieve(corpus, candidate, pipeline.guidelines, corpus.allowed_documents, k=5)
            group_key = lambda g: (g['task_id'], g['product'], g['jurisdiction'])
            after = {group_key(g): g for g in groups_after}
            preserved = all([(h['score'], h['chunk']['chunk_id']) for h in g['hits']] ==
                [(h['score'], h['chunk']['chunk_id']) for h in after[group_key(g)]['hits']] for g in groups_before)
            if not preserved: raise AssertionError('Existing retrieval ranking changed: ' + key)
            with patch.multiple(ra_answer, messages=baseline_answer['messages'], schema=baseline_answer['schema']):
                old_evidence, old_budget = baseline['context'](corpus, groups_before, case['question'], original, client, 'spans')
            new_evidence, new_budget = ra_retrieval.context(corpus, groups_after, case['question'], candidate, client, 'spans',
                                                         guideline_index=pipeline.guidelines)
            old_ids = {b['chunk_id'] for b in old_evidence.values()}
            new_ids = {b['chunk_id'] for b in new_evidence.values()}
            routed = any(t.get('reading_source_roles') for t in candidate['tasks'])
            if not routed and not old_ids <= new_ids: raise AssertionError('Existing admitted source lost: ' + key)
            write(OUT / 'local' / (key + '.json'), {'question': case['question'], 'understanding': candidate,
                'before_groups': groups_before, 'after_groups': groups_after, 'before_evidence': old_evidence,
                'evidence': new_evidence, 'before_budget': old_budget, 'context_budget': new_budget})
            row = {'id': key, 'existing_rankings_preserved': preserved,
                   'existing_context_preserved': old_ids <= new_ids, 'reading_role_corrected': routed,
                   'guideline_blocks': sum(b.get('role') == 'guideline' for b in new_evidence.values()),
                   'study_expansions': new_budget['study_expansions'], 'context_tokens': new_budget['estimated_prompt_and_schema_tokens']}
            rows.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != 'study_expansions'}), flush=True)
        unchanged_index()
        write(OUT / 'local_replay.json', {'created_at': datetime.now(timezone.utc).isoformat(), 'rows': rows,
            'new_api_calls': 0, 'document_embeddings_reused': True,
            'scope': 'Three cached development plans with identical query vectors, document scope and k=5; not full accuracy evaluation'})
        paths = [*sorted((ROOT / 'rag').glob('*.py')), Path(__file__), SUITE]
        frozen = {str(p.relative_to(ROOT)): digest(p) for p in paths}
        write(OUT / 'candidate.json', {'pipeline_version': VERSION, 'code_sha256': frozen,
            'model': 'gpt-4o-mini', 'max_new_api_calls': 9, 'cases': ['RA10', 'RA04', 'RA14'],
            'independent_holdout': False, 'gold_sent': False, 'automatic_retries': 0, 'service_restarted': False})
        for path in paths:
            target = OUT / 'candidate_code' / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(path.read_bytes())
    finally:
        corpus.release_encoder(); corpus.bm.db.close(); pipeline.close(); client.close()


def api(case_ids, runtime_client=False):
    unchanged_index()
    frozen = read(OUT / 'candidate.json')
    if any(digest(ROOT / name) != value for name, value in frozen['code_sha256'].items()):
        raise RuntimeError('Frozen candidate code changed')
    cases = {c['id']: c for c in read(SUITE)['cases']}
    client = (OpenAIPipelineChat(out_root=OUT / 'runtime_requests') if runtime_client else
              EvaluationAPIChat(OUT / 'api', max_new_calls=9, model='gpt-4o-mini'))
    pipeline = RAPipeline(RELEASE, guideline_snapshot=GUIDELINES, chat_client=client)
    try:
        for key in case_ids:
            if key not in frozen['cases']: raise ValueError('Outside focused development cases')
            path = OUT / 'api' / (key + '.json')
            if path.exists(): raise ValueError('Inspect saved response before any new execution')
            client.case = key
            print(key, 'mini verification', flush=True)
            try:
                report = pipeline.ask(cases[key]['question'], **cases[key]['scope'],
                    progress=lambda **event: print(event['stage'], flush=True))
            except Exception:
                write(path.with_suffix('.failure.json'), pipeline.last_failure)
                raise
            write(path, report)
            result = report['results'][0]
            print(json.dumps({'id': key, 'status': result['status'], 'claims': len(result['claims']),
                'rejected': len(result['validation']['rejected_items']), 'usage': report['usage']}), flush=True)
            print(result['answer_ko'], flush=True)
    finally:
        pipeline.close(); client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['local', 'api'])
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--reuse-query-vectors', type=Path)
    parser.add_argument('--runtime-client', action='store_true')
    parser.add_argument('--cases', nargs='+', choices=['RA10', 'RA04', 'RA14'], default=['RA10'])
    args = parser.parse_args()
    OUT = args.out.resolve()
    local(args.reuse_query_vectors) if args.phase == 'local' else api(args.cases, args.runtime_client)
