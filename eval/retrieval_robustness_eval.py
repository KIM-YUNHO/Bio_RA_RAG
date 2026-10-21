"""Retrieval robustness grid on one index. No provider calls.

Profiles vary BM25 weight in the dense+BM25 fusion, an added dense ranking for
the original (Korean) question, and stripping slot-constant words (product
names, agencies, document types) from BM25 queries.

  recall set     adjudicated/user-judged cases; silver = cited (document, page);
                 every stored first-turn plan of the question; all service docs
  stability set  planner_variance.json: several fresh plans of the same
                 question; mean pairwise Jaccard of hit chunk IDs
  gold set       --gold-pages: (document, page) targets that silver cannot
                 contain, e.g. facts only printed in non-default documents
Profile scope 'roles' keeps non-default documents only for planned source roles
(production); 'all' admits every permitted document of the product x agency slot.
Plans from other indexes are reused for their tasks only (scope = all docs),
because document keys differ between indexes while (document, page) do not.
"""
import argparse, glob, hashlib, itertools, json, os, re, statistics, sys
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
    p.add_argument('--profile', action='append', required=True,
                   help='name:bm25_weight:question(off|fuse|slot):strip(0|1)[:scope(roles|all)[:flags]] '
                        'flags: b = suppress boilerplate, d = collapse near-duplicates, n = no planner-role boost, w0.5 = role boost weight 0.5, c = correspondence searched by default, r = cross-encoder rerank of 40')
    p.add_argument('--gold-pages', type=Path, action='append', default=[], help='JSON {"cases":[{"id","question","pages":[[document_id,pdf_page],...]}]} '
                   'or fact-level {"facts":[{"key":[product,jurisdiction,...],"pages":[[document_id,pdf_page],...]}]}: '
                   'a fact counts once if any of its equivalent pages is hit, within the plan scope')
    p.add_argument('--max-plans', type=int, default=8)
    p.add_argument('--skip-stability', action='store_true', help='Recall only; no repeated fresh plans of one question')
    p.add_argument('--content-equivalent', action='store_true',
                   help='Silver: a page also counts as found when a hit repeats one of its chunks (3-gram Jaccard >= 0.8)')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        p.error('Choose a new output file')
    cases = adjudicated_cases(a.adjudications)
    for path in a.user_case:
        r = report_of(path)
        c = cases.setdefault(r['question'].strip(), {'id': 'U' + digest(path)[:4], 'pages': set(), 'sources': []})
        c['pages'] |= silver_pages(r)
    for c in cases.values():
        c['set'] = 'silver'
    for gold_file in a.gold_pages:
        for g in json.loads(gold_file.read_text(encoding='utf-8'))['cases']:
            c = cases.setdefault(g['question'].strip(), {'id': g['id'], 'pages': set(), 'sources': []})
            c.update(set='gold', id=g['id'], pages={tuple(x) for x in g.get('pages') or []} or
                     {tuple(x) for f in g['facts'] for x in f['pages']},
                     facts=[(tuple(f['key']), {tuple(x) for x in f['pages']}) for f in g.get('facts') or []],
                     fixed=g.get('scope') == 'fixed', template=g.get('template'), location=g.get('location'))
    cases = {q: c for q, c in cases.items() if c['pages']}
    variance = [] if a.skip_stability else json.loads(a.variance.read_text(encoding='utf-8'))['results']
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

    from rag.ra import ra_retrieval
    from rag.llm.openai_chat import OpenAIPipelineChat
    from rag.ra.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    OpenAIPipelineChat.__call__ = lambda self, *x, **y: (_ for _ in ()).throw(RuntimeError('no provider calls'))
    pipe = RAPipeline(a.run, None, load_config(ROOT / 'configs/preprocess.local.json'),
                      guideline_snapshot=a.guidelines, research_rounds=0, chat_client=OpenAIPipelineChat(model='gpt-5.6-luna'))
    pipe.warmup()
    corpus = pipe.retrieval_corpus()
    docs = {(d['document_id'], d['version_id']) for d in pipe.documents}
    corpus.allowed_documents = docs

    def run(u, profile, scope='roles'):
        g = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs, include_nondefault=scope == 'all',
                                  k=a.k, profile=profile)
        return [h['chunk'] for x in g for h in x['hits']]

    same = ra_retrieval.near_duplicate(corpus)
    by_page = {}
    for i, chunk in enumerate(corpus.chunks):
        by_page.setdefault((chunk['document_id'], chunk['pdf_page']), []).append(i)

    def found_pages(c, hits):
        pages = {(h['document_id'], h['pdf_page']) for h in hits}
        if not a.content_equivalent or c.get('facts'):
            return pages
        index = {h['chunk_id']: i for i, h in ((i, corpus.chunks[i]) for p in c['pages'] for i in by_page.get(p, []))}
        hit_ids = [i for h in hits for i in by_page.get((h['document_id'], h['pdf_page']), []) if corpus.chunks[i]['chunk_id'] == h['chunk_id']]
        for p in c['pages'] - pages:
            if any(same(g, h) for g in by_page.get(p, []) for h in hit_ids):
                pages.add(p)
        return pages

    def jaccard(sets):
        pairs = [len(x & y) / len(x | y) for x, y in itertools.combinations(sets, 2) if x | y]
        return statistics.mean(pairs) if pairs else None

    summary, rows = {}, []
    try:
        for spec in a.profile:
            name, w, qq, strip, *rest = spec.split(':')
            scope = rest[0] if rest else 'roles'
            flags = rest[1] if len(rest) > 1 else ''
            if scope not in ('roles', 'all'):
                p.error('scope must be roles or all')
            profile = {'bm25_weight': float(w), 'question_query': QQ[qq], 'strip_slot_terms': strip == '1',
                       'suppress_boilerplate': 'b' in flags, 'collapse_duplicates': 'd' in flags,
                       'role_boost': 0.0 if 'n' in flags else float(m[1]) if (m := re.search(r'w([0-9.]+)', flags)) else 1.0,
                       'extra_default_groups': ('correspondence',) if 'c' in flags else (),
                       'rerank': 'both' if 'r' in flags else None}
            recalls, stabs_a, stabs_b, by_set = [], [], [], {}
            for q, c in cases.items():
                hit_sets, rec = [], []
                for u in plans[q][:a.max_plans]:
                    hits = run(u, profile, scope)
                    pages = found_pages(c, hits)
                    if c.get('facts'):
                        # 'fixed': the question's intended product/agency; a plan that drops it is a miss.
                        own = [f for k, f in c['facts'] if c.get('fixed') or (k[0] in u['products'] and k[1] in u['jurisdictions'])]
                        rec.append(sum(bool(f & pages) for f in own) / len(own))
                    else:
                        rec.append(len(c['pages'] & pages) / len(c['pages']))
                    hit_sets.append({h['chunk_id'] for h in hits})
                recalls.append(statistics.mean(rec))
                by_set.setdefault(c['set'], []).append(statistics.mean(rec))
                if c.get('location'):
                    by_set.setdefault('gold_' + c['location'], []).append(statistics.mean(rec))
                    by_set.setdefault('template:' + c['template'], []).append(statistics.mean(rec))
                j = jaccard(hit_sets)
                if j is not None:
                    stabs_a.append(j)
                rows.append({'profile': name, 'set': 'recall', 'recall_set': c['set'], 'case': c['id'], 'plans': len(rec),
                             'recall': round(statistics.mean(rec), 3), 'stability': None if j is None else round(j, 3)})
            for q, variants in stability_sets.items():
                j = jaccard([{h['chunk_id'] for h in run(u, profile, scope)} for u in variants])
                stabs_b.append(j)
                rows.append({'profile': name, 'set': 'stability', 'question': q[:50], 'plans': len(variants), 'stability': round(j, 3)})
            summary[name] = {**profile, 'scope': scope, 'recall': round(statistics.mean(recalls), 3), 'recall_cases': len(recalls),
                             'recall_by_set': {k: {'recall': round(statistics.mean(v), 3), 'cases': len(v)} for k, v in by_set.items()},
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
