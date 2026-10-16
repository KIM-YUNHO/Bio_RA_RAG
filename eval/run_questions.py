"""Run fixed questions through RAPipeline from a given code checkout; save reports only.

The code root is put first on sys.path so main, a service worktree and a branch can
be compared with the same data, settings (service defaults) and question file.
Grading is separate. Existing report files are never re-run.

  python eval/run_questions.py --code-root ../project_shared_service --cases <cases.json> --out <dir> [--repeat 2 --only Q1,Q2]
"""
import argparse, json, sys, time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--code-root', type=Path, required=True)
    p.add_argument('--data-root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--repeat', type=int, default=1)
    p.add_argument('--only', help='comma-separated case IDs')
    p.add_argument('--extra-default', default='', help='comma-separated doc groups searched by default')
    p.add_argument('--planner-inventory', action='store_true', help='show the planner the document list')
    p.add_argument('--retrieval-pages', type=int, default=8, help='candidates kept per task x product x agency')
    p.add_argument('--rerank', choices=['request', 'queries', 'both', 'ask', 'fquery', 'request_unit', 'ask_unit', 'fquery_unit'],
                   help='local reranker query')
    p.add_argument('--rerank-fuse', action='store_true', help='combine hybrid and reranker ranks (RRF)')
    p.add_argument('--rerank-pool', type=int, default=40)
    a = p.parse_args()
    if a.planner_inventory:
        import os
        os.environ['RA_PLANNER_INVENTORY'] = '1'
    profile = {'extra_default_groups': tuple(g for g in a.extra_default.split(',') if g)} if a.extra_default else {}
    if a.rerank:
        profile.update(rerank=a.rerank, rerank_pool=a.rerank_pool, rerank_fuse=a.rerank_fuse)
    profile = profile or None
    code = a.code_root.resolve()
    sys.path.insert(0, str(code))
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    data = a.data_root.resolve()
    cases = json.loads(a.cases.read_text(encoding='utf-8'))
    if a.only:
        cases = [c for c in cases if c['id'] in a.only.split(',')]
    pipe = RAPipeline(data / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release', None,
                      load_config(code / 'configs/preprocess.local.json'), 'http://127.0.0.1:8886',
                      guideline_snapshot=data / 'data/guidelines/ra_guidelines_native_2026-10-12_v2',
                      research_rounds=1, retrieval_pages=a.retrieval_pages, retrieval_profile=profile,
                      chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    a.out.mkdir(parents=True, exist_ok=True)
    try:
        pipe.warmup()
        for case in cases:
            for n in range(1, a.repeat + 1):
                out = a.out / f"{case['id']}_{n}.json"
                if out.exists() or out.with_suffix('.failure.json').exists():
                    continue
                started = time.monotonic()
                try:
                    report = pipe.ask(case['question'], case.get('products'), case.get('jurisdictions'))
                    report['run'] = {'code_root': str(code), 'case': case, 'wall_seconds': round(time.monotonic() - started, 1)}
                    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
                    print(case['id'], n, report['results'][0]['status'], round(time.monotonic() - started, 1), flush=True)
                except Exception as exc:
                    out.with_suffix('.failure.json').write_text(json.dumps(
                        {'error': f'{type(exc).__name__}: {exc}', 'case': case, 'failure': pipe.last_failure},
                        ensure_ascii=False, indent=1, default=str), encoding='utf-8')
                    print(case['id'], n, 'FAILED', type(exc).__name__, str(exc)[:200], flush=True)
                    if 'insufficient_quota' in str(exc) or 'credit' in str(exc).lower():
                        print('API credit exhausted; stopping', flush=True)
                        return
    finally:
        pipe.close()


if __name__ == '__main__':
    main()
