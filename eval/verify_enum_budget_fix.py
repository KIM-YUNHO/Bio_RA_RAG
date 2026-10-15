#!/usr/bin/env python3
"""Read-only: does the enum-aware ra_answer.cost() keep the saved failed requests under the limit?

For each saved failed job (it carries `diagnostics.evidence`, the blocks that were about to be
sent) the evidence blocks are added one by one in their stored order, the way
rag/ra_retrieval.py context().add() does. A block is skipped when the answer schema built from
(kept + block) is priced over the input limit by ra_answer.cost().

Output has the same layout as eval/diagnose_enum_overflow.py, for "before" and for "after".
Token pricing is switched off on purpose (stub tokenizer): this isolates the enum guard.
No model is called, no network is used, nothing is written.

    .venv\\Scripts\\python.exe -X utf8 eval/verify_enum_budget_fix.py
    .venv\\Scripts\\python.exe -X utf8 eval/verify_enum_budget_fix.py path\\to\\job.json
"""
import argparse
import collections
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag import ra_answer                                   # noqa: E402
from rag.ra_spans import catalog                            # noqa: E402
from rag.schema_budget import enum_count, MAX_ENUM_VALUES   # noqa: E402

DEFAULT_GLOB = str(ROOT / 'data/runs/qa_eval/allscope_r1_2026-10-15/responses/CHEMR_Q*.json')


class ZeroTokens:
    def messages(self, msgs):
        return 0

    def one(self, text):
        return 0


class Client:
    input_limit = 62280
    tokenizer = ZeroTokens()


def schema_for(blocks, question):
    return ra_answer.schema(blocks, [{'id': 'T1'}], 'spans', question=question)['schema']


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


def keep_what_fits(evidence, question):
    kept = {}
    for key, block in evidence.items():
        trial = dict(kept)
        trial[key] = block
        if ra_answer.cost(Client, [], {'schema': schema_for(trial, question)}) > Client.input_limit:
            continue                      # context().add() skips an over-budget candidate and goes on
        kept = trial
    return kept


def describe(label, blocks, question):
    schema = schema_for(blocks, question)
    spans, _ = catalog(blocks)
    total = enum_count(schema)
    print(f'  [{label}]')
    print(f'    evidence blocks   : {len(blocks)}')
    print(f'    citable span ids  : {len(spans)}')
    print(f'    enum values total : {total}   (limit {MAX_ENUM_VALUES})  ->',
          'OVER' if total > MAX_ENUM_VALUES else 'ok')
    for size, where in largest_enums(schema):
        print(f'        {size:5d}  {where}')
    print('    blocks by agency  :', dict(collections.Counter(b.get('jurisdiction') for b in blocks.values())))
    print('    blocks by product :', dict(collections.Counter(b.get('product_id') for b in blocks.values())))
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('jobs', nargs='*')
    args = parser.parse_args()
    paths = args.jobs or sorted(glob.glob(DEFAULT_GLOB))
    ok = total_jobs = 0
    for path in paths:
        job = json.loads(Path(path).read_text(encoding='utf-8'))
        evidence = (job.get('diagnostics') or {}).get('evidence')
        if not evidence:
            continue
        question = job['question']
        print('=' * 78)
        print(Path(path).name)
        print('question          :', question[:70])
        print('error (before)    :', (job.get('error') or '')[:80])
        describe('before the fix: every block is sent', evidence, question)
        kept = keep_what_fits(evidence, question)
        after = describe('after the fix: enum-aware cost()', kept, question)
        print(f'  blocks dropped    : {len(evidence) - len(kept)} of {len(evidence)}')
        total_jobs += 1
        ok += after <= MAX_ENUM_VALUES
    print('=' * 78)
    print(f'{ok} of {total_jobs} saved failures end under the limit of {MAX_ENUM_VALUES} enum values.')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
