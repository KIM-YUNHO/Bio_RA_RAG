#!/usr/bin/env python3
"""Read-only check of the "Structured output schema exceeds 1000 enum values" failure.

For a saved failed service job (it carries `diagnostics.evidence`, the evidence blocks
that were about to be sent) this rebuilds the answer schema with the service's own
code and counts the enum values, so you can see for yourself that:

  * the schema is over the limit (rag/schema_budget.py: MAX_ENUM_VALUES),
  * almost all enum values are the citation span IDs (rag/ra_answer.py: schema()),
  * how many blocks would fit if evidence were added one by one until the limit.

No model is called, no network is used, nothing is written.

    .venv\\Scripts\\python.exe -X utf8 eval/diagnose_enum_overflow.py
    .venv\\Scripts\\python.exe -X utf8 eval/diagnose_enum_overflow.py path\\to\\job.json [more.json ...]
"""
import argparse
import collections
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.ra import ra_answer                       # noqa: E402  schema() builds the model's output format
from rag.ra.ra_spans import catalog                # noqa: E402  evidence blocks -> citable span ids
from rag.evidence.schema_budget import enum_count, MAX_ENUM_VALUES  # noqa: E402

DEFAULT_GLOB = str(ROOT / 'data/runs/qa_eval/allscope_r1_2026-10-15/responses/CHEMR_Q*.json')


def load_failed_job(path):
    job = json.loads(Path(path).read_text(encoding='utf-8'))
    diagnostics = job.get('diagnostics')
    if not isinstance(diagnostics, dict) or not diagnostics.get('evidence'):
        return None
    return job['question'], diagnostics['evidence'], job.get('error', '')


def schema_for(evidence, question):
    # The failing call used one task (T1) in span-citation mode.
    return ra_answer.schema(evidence, [{'id': 'T1'}], 'spans', question=question)['schema']


def largest_enums(schema, top=3):
    found = []

    def walk(node, path):
        if isinstance(node, dict):
            if 'enum' in node:
                found.append((len(node['enum']), path))
            for key, value in node.items():
                if key != 'enum':
                    walk(value, f'{path}/{key}')
        elif isinstance(node, list):
            for value in node:
                walk(value, path)
    walk(schema, '')
    return sorted(found, reverse=True)[:top]


def blocks_that_fit(evidence, question):
    """Add blocks in their stored order until the enum limit would be exceeded."""
    kept = {}
    for key, block in evidence.items():
        trial = dict(kept)
        trial[key] = block
        if enum_count(schema_for(trial, question)) > MAX_ENUM_VALUES:
            break
        kept = trial
    return len(kept)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('jobs', nargs='*', help='saved failed job JSON files (default: the all-scope evaluation run)')
    args = parser.parse_args()
    paths = args.jobs or sorted(glob.glob(DEFAULT_GLOB))
    shown = 0
    for path in paths:
        loaded = load_failed_job(path)
        if loaded is None:
            continue
        question, evidence, error = loaded
        schema = schema_for(evidence, question)
        spans, _ = catalog(evidence)
        total = enum_count(schema)
        shown += 1
        print('=' * 78)
        print(Path(path).name)
        print('question          :', question[:70])
        print('error             :', error[:80])
        print('evidence blocks   :', len(evidence))
        print('citable span ids  :', len(spans))
        print(f'enum values total : {total}   (limit {MAX_ENUM_VALUES})  ->', 'OVER' if total > MAX_ENUM_VALUES else 'ok')
        for size, where in largest_enums(schema):
            print(f'   {size:5d}  {where}')
        by_juris = collections.Counter(b.get('jurisdiction') for b in evidence.values())
        by_product = collections.Counter(b.get('product_id') for b in evidence.values())
        print('blocks by agency  :', dict(by_juris))
        print('blocks by product :', dict(by_product))
        fit = blocks_that_fit(evidence, question)
        print(f'blocks that fit under the limit (stored order): {fit} of {len(evidence)}')
    if not shown:
        print('No failed job with diagnostics.evidence found in:', paths[:1] or DEFAULT_GLOB)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
