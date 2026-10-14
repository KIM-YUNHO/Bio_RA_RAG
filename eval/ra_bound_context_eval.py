"""Mini-only bound context controls, paired synthesis, and two missing full cases."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from eval.ra_span_api_eval import ROOT, RELEASE, GUIDELINES, SUITE, digest, read, write
from rag import ra_answer, ra_review, ra_spans
from rag.openai_chat import EvaluationAPIChat
from rag.ra_pipeline import RAPipeline

PREVIOUS = ROOT / 'data/runs/ra_span_openai_2026-10-11_v1'


def selected_only(evidence, claim):
    """Evaluation ablation: expose only original selected spans, never edit index."""
    catalog, _ = ra_spans.catalog(evidence)
    stable = {s['span_id']: s for s in catalog.values()}
    selected = [stable[s['citation_span_id']] for s in claim['sources']]
    reduced = {}
    for key, original in evidence.items():
        own = [s for s in selected if s['evidence'] == key]
        if not own:
            continue
        block = deepcopy(original)
        block['text'] = '\n'.join(s['quote'] for s in own)
        block['source_spans'] = [{'unit_id': s['unit_id'], 'representation_start': s['representation_start'],
            'representation_end': s['representation_end'], 'separator_before': '' if n == 0 else '\n'}
            for n, s in enumerate(own)]
        block.setdefault('provenance', {})['source_spans'] = deepcopy(block['source_spans'])
        block['retrieval_role'] = 'evaluation_selected_spans_only'
        reduced[key] = block
    spans, _ = ra_spans.catalog(reduced)
    aliases = {s['span_id']: alias for alias, s in spans.items()}
    proposal = {k: deepcopy(claim[k]) for k in ('id', 'text', 'kind', 'products', 'claim_basis', 'task_ids')}
    proposal['citation_span_ids'] = [aliases[s['citation_span_id']] for s in claim['sources']]
    bound = ra_spans.resolve({'claims': [proposal], 'gaps': []}, reduced, spans)['claims'][0]
    return reduced, bound


def run(out):
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    paths = [*sorted((ROOT / 'rag').glob('*.py')), Path(__file__), SUITE]
    frozen = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    cached = PREVIOUS / 'paired_review_ids/RA04.json'
    baseline = PREVIOUS / 'baseline/RA04.json'
    write(out / 'design.json', {'created_at': datetime.now(timezone.utc).isoformat(),
        'model': 'gpt-4o-mini', 'max_new_calls': 10, 'code_hashes': frozen,
        'release_sha256': digest(RELEASE / 'release_manifest.json'),
        'chunks_sha256': digest(RELEASE / 'candidate/chunks.jsonl'),
        'guideline_sha256': digest(GUIDELINES / 'manifest.json'),
        'cached_comparison_sha256': digest(cached), 'cached_plan_sha256': digest(baseline),
        'jobs': ['review_same_six_claims', 'dose_selected_only_control', 'paired_atomic_RA04', 'full_RA10', 'full_RA14'],
        'new_preprocessing_calls': 0, 'gold_sent': False, 'independent_holdout': False,
        'limitations': ['Single response per condition', 'Development cases reused',
            'Source adjudication is agent review, not RA expert certification',
            'Binding guards cannot certify complete clause splitting or semantic entailment']})
    for relative in frozen:
        target = out / 'code' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    client = EvaluationAPIChat(out, max_new_calls=10, model='gpt-4o-mini')
    pipeline = None
    rows = []

    def unchanged():
        if any(digest(ROOT / p) != h for p, h in frozen.items()):
            raise RuntimeError('Frozen evaluation code changed')
        assert client.model == 'gpt-4o-mini'

    def review_job(label, question, plan, evidence, claims):
        unchanged(); client.case = label
        messages = ra_review.messages(question, plan, evidence, claims, allow_expansion=True)
        contract = ra_review.schema(evidence, claims, plan['tasks'], allow_expansion=True)
        write(out / (label + '.input.json'), {'messages': messages, 'schema': contract,
            'evidence': evidence, 'claims': claims})
        if not ra_answer.fits(client, messages, contract):
            raise ValueError('Review input exceeds model budget')
        print(label, 'start', flush=True)
        raw = json.loads(client(messages, json_schema=contract)['text'])
        kept, rejected, checked = ra_review.apply(raw, evidence, claims, plan['tasks'], allow_expansion=True)
        write(out / (label + '.json'), {'raw': raw, 'claims': kept, 'rejected': rejected,
            'review': checked, 'generation_telemetry': deepcopy(client.calls)})
        client.calls.clear()
        row = {'label': label, 'kept': len(kept), 'rejected': len(rejected),
            'expanded_clauses': sum(bool(c['citation_expansion']) for c in kept)}
        rows.append(row); print(json.dumps(row), flush=True)

    def failure(label, exc):
        write(out / (label + '.failure.json'), {'error': type(exc).__name__ + ': ' + str(exc),
            'diagnostics': pipeline.last_failure if pipeline else None,
            'generation_telemetry': deepcopy(client.calls)})
        client.calls.clear()
        rows.append({'label': label, 'error': type(exc).__name__ + ': ' + str(exc)})
        print(json.dumps(rows[-1]), flush=True)

    try:
        comparison = read(cached)
        evidence = comparison['results'][0]['evidence']
        claims = comparison['results'][0]['claims']
        accepted, invalid = ra_answer.literal_check({'claims': claims, 'gaps': []}, evidence,
            comparison['understanding']['tasks'], check_numbers=False)
        if invalid or len(accepted) != len(claims):
            raise ValueError('Saved claims no longer bind to supplied sources')
        for claim, old in zip(accepted, claims):
            claim['id'] = old['id']
        try:
            review_job('review_same_six_claims', comparison['question'], comparison['understanding'], evidence, accepted)
        except Exception as exc:
            failure('review_same_six_claims', exc)
        dose = next(c for c in accepted if c['id'] == 'C5')
        reduced, bound = selected_only(evidence, dose)
        try:
            review_job('dose_selected_only_control', comparison['question'], comparison['understanding'], reduced, [bound])
        except Exception as exc:
            failure('dose_selected_only_control', exc)
        try:
            unchanged(); client.case = 'paired_atomic_RA04'
            saved = read(baseline)
            print(client.case, 'start', flush=True)
            start = time.monotonic()
            result = ra_answer.generate(saved['question'], saved['understanding'], saved['results'][0]['evidence'], client, 'spans')
            write(out / 'paired_atomic_RA04.json', {'question': saved['question'], 'understanding': saved['understanding'],
                'results': [{**result, 'evidence': saved['results'][0]['evidence']}],
                'context_origin': 'cached_actual_mini_plan_and_retrieved_evidence',
                'seconds': round(time.monotonic() - start, 3), 'generation_telemetry': deepcopy(client.calls)})
            client.calls.clear()
            rows.append({'label': client.case, 'status': result['status'], 'kept': len(result['claims']),
                         'rejected': len(result['validation']['rejected_items'])})
            print(json.dumps(rows[-1]), flush=True)
        except Exception as exc:
            failure('paired_atomic_RA04', exc)
        if not client.halted:
            pipeline = RAPipeline(RELEASE, guideline_snapshot=GUIDELINES, chat_client=client)
            write(out / 'pipeline_summary.json', pipeline.summary())
            for case in [c for c in read(SUITE)['cases'] if c['id'] in ('RA10', 'RA14')]:
                label = 'full_' + case['id']
                try:
                    unchanged(); client.case = label
                    print(label, 'start', flush=True)
                    report = pipeline.ask(case['question'], **case['scope'],
                        progress=lambda **event: print(event['stage'], flush=True))
                    write(out / (label + '.json'), report)
                    result = report['results'][0]
                    rows.append({'label': label, 'status': result['status'], 'kept': len(result['claims']),
                        'rejected': len(result['validation']['rejected_items'])})
                    print(json.dumps(rows[-1]), flush=True)
                except Exception as exc:
                    failure(label, exc)
                    if client.halted:
                        break
    finally:
        if pipeline:
            pipeline.close()
        own = [a for a in read(client.ledger_path)['attempts'] if a.get('evaluation_out') == str(out)] if client.ledger_path.exists() else []
        summary = {'rows': rows, 'new_calls': len(own), 'models': sorted({a['model'] for a in own}),
            'input_tokens': sum(a.get('prompt_tokens') or 0 for a in own),
            'output_tokens': sum(a.get('completion_tokens') or 0 for a in own),
            'statuses': {s: sum(a.get('status') == s for a in own) for s in {a.get('status') for a in own}},
            'semantic_accuracy_certified': False, 'production_restarted': False}
        write(out / 'summary.json', summary)
        print(json.dumps(summary), flush=True)
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out)
