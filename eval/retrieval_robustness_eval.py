"""Retrieval robustness grid on one index. No provider calls.

Profiles vary BM25 weight in the dense+BM25 fusion, an added dense ranking for
the original (Korean) question, and stripping slot-constant words (product
names, agencies, document types) from BM25 queries.

  recall set     adjudicated/user-judged cases; silver = cited (document, page);
                 every stored first-turn plan of the question; all service docs
  stability set  planner_variance.json: several fresh plans of the same
                 question; mean pairwise Jaccard of hit chunk IDs
Plans from other indexes are reused for their tasks only (scope = all docs),
because document keys differ between indexes while (document, page) do not.
"""
import argparse, glob, hashlib, itertools, json, os, statistics, sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.retrieval_stability_eval import adjudicated_cases, report_of, silver_pages  # noqa: E402

QQ = {'off': None, 'fuse': 'fuse', 'slot': 'slot'}


def digest(v):
    return hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def stored(report_globs, questions):
    plans = {}
    for pattern in report_globs:
        for path in glob.glob(pattern, recursive=True):
            if 'api_calls' in path or os.path.getsize(path) > 30_000_000:
                continue
            try:
                data = json.loads(Path(path).read_text(encoding='utf-8'))
            except (ValueError, UnicodeDecodeError):
                continue
            r = data.get('report', data) if isinstance(data, dict) else None
            if not isinstance(r, dict) or r.get('mode') != 'ra' or 'understanding' not in r:
                continue
            q = r.get('question', '').strip()
            if q not in questions or (r['understanding'].get('conversation') or {}).get('turn_kind', 'first') != 'first':
                continue
            u = deepcopy(r['understanding'])
            u.setdefault('source_question', q)
            if not u.get('products') or not u.get('jurisdictions'):
                continue
            plans.setdefault(q, {}).setdefault(digest([u['tasks'], u['products'], u['jurisdictions']]), u)
    return {q: list(v.values()) for q, v in plans.items()}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', type=Path, default=ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release')
    p.add_argument('--guidelines', type=Path, default=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2')
    p.add_argument('--adjudications', default=str(ROOT / 'data/runs/h_v2_rag_development_2026-10-14_luna_v1/*/adjudication.json'))
    p.add_argument('--reports', action='append')
    p.add_argument('--user-case', action='append', default=[])
    p.add_argument('--variance', type=Path, default=ROOT / 'data/runs/retrieval_stability_2026-10-14/planner_variance.json')
    p.add_argument('--k', type=int, default=8)
    p.add_argument('--profile', action='append', required=True, help='name:bm25_weight:question(off|fuse|slot):strip(0|1)')
    p.add_argument('--max-plans', type=int, default=8)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        p.error('Choose a new output file')
    cases = adjudicated_cases(a.adjudications)
    for path in a.user_case:
        r = report_of(path)
        c = cases.setdefault(r['question'].strip(), {'id': 'U' + digest(path)[:4], 'pages': set(), 'sources': []})
        c['pages'] |= silver_pages(r)
    cases = {q: c for q, c in cases.items() if c['pages']}
    variance = json.loads(a.variance.read_text(encoding='utf-8'))['results']
    var_questions = {v['summary']['question'] for v in variance}
    reports = a.reports or [str(ROOT / 'data/runs/**/*.json')]
    plans = stored(reports, set(cases) | var_questions)
    cases = {q: c for q, c in cases.items() if plans.get(q)}
    stability_sets = {}
    for v in variance:
        q = v['summary']['question']
        if not plans.get(q):
            continue
        template = plans[q][0]
        variants = []
        for run in v['runs']:
            if 'error' in run:
                continue
            u = deepcopy(template)
            u['tasks'] = [{**t, 'id': f'T{n}', 'guideline_request_quote': ''} for n, t in enumerate(run['tasks'], 1)]
            u['source_question'] = q
            variants.append(u)
        stability_sets[q] = variants

    from rag import ra_retrieval
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    OpenAIPipelineChat.__call__ = lambda self, *x, **y: (_ for _ in ()).throw(RuntimeError('no provider calls'))
    pipe = RAPipeline(a.run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      guideline_snapshot=a.guidelines, research_rounds=0, chat_client=OpenAIPipelineChat(model='gpt-5.6-luna'))
    pipe.warmup()
    corpus = pipe.retrieval_corpus()
    docs = {(d['document_id'], d['version_id']) for d in pipe.documents}
    corpus.allowed_documents = docs

    def run(u, profile):
        g = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs, include_nondefault=False,
                                  k=a.k, profile=profile)
        return [h['chunk'] for x in g for h in x['hits']]

    def jaccard(sets):
        pairs = [len(x & y) / len(x | y) for x, y in itertools.combinations(sets, 2) if x | y]
        return statistics.mean(pairs) if pairs else None

    summary, rows = {}, []
    try:
        for spec in a.profile:
            name, w, qq, strip = spec.split(':')
            profile = {'bm25_weight': float(w), 'question_query': QQ[qq], 'strip_slot_terms': strip == '1'}
            recalls, stabs_a, stabs_b = [], [], []
            for q, c in cases.items():
                hit_sets, rec = [], []
                for u in plans[q][:a.max_plans]:
                    hits = run(u, profile)
                    pages = {(h['document_id'], h['pdf_page']) for h in hits}
                    rec.append(len(c['pages'] & pages) / len(c['pages']))
                    hit_sets.append({h['chunk_id'] for h in hits})
                recalls.append(statistics.mean(rec))
                j = jaccard(hit_sets)
                if j is not None:
                    stabs_a.append(j)
                rows.append({'profile': name, 'set': 'recall', 'case': c['id'], 'plans': len(rec),
                             'recall': round(statistics.mean(rec), 3), 'stability': None if j is None else round(j, 3)})
            for q, variants in stability_sets.items():
                j = jaccard([{h['chunk_id'] for h in run(u, profile)} for u in variants])
                stabs_b.append(j)
                rows.append({'profile': name, 'set': 'stability', 'question': q[:50], 'plans': len(variants), 'stability': round(j, 3)})
            summary[name] = {**profile, 'recall': round(statistics.mean(recalls), 3), 'recall_cases': len(recalls),
                             'stability_stored_plans': round(statistics.mean(stabs_a), 3) if stabs_a else None,
                             'stability_fresh_plans': round(statistics.mean(stabs_b), 3) if stabs_b else None,
                             'stability_fresh_by_question': [round(x, 3) for x in stabs_b]}
            print(name, json.dumps(summary[name], ensure_ascii=False), flush=True)
    finally:
        pipe.close()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x', encoding='utf-8') as stream:
        json.dump({'index': str(a.run), 'k': a.k, 'summary': summary, 'rows': rows,
                   'stability_questions': list(stability_sets)}, stream, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
