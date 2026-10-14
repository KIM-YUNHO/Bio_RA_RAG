"""At most four mini calls; append isolated meaning review to saved candidates."""
import argparse
from copy import deepcopy
from pathlib import Path
import json

from eval.ra_span_api_eval import ROOT, read, write, digest
from rag import ra_support, ra_answer
from rag.openai_chat import EvaluationAPIChat


def run(source, out):
    source, out = source.resolve(), out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in [*sorted((ROOT / 'rag').glob('*.py')), Path(__file__)]}
    files = ['review_same_six_claims', 'dose_selected_only_control', 'paired_atomic_RA04', 'full_RA10', 'full_RA14']
    write(out / 'design.json', {'model': 'gpt-4o-mini', 'max_new_calls': 4, 'code_hashes': hashes,
        'source_dir': str(source), 'source_hashes': {f: digest(source / (f + '.json')) for f in files},
        'purpose': 'Matched final selected-citation review; reuse all prior planning/retrieval/synthesis',
        'gold_sent': False, 'new_preprocessing_calls': 0, 'independent_holdout': False})
    for relative in hashes:
        target = out / 'code' / relative; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    full = read(source / 'review_same_six_claims.json')
    absent = read(source / 'dose_selected_only_control.json')
    original = read(source / 'review_same_six_claims.input.json')
    plan = json.loads(original['messages'][1]['content'])
    plan['tasks'] = plan['research_tasks']
    controls = deepcopy(absent['claims'] + absent['rejected'])
    ablated = read(source / 'dose_selected_only_control.input.json')['evidence']
    for c in controls:
        for s in c['sources']:
            s['evidence'] = 'ABL_' + s['evidence']
    claims = deepcopy(full['claims']) + controls
    for n, c in enumerate(claims, 1):
        c['id'] = 'P' + str(n)
    evidence = deepcopy(original['evidence'])
    evidence.update({'ABL_' + k: v for k, v in ablated.items()})
    # Ablated quotes retain their own exact text/metadata; final input never uses
    # block text. References need not expose any surrounding original paragraph.
    jobs = [('context_controls', plan['original_question'], plan, evidence, claims)]
    for label in files[2:]:
        d = read(source / (label + '.json')); r = d['results'][0]
        jobs.append((label, d['question'], d['understanding'], r['evidence'], r['claims']))
    client = EvaluationAPIChat(out, max_new_calls=4, model='gpt-4o-mini')
    rows = []
    try:
        for label, question, understanding, evidence, claims in jobs:
            if any(digest(ROOT / p) != h for p, h in hashes.items()):
                raise RuntimeError('Frozen code changed')
            if not claims:
                rows.append({'label': label, 'skipped': 'no_candidates'}); continue
            client.case = label
            messages = ra_support.messages(question, understanding, evidence, claims)
            contract = ra_support.schema(claims, understanding['tasks'])
            write(out / (label + '.input.json'), {'messages': messages, 'schema': contract, 'claims': claims})
            print(label, 'start', flush=True)
            try:
                if not ra_answer.fits(client, messages, contract):
                    raise ValueError('Final review exceeds model budget')
                raw = json.loads(client(messages, json_schema=contract)['text'])
                kept, rejected, checked = ra_support.apply(raw, claims, understanding['tasks'])
                write(out / (label + '.json'), {'raw': raw, 'claims': kept, 'rejected': rejected,
                    'review': checked, 'generation_telemetry': deepcopy(client.calls)})
                rows.append({'label': label, 'kept': len(kept), 'rejected': len(rejected)})
            except Exception as exc:
                write(out / (label + '.failure.json'), {'error': type(exc).__name__ + ': ' + str(exc),
                    'generation_telemetry': deepcopy(client.calls)})
                rows.append({'label': label, 'error': type(exc).__name__ + ': ' + str(exc)})
            client.calls.clear(); print(json.dumps(rows[-1]), flush=True)
            if client.halted:
                break
    finally:
        own = [a for a in read(client.ledger_path)['attempts'] if a.get('evaluation_out') == str(out)]
        summary = {'rows': rows, 'new_calls': len(own), 'models': sorted({a['model'] for a in own}),
            'input_tokens': sum(a.get('prompt_tokens') or 0 for a in own),
            'output_tokens': sum(a.get('completion_tokens') or 0 for a in own),
            'semantic_accuracy_certified': False, 'production_restarted': False}
        write(out / 'summary.json', summary); print(json.dumps(summary), flush=True)
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(); run(args.source, args.out)
