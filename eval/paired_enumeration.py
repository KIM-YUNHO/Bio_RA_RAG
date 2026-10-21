"""Paired retrieval -> packing -> generation comparison on frozen plans.

Planner variance is removed: every configuration reuses the same stored plans
(understanding) of the enumeration question. For each plan and configuration it
records gold dates in the top-k hits, in the packed context, and in the checked
answer (one generation + one review call). The bounded research round is off.

  python eval/paired_enumeration.py --plans data/runs/e2e_enumeration_2026-10-15/v3 --output <dir>
  python eval/paired_enumeration.py ... --no-generation        (retrieval and packing only, no API)
"""
import argparse, glob, json, sys, time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.e2e_enumeration import QUESTION, CONFIGS, gold, dates, score  # noqa: E402


def context_dates(blocks, units):
    seen = {k: set() for k in units}
    for b in blocks:
        key = (b.get('product_id'), b.get('jurisdiction'))
        if key in seen:
            seen[key] |= dates(b['text'])
    return sum(units[k].get('submission') in seen[k] for k in units) + sum(units[k].get('approval') in seen[k] for k in units)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    p.add_argument('--guidelines', type=Path, default=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2')
    p.add_argument('--plans', type=Path, required=True, help='Folder of stored reports whose understanding is reused')
    p.add_argument('--configs', default='default_retrieval,rerank_corr')
    p.add_argument('--no-generation', action='store_true')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    from rag.ra import ra_retrieval, ra_answer, ra_spans
    from rag.evidence.schema_budget import enum_count
    from rag.llm.openai_chat import OpenAIPipelineChat
    from rag.ra.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    units = gold(a.run)
    plans = [json.loads(Path(f).read_text(encoding='utf-8'))['understanding'] for f in sorted(glob.glob(str(a.plans / '*_?.json')))]
    pipe = RAPipeline(a.run, None, load_config(ROOT / 'configs/preprocess.local.json'),
                      guideline_snapshot=a.guidelines, research_rounds=0, retrieval_pages=8,
                      chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    a.output.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        pipe.warmup()
        corpus = pipe.retrieval_corpus()
        docs = {(d['document_id'], d['version_id']) for d in pipe.documents}
        corpus.allowed_documents = docs
        for n, u in enumerate(plans, 1):
            for name in a.configs.split(','):
                out = a.output / f'plan{n}_{name}.json'
                if out.exists():
                    rows.append(json.loads(out.read_text(encoding='utf-8'))['row']); continue
                started = time.monotonic()
                groups = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs, k=8,
                                               profile=CONFIGS[name])
                retrieve_seconds = round(time.monotonic() - started, 1)
                evidence, budget = ra_retrieval.context(corpus, groups, QUESTION, u, pipe.client, 'spans',
                                                        guideline_index=pipe.guidelines)
                contract = ra_answer.schema(evidence, u['tasks'], 'spans', question=QUESTION,
                                            claim_budget=ra_answer.claim_budget(u))
                row = {'plan': n, 'config': name, 'retrieve_seconds': retrieve_seconds,
                       'top8_hit_tokens': sum(h['chunk'].get('n_tokens_with_header', 0) for g in groups for h in g['hits']),
                       'top8_gold_dates': context_dates([h['chunk'] for g in groups for h in g['hits']], units),
                       'context_gold_dates': context_dates(evidence.values(), units), 'packets': len(evidence),
                       'omitted': len(budget['omitted']), 'context_tokens': budget['estimated_prompt_and_schema_tokens'],
                       'token_budget': budget['input_limit'] - budget['review_reserved_tokens'],
                       'spans': len(ra_spans.catalog(evidence)[0]), 'schema_enum_values': enum_count(contract['schema'])}
                report = None
                if not a.no_generation:
                    pipe.client.begin_request()
                    started = time.monotonic()
                    try:
                        result = ra_answer.generate(QUESTION, u, evidence, pipe.client, 'spans')
                        calls = list(pipe.client.calls)
                    except Exception as exc:
                        result, calls = None, list(pipe.client.calls)
                        row['generation_error'] = f'{type(exc).__name__}: {exc}'
                    finally:
                        pipe.client.release()
                    if result is not None:
                        result.update(evidence=evidence)
                        report = {'results': [result], 'generation_telemetry': calls, 'understanding': u,
                                  'usage': {'input_tokens': sum(c.get('prompt_tokens', 0) for c in calls)},
                                  'seconds': round(time.monotonic() - started, 1)}
                        s = score(report, units)
                        row.update({k: s[k] for k in ('submission_dates', 'approval_dates', 'durations_computed_correctly',
                                                      'claims_kept', 'rejected_reasons', 'derived_withheld')})
                rows.append(row)
                out.write_text(json.dumps({'row': row, 'report': report}, ensure_ascii=False, indent=1, default=str),
                               encoding='utf-8')
                print(json.dumps(row, ensure_ascii=False), flush=True)
    finally:
        pipe.close()
    (a.output / 'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
