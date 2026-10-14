"""Focused, capped OpenAI RA evaluation; paired arms reuse actual retrieved context."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from rag.openai_chat import EvaluationAPIChat
from rag.ra_pipeline import RAPipeline

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2'
GUIDELINES = ROOT / 'data/guidelines/ra_guidelines_native_2026-10-11_v1'
SUITE = ROOT / 'eval/ra_research_cases_2026-10-11.json'
CASES = ['RA04', 'RA10', 'RA14']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def freeze(folder, phase, cases, model='gpt-4o-mini'):
    paths = [*sorted((ROOT / 'rag').glob('*.py')), Path(__file__).resolve()]
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    design = {'created_at': datetime.now(timezone.utc).isoformat(), 'phase': phase,
        'cases': cases, 'provider': 'openai', 'model': model,
        'max_new_api_attempts_all_phases': 18, 'code_hashes': hashes,
        'suite_sha256': digest(SUITE), 'release_manifest_sha256': digest(RELEASE / 'release_manifest.json'),
        'chunks_sha256': digest(RELEASE / 'candidate/chunks.jsonl'),
        'guideline_manifest_sha256': digest(GUIDELINES / 'manifest.json'),
        'gold_sent': False, 'new_preprocessing_calls': 0, 'independent_holdout': False,
        'source_adjudication': 'Separate source-based agent review; not RA expert certification',
        'evaluation_axes': ['citation fidelity', 'product/study attribution', 'requested conditions',
                            'speaker roles', 'coverage', 'over-abstention', 'tokens and seconds'],
        'pairing': 'Same actual OpenAI baseline plan and complete supplied evidence; no planner/retrieval rerun for paired arm'}
    write(folder / 'design.json', design)
    for relative in hashes:
        target = folder / 'code' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    return hashes


def unchanged(hashes):
    if any(digest(ROOT / relative) != expected for relative, expected in hashes.items()):
        raise RuntimeError('Frozen generation code changed during this phase')


def run(out, phase, case_ids=None, label=None, reuse_case=None, model='gpt-4o-mini'):
    out = out.resolve()
    selected = case_ids or CASES
    if len(set(selected)) != len(selected) or not set(selected) <= set(CASES):
        raise ValueError('Choose unique cases from the focused pilot')
    cases = [c for c in read(SUITE)['cases'] if c['id'] in selected]
    if reuse_case and (phase not in ('paired', 'end_to_end') or len(cases) != 1 or not reuse_case.endswith(':' + cases[0]['id'])):
        raise ValueError('Reuse one exact successful API request only for its recorded case continuation')
    if phase == 'paired' and not all((out / 'baseline' / (c['id'] + '.json')).exists() for c in cases):
        raise ValueError('Run and inspect the actual baseline first')
    label = label or phase
    if not label or any(ch not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for ch in label):
        raise ValueError('Choose a simple immutable phase label')
    folder = out / label
    folder.mkdir(parents=True, exist_ok=False)
    frozen = freeze(folder, phase, cases, model)
    client = EvaluationAPIChat(out, max_new_calls=18, model=model)
    pipeline = None
    results = []
    try:
        if phase in ('baseline', 'end_to_end'):
            options = {'citation_mode': 'spans'} if phase == 'end_to_end' else {}
            pipeline = RAPipeline(RELEASE, guideline_snapshot=GUIDELINES, chat_client=client, **options)
            write(folder / 'pipeline_summary.json', pipeline.summary())
        for case in cases:
            unchanged(frozen)
            client.case = reuse_case or label + ':' + case['id']
            print(client.case, 'start', flush=True)
            try:
                if pipeline:
                    report = pipeline.ask(case['question'], **case['scope'],
                        progress=lambda **event: print(event['stage'], flush=True))
                else:
                    from rag import ra_answer
                    baseline_path = out / 'baseline' / (case['id'] + '.json')
                    baseline = read(baseline_path)
                    question, understanding = baseline['question'], baseline['understanding']
                    evidence = baseline['results'][0]['evidence']
                    started = time.monotonic()
                    result = ra_answer.generate(question, understanding, evidence, client, citation_mode='spans')
                    report = {'question': question, 'understanding': understanding, 'results': [
                        {**result, 'evidence': evidence, 'context_budget': baseline['results'][0]['context_budget']}],
                        'pipeline_version': 'ra-span-paired-evaluation', 'generation_provider': 'openai',
                        'context_origin': 'paired_actual_retrieved_baseline_context',
                        'baseline_response_sha256': digest(baseline_path), 'index': baseline['index'],
                        'seconds': round(time.monotonic() - started, 3),
                        'generation_telemetry': deepcopy(client.calls)}
                    client.calls.clear()
                write(folder / (case['id'] + '.json'), report)
                result = report['results'][0]
                results.append({'id': case['id'], 'status': result['status'],
                    'claims': len(result['claims']), 'rejected': len(result['validation']['rejected_items']),
                    'seconds': report['seconds'], 'api_calls': sum(not c.get('cache_reused') for c in report['generation_telemetry']),
                    'cached_calls': sum(bool(c.get('cache_reused')) for c in report['generation_telemetry'])})
                print(json.dumps(results[-1], ensure_ascii=False), flush=True)
            except Exception as exc:
                write(folder / (case['id'] + '.failure.json'), {'error': f'{type(exc).__name__}: {exc}',
                    'diagnostics': pipeline.last_failure if pipeline else None,
                    'generation_telemetry': deepcopy(client.calls)})
                raise
        own = [a for a in read(client.ledger_path)['attempts'] if a.get('evaluation_out') == str(out)]
        usage = {'new_api_attempts_all_phases': len(own),
            'prompt_tokens_all_phases': sum(a.get('prompt_tokens') or 0 for a in own),
            'completion_tokens_all_phases': sum(a.get('completion_tokens') or 0 for a in own),
            'statuses': {status: sum(a.get('status') == status for a in own) for status in {a.get('status') for a in own}}}
        write(folder / 'progress.json', {'rows': results, 'api_usage': usage, 'semantic_accuracy_certified': False})
        print(json.dumps(usage), flush=True)
    finally:
        if pipeline:
            pipeline.close()
        client.close()


def review_probe(out, label='review_probe'):
    from rag import ra_answer
    out = out.resolve()
    path = out / 'paired_review_ids/RA04.json'
    source = read(path)
    result = source['results'][0]
    if not label or any(ch not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for ch in label):
        raise ValueError('Choose a simple immutable probe label')
    folder = out / label
    folder.mkdir(exist_ok=False)
    model = 'gpt-4.1-2025-04-14'
    freeze(folder, 'review_only_same_existing_claims_and_context', [{'id': 'RA04', 'source_sha256': digest(path)}], model)
    client = EvaluationAPIChat(out, max_new_calls=18, model=model)
    client.case = label + ':RA04'
    try:
        messages = ra_answer.review_messages(source['question'], source['understanding'],
            result['evidence'], result['claims'], 'spans')
        schema = ra_answer.review_schema(result['claims'], source['understanding']['tasks'])
        if not ra_answer.fits(client, messages, schema):
            raise ValueError('Probe exceeds actual input budget')
        reply = client(messages, json_schema=schema)
        reviewed = ra_answer.check_review(json.loads(reply['text']), result['claims'], source['understanding']['tasks'])
        write(folder / 'RA04.json', {'source_response_sha256': digest(path), 'model': model,
            'review': reviewed, 'generation_telemetry': deepcopy(client.calls),
            'semantic_accuracy_certified': False, 'new_generation_calls': 0})
        print(json.dumps(reviewed, ensure_ascii=False), flush=True)
    finally:
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--phase', choices=('baseline', 'paired', 'end_to_end', 'review_probe'), required=True)
    parser.add_argument('--case', action='append')
    parser.add_argument('--label')
    parser.add_argument('--reuse-case')
    parser.add_argument('--model', choices=('gpt-4o-mini', 'gpt-4.1-2025-04-14'), default='gpt-4o-mini')
    args = parser.parse_args()
    if args.phase == 'review_probe':
        review_probe(args.out, args.label or 'review_probe')
    else:
        run(args.out, args.phase, args.case, args.label, args.reuse_case, args.model)
