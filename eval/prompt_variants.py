"""Stage-1 prompt comparison: generation only, on frozen reading contexts.

Each case reuses the evidence, plan and question saved by eval/paired_dev.py (branch
code), so retrieval and planning do not vary. 'current' uses the two generations
already saved there; each variant (RA_PROMPT_VARIANT) gets new generations. Metrics
are computed on the generated (pre-review) claims: all claims count as cited
facts, derived values are computed from them, unknown span IDs are counted.

  python eval/prompt_variants.py --out data/runs/prompt_rework_2026-10-15/stage1 --repeat 2
"""
import argparse, json, os, statistics, sys, time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REF = ROOT / 'data/runs/answer_eval_2026-10-15/paired_dev/branch'
# Request-path questions only: requirement-decomposed questions (ra_obligations) use a
# separate system prompt that these variants do not change.
CASES = ['Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6', 'Q7', 'C04', 'E01', 'U02', 'U03',
         'C01', 'C02', 'C03', 'E02', 'E03', 'E04', 'N01', 'T01', 'T02', 'T03', 'T04', 'T05', 'U01', 'U04']
CONTEXTS = ROOT / 'data/runs/prompt_rework_2026-10-15/contexts'  # frozen runs for questions without paired references
# Expected values from the prior source review (data/runs/answer_eval_2026-10-15/dev_rubric.json).
EXP = {'T01': ['5095', '5689', '5420', '1498', '1877', '1659'],
       'T02': ['145.592', '24.05', '313.15', ('588.110', '588.11'), '133.6061'],
       'T03': ['5045', '5480', '5400'],
       'T04': ['190.567', '647.38', '556.998', '168.9308', '188.550', '649.12', '579.246', '148.8189'],
       'T05': ['260', '390', '520', '90 mg', ('8주', '8 주'), ('12주', '12 주')],
       'C01': ['100 kg', '45 mg', '90 mg', ('12주', '12 주'), ('0주', '0 주', '제0주')],
       'C03': ['260', '390', '520', '90 mg', ('8주', '8 주')],
       'E02': [('일본', 'Japanese'), ('Day -1', 'Day-1', '-1일'), ('남성', 'male')],
       'E04': ['QTPP', ('분석', 'analytical'), ('브리징', 'bridg'), ('PK', '약동학')],
       'U01': ['100 kg', '45 mg', '90 mg', ('4주', '4 주'), ('12주', '12 주')],
       'U04': [('초안', 'draft'), ('시행', 'implementation')],
       'C02': [('남녀', '남성과 여성', 'men and women'), '90 mg', ('112', 'Day 112'), ('EU', '유럽')],
       'N01': ['45 mg', ('피하', 'SC', 'subcutaneous')],
       'C04': [('분석', 'analytical'), ('통상', '일반적으로', 'typically', 'routinely')],
       'E01': ['45 mg', ('피하', 'SC', 'subcutaneous')],
       'U02': ['260', '390', '520', '90 mg', ('8주', '8 주')],
       'U03': ['PREA', ('해당하지 않', '적용되지 않', 'apply')],
       'E03': ['45 mg', '90 mg', ('99일', '99 일', 'Day 99'), ('112일', 'Day 112', '112 일'), ('60', '60.0'),
               ('90.0', '90 kg'), '19', ('29.9', '30')]}


def pseudo_report(draft_claims, derived, evidence):
    """Generated claims as if all were kept: resolve spans and compute derived values."""
    from rag.ra import ra_spans
    from rag.ra.ra_derived import compute
    spans, _ = ra_spans.catalog(evidence)
    claims = [{k: v for k, v in c.items() if k != 'request_ids'} for c in draft_claims]
    resolved = ra_spans.resolve({'claims': claims, 'gaps': []}, evidence, spans)['claims']
    unknown = sum(1 for c in resolved if c.get('citation_error'))
    kept = [{**c, 'id': f'C{n}'} for n, c in enumerate(resolved, 1) if not c.get('citation_error')]
    made, withheld = compute(derived or [], kept)
    return {'results': [{'claims': kept + made, 'evidence': evidence, 'validation': {}, 'status': 'generated'}]}, unknown, withheld


def metrics(case, report, units):
    from eval.answer_scoring import score_question
    if case.startswith('Q'):
        return score_question(case, report, units)
    text = ' '.join(c['text'] for c in report['results'][0]['claims']).replace(',', '')
    hits = sum(any(a in text for a in (e if isinstance(e, tuple) else (e,))) for e in EXP[case])
    return {'score': hits, 'of': len(EXP[case])}


def draft_parts(request_generation):
    if 'request_results' in request_generation:
        claims = [c for v in request_generation['request_results'].values() for c in v['claims']]
    elif 'requirement_results' in request_generation:
        claims = [c for v in request_generation['requirement_results'].values() for c in v['claims']]
    else:
        claims = request_generation['claims']
    return claims, request_generation.get('derived', [])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--variants', default='modular')
    p.add_argument('--only', help='comma-separated case IDs')
    p.add_argument('--repeat', type=int, default=2)
    a = p.parse_args()
    from eval.e2e_enumeration import gold
    from rag.ra import ra_answer
    from rag.ra.ra_models import RequestedDraft, ObligationDraft, request_items
    from rag.ra.ra_obligations import build
    from rag.llm.openai_chat import OpenAIPipelineChat
    units = gold(ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    client = OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536)
    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in [c for c in CASES if not a.only or c in a.only.split(',')]:
        if (REF / f'{case}_1.json').exists():
            refs = [json.loads((REF / f'{case}_{n}.json').read_text(encoding='utf-8')) for n in (1, 2)]
            variants = a.variants.split(',')
        else:  # no paired reference: frozen context from one run; 'current' is generated like a variant
            frozen = json.loads((CONTEXTS / f'{case}_1.json').read_text(encoding='utf-8'))
            refs = [{'results': frozen['results'], 'understanding': frozen['understanding'], 'question': frozen['question']}]
            variants = ['current', *a.variants.split(',')]
        evidence, u, question = refs[0]['results'][0]['evidence'], refs[0]['understanding'], refs[0]['question']
        has_requirements = bool(build(question))
        for n, ref in enumerate(refs if 'current' not in variants else [], 1):
            claims, derived = draft_parts(ref['results'][0]['request_generation'])
            report, unknown, withheld = pseudo_report(claims, derived, ref['results'][0]['evidence'])
            out_tokens = sum(c.get('completion_tokens', 0) for c in ref['generation_telemetry'] if 'grounded' in c.get('schema', ''))
            rows.append({'case': case, 'variant': 'current', 'run': n, 'claims': len(claims), 'unknown_span_claims': unknown,
                         'derived_declared': len(derived), 'derived_withheld': len(withheld), 'output_tokens': out_tokens,
                         'requirement_path': has_requirements, **metrics(case, report, units)})
        for variant in variants:
            os.environ['RA_PROMPT_VARIANT'] = variant
            for n in range(1, a.repeat + 1):
                path = a.out / f'{case}_{variant}_{n}.json'
                if path.exists():
                    saved = json.loads(path.read_text(encoding='utf-8'))
                    rows.append(saved['row']); continue
                budget = ra_answer.claim_budget(u)
                msgs = ra_answer.messages(question, u, evidence, 'spans')
                contract = ra_answer.schema(evidence, u['tasks'], 'spans', question=question, claim_budget=budget)
                if budget > 8:
                    contract = {**contract, 'max_output_tokens': client.output_tokens + ra_answer.extra_generation_tokens(client, u, question)}
                client.begin_request()
                try:
                    reply = client(msgs, json_schema=contract)
                finally:
                    client.release()
                context = {'task_ids': [t['id'] for t in u['tasks']], 'request_ids': list(request_items(question))}
                if has_requirements:
                    parsed = ObligationDraft.model_validate_json(reply['text'], context={
                        'task_ids': context['task_ids'], 'requirements': {r['id']: r for r in build(question)}}).model_dump()
                else:
                    parsed = RequestedDraft.model_validate_json(reply['text'], context=context).model_dump()
                claims, derived = draft_parts(parsed)
                report, unknown, withheld = pseudo_report(claims, derived, evidence)
                row = {'case': case, 'variant': variant, 'run': n, 'claims': len(claims), 'unknown_span_claims': unknown,
                       'derived_declared': len(derived), 'derived_withheld': len(withheld),
                       'output_tokens': reply.get('completion_tokens'), 'requirement_path': has_requirements,
                       **metrics(case, report, units)}
                path.write_text(json.dumps({'row': row, 'draft': parsed, 'system': msgs[0]['content']}, ensure_ascii=False,
                                           indent=1, default=str), encoding='utf-8')
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    os.environ.pop('RA_PROMPT_VARIANT', None)
    (a.out / 'rows.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    by = {}
    for r in rows:
        by.setdefault((r['case'], r['variant']), []).append(r)
    print(f"{'case':5}" + ''.join(f'{v:>22}' for v in ['current', *a.variants.split(',')]))
    for case in [c for c in CASES if not a.only or c in a.only.split(',')]:
        cells = []
        for v in ['current', *a.variants.split(',')]:
            rs = by.get((case, v), [])
            cells.append(', '.join(f"{r['score']}/{r['of']}" for r in rs) + f" ({statistics.mean(r['claims'] for r in rs):.0f}c)" if rs else '-')
        print(f'{case:5}' + ''.join(f'{c:>22}' for c in cells) + ('  [requirement path]' if by.get((case, 'current'), [{}])[0].get('requirement_path') else ''))


if __name__ == '__main__':
    main()
