"""Frozen local 2x2 RAG comparison; answer labels never enter model requests.

Arms: legacy/RA answer path x retrieved/reviewed source IDs. Every arm
writes its own actual supplied context, telemetry and code fingerprint. Results
need source/expert adjudication; literal support is not reported as accuracy.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from dataclasses import asdict
import json
from pathlib import Path

from rag.pipeline import RAGPipeline
from rag.ra.ra_pipeline import RAPipeline
from rag.llm.local_chat import LocalChat
from rag.retrieval.search import filter_idx, to_pages
from rag.retrieval.plan_query import plan
from rag.ra.ra_sources import permitted
from rag.retrieval.service_scope import allowed
from preprocess.hybrid_config import load_config
from preprocess.ingest import sha256

ROOT = Path(__file__).resolve().parents[1]


class CappedLocalChat:
    provider = 'ollama'
    local_only = True
    source_selection = False

    def __init__(self, config, limit):
        self.inner = LocalChat(config)
        self.limit, self.attempts = limit, 0
        self.archive = []

    def __getattr__(self, name): return getattr(self.inner, name)

    def __call__(self, *args, **kwargs):
        if self.attempts >= self.limit:
            raise RuntimeError('Frozen local generation call cap reached')
        self.attempts += 1
        start = len(self.inner.calls)
        try:
            return self.inner(*args, **kwargs)
        finally:
            self.archive.extend(deepcopy(self.inner.calls[start:]))


def oracle_retrieval(source_ids):
    def retrieve(corpus, u, qv, use_dense=True, k=5, policy=None, agencies=None, products=None):
        eligible = {corpus.chunks[int(i)]['chunk_id']: int(i) for i in filter_idx(corpus.chunks, include_nondefault=True)
                    if allowed(corpus.chunks[int(i)]) and permitted(corpus.chunks[int(i)].get('document_metadata') or {})}
        if any(key not in eligible for key in source_ids):
            raise ValueError('Legacy oracle IDs absent/protected; guideline-only contexts require RA mode')
        for key in source_ids:
            c = corpus.chunks[eligible[key]]
            if ((c['document_id'], c['version_id']) not in corpus.allowed_documents
                    or c['product_id'] not in u['products'] or c['jurisdiction'] not in u['jurisdictions']):
                raise ValueError('Legacy oracle crosses request scope')
        result = []
        for unit in plan(u, agencies=agencies, products=products):
            hits = [(1., eligible[key]) for key in source_ids if corpus.chunks[eligible[key]]['product_id'] == unit['product']
                    and corpus.chunks[eligible[key]]['jurisdiction'] == unit['jurisdiction']]
            result.append((unit, to_pages(hits, corpus.chunks, max(k, len(hits)))))
        return result
    return retrieve


def fingerprint():
    paths = [*sorted((ROOT / 'rag').glob('*.py')), ROOT / 'eval/ra_research_eval.py']
    return {str(p.relative_to(ROOT)): sha256(p) for p in paths}


def run(suite, release, guidelines, out, mode, source, limit, case_ids=None):
    out = Path(out)
    if out.exists(): raise ValueError('Choose a new immutable evaluation output directory')
    spec = json.loads(Path(suite).read_text(encoding='utf-8'))
    cases = spec['cases']
    if case_ids:
        requested = set(case_ids)
        if len(requested) != len(case_ids) or not requested <= {c['id'] for c in cases}:
            raise ValueError('Unknown/repeated evaluation case')
        cases = [c for c in cases if c['id'] in requested]
    if source == 'oracle' and any(not c.get('oracle_chunk_ids') for c in cases):
        raise ValueError('Oracle arm requires reviewed source IDs for every selected case')
    if not cases or type(limit) is not int or not 1 <= limit <= 60:
        raise ValueError('Select cases and a bounded local call budget (1..60)')
    config = load_config()
    client = CappedLocalChat(config, limit)
    out.mkdir(parents=True)
    frozen = {'created_at': datetime.now(timezone.utc).isoformat(), 'mode': mode, 'context_source': source,
              'suite_sha256': sha256(suite), 'case_ids': [c['id'] for c in cases], 'code_hashes': fingerprint(),
              'release_manifest_sha256': sha256(Path(release) / 'release_manifest.json'),
              'guideline_manifest_sha256': sha256(Path(guidelines) / 'manifest.json') if guidelines else None,
              'max_local_calls': limit, 'provider': 'ollama', 'model': config.qwen.model,
              'model_settings': asdict(config.qwen),
              'gold_in_generation': False, 'accuracy_adjudication': 'pending', 'new_external_api_calls': 0}
    def write(name, value):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    write('design.json', frozen)
    write('suite_spec.json', spec)
    for relative in frozen['code_hashes']:
        copy = out / 'code' / relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes((ROOT / relative).read_bytes())
    results = []
    for case in cases:
        if fingerprint() != frozen['code_hashes']:
            raise RuntimeError('Evaluation code changed after freezing')
        kwargs = {'chat_client': client}
        if mode == 'ra':
            kwargs['guideline_snapshot'] = guidelines
            pipeline = RAPipeline(release, **kwargs)
        else:
            if source == 'oracle': kwargs['retrieval_fn'] = oracle_retrieval(case['oracle_chunk_ids'])
            pipeline = RAGPipeline(release, **kwargs)
        try:
            # Only request fields and optional immutable source IDs are sent.
            # Rubrics, expected claims, labels and case IDs stay in this runner.
            payload = {'question': case['question'], **case.get('scope', {})}
            write(case['id'] + '.request.json', payload)
            options = {'source_ids': case['oracle_chunk_ids']} if mode == 'ra' and source == 'oracle' else {}
            report = pipeline.ask(**payload, **options)
            write(case['id'] + '.response.json', report)
            results.append({'case_id': case['id'], 'task_type': case['task_type'], 'status': report['status'],
                'answer_statuses': [r['status'] for r in report['results']], 'seconds': report['seconds'],
                'local_calls_cumulative': client.attempts, 'source_adjudication': 'pending'})
        except Exception as exc:
            write(case['id'] + '.failure.json', {'error': f'{type(exc).__name__}: {exc}',
                'diagnostics': pipeline.last_failure, 'local_calls': client.attempts})
            raise
        finally:
            pipeline.close()
            write('local_calls.json', client.archive)
            write('progress.json', {'rows': results, 'local_calls': client.attempts,
                                     'new_external_api_calls': 0, 'accuracy_adjudication': 'pending'})
    return {'cases': len(results), 'local_calls': client.attempts, 'new_external_api_calls': 0,
            'source_adjudication': 'pending', 'answer_accuracy_certified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=ROOT / 'eval/ra_research_cases_2026-10-11.json')
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--guidelines', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mode', choices=('legacy', 'ra'), required=True)
    parser.add_argument('--source', choices=('retrieved', 'oracle'), default='retrieved')
    parser.add_argument('--case', action='append')
    parser.add_argument('--max-local-calls', type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.suite, args.release, args.guidelines, args.out, args.mode, args.source,
                         args.max_local_calls, args.case), ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
