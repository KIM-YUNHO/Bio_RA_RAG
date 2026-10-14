"""Retrieval recall and plan-stability benchmark. No provider calls.

Silver relevance: (document_id, pdf_page) cited by kept claims of reports that a
reviewer judged correct or partial (H v2 development adjudications), plus
user-judged reports given with --user-case. This is an approximation of the
pages an answer needs, not an independent gold retrieval set.

Plans: every stored first-turn planner output for the same question and UI
scope (service reports and evaluation runs). Several plans per question show how
much retrieval moves when only the planner's wording changes.

For each configuration and plan the current code runs retrieval and context
assembly (token counting only) and records:
  hit_recall        silver pages among retrieval hits
  evidence_recall   silver pages among the assembled evidence packets
  stability         mean pairwise Jaccard of hit chunk IDs across a case's plans
  tokens / omitted  estimated synthesis prompt size and chunks dropped for budget
"""
from copy import deepcopy
import argparse, glob, hashlib, itertools, json, os, statistics, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
VERDICTS = ('correct', 'partial')
TOPIC_MODES = {'0': False, '1': True, '2': 'fuse'}  # off / own first slot / consensus only


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def report_of(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return data.get('report', data)


def silver_pages(report):
    result = report['results'][0]
    evidence = result.get('evidence') or {}
    pages = set()
    for claim in result.get('claims', []):
        for source in claim.get('sources', []):
            block = evidence.get(source.get('evidence'))
            if block and block.get('product_id') is not None:
                pages.add((block['document_id'], block['pdf_page']))
    return pages


def adjudicated_cases(adjudication_glob):
    cases = {}
    for path in glob.glob(adjudication_glob):
        for row in json.loads(Path(path).read_text(encoding='utf-8'))['rows']:
            judged = []
            if 'reports' in row:
                judged = [(row.get(f'{name}_verdict'), ref['path']) for name, ref in row['reports'].items()]
            elif 'report' in row:
                judged = [(row.get('verdict') or row.get('after'), row['report'])]
            for verdict, ref in judged:
                if verdict not in VERDICTS or not (ROOT / ref).exists():
                    continue
                report = report_of(ROOT / ref)
                case = cases.setdefault(report['question'].strip(), {'id': row['case'], 'pages': set(), 'sources': []})
                case['pages'] |= silver_pages(report)
                case['sources'].append({'report': ref, 'verdict': verdict})
    return cases


def stored_plans(report_globs, questions, chunks_sha256):
    """Plans from reports run on THIS index; other indexes' document keys do not map."""
    plans = {}
    for pattern in report_globs:
        for path in glob.glob(pattern, recursive=True):
            if 'api_calls' in path or os.path.getsize(path) > 30_000_000:
                continue
            try:
                data = json.loads(Path(path).read_text(encoding='utf-8'))
            except (ValueError, UnicodeDecodeError):
                continue
            report = data.get('report', data) if isinstance(data, dict) else None
            if not isinstance(report, dict) or report.get('mode') != 'ra' or 'understanding' not in report:
                continue
            if (report.get('index') or {}).get('chunks_sha256') != chunks_sha256:
                continue
            question = report.get('question', '').strip()
            if question not in questions or (report['understanding'].get('conversation') or {}).get('turn_kind', 'first') != 'first':
                continue
            u = report['understanding']
            key = digest([u['tasks'], u['products'], u['jurisdictions'], sorted(report['scope']['selected_documents'])])
            plans.setdefault(question, {}).setdefault(key, (u, report['scope']['selected_documents']))
    return {q: list(v.values()) for q, v in plans.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', type=Path, default=ROOT / 'data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2')
    parser.add_argument('--guidelines', type=Path, default=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2')
    parser.add_argument('--adjudications', default=str(ROOT / 'data/runs/h_v2_rag_development_2026-10-14_luna_v1/*/adjudication.json'))
    parser.add_argument('--reports', action='append', help='Glob(s) of stored RA reports supplying planner outputs')
    parser.add_argument('--user-case', action='append', default=[], help='Report path judged acceptable by a user')
    parser.add_argument('--config', action='append', required=True,
                        help='name:k:topic(0 off|1 slot|2 fuse):context_tokens, e.g. base:5:0:16384')
    parser.add_argument('--max-plans', type=int, default=8)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output file')
    cases = adjudicated_cases(args.adjudications)
    for path in args.user_case:
        report = report_of(path)
        case = cases.setdefault(report['question'].strip(), {'id': 'U' + digest(path)[:4], 'pages': set(), 'sources': []})
        case['pages'] |= silver_pages(report)
        case['sources'].append({'report': str(path), 'verdict': 'user_preferred'})
    cases = {q: c for q, c in cases.items() if c['pages']}

    from rag import ra_retrieval
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config

    def blocked(self, *a, **k):
        raise RuntimeError('provider calls are not allowed in this benchmark')
    OpenAIPipelineChat.__call__ = blocked
    pipe = RAPipeline(args.run, None, load_config(ROOT / 'configs/preprocess.local.json'), 'http://127.0.0.1:1',
                      guideline_snapshot=args.guidelines, research_rounds=0,
                      chat_client=OpenAIPipelineChat(model='gpt-5.6-luna'))
    pipe.warmup()
    corpus = pipe.retrieval_corpus()
    bykey = {d['key']: (d['document_id'], d['version_id']) for d in pipe.documents}
    plans = stored_plans(args.reports or [str(ROOT / 'data/runs/**/*.json')], set(cases), pipe.index_meta['chunks_sha256'])
    cases = {q: c for q, c in cases.items() if plans.get(q)}
    rows, summary = [], {}
    try:
        for spec in args.config:
            name, k, topic, ctx = spec.split(':')
            client = OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=int(ctx))
            per_case = []
            for question, case in cases.items():
                hit_sets, recalls, ev_recalls, tokens, omitted = [], [], [], [], []
                for u, scope in plans.get(question, [])[:args.max_plans]:
                    docs = {bykey[key] for key in scope if key in bykey}
                    corpus.allowed_documents = docs
                    try:
                        groups = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=docs,
                                                       include_nondefault=False, k=int(k), topic_queries=TOPIC_MODES[topic])
                        evidence, budget = ra_retrieval.context(corpus, groups, question, deepcopy(u), client, 'spans',
                                                                guideline_index=pipe.guidelines)
                    except Exception as exc:  # older stored plans may lack fields the current code needs
                        rows.append({'config': name, 'case': case['id'], 'error': f'{type(exc).__name__}: {exc}'})
                        continue
                    hits = [h['chunk'] for g in groups for h in g['hits']]
                    hit_pages = {(c['document_id'], c['pdf_page']) for c in hits}
                    ev_pages = {(b['document_id'], b['pdf_page']) for b in evidence.values()}
                    hit_sets.append({c['chunk_id'] for c in hits})
                    recalls.append(len(case['pages'] & hit_pages) / len(case['pages']))
                    ev_recalls.append(len(case['pages'] & ev_pages) / len(case['pages']))
                    tokens.append(budget.get('estimated_prompt_and_schema_tokens', 0))
                    omitted.append(len(budget.get('omitted', [])))
                if not hit_sets:
                    continue
                pairs = [len(a & b) / len(a | b) for a, b in itertools.combinations(hit_sets, 2) if a | b]
                row = {'config': name, 'case': case['id'], 'question': question[:80], 'plans': len(hit_sets),
                       'silver_pages': len(case['pages']), 'hit_recall': round(statistics.mean(recalls), 3),
                       'evidence_recall': round(statistics.mean(ev_recalls), 3),
                       'stability': round(statistics.mean(pairs), 3) if pairs else None,
                       'tokens': round(statistics.mean(tokens)), 'omitted': round(statistics.mean(omitted), 1),
                       'input_limit': client.input_limit}
                rows.append(row)
                per_case.append(row)
            stable = [r['stability'] for r in per_case if r['stability'] is not None]
            summary[name] = {'k': int(k), 'topic_queries': TOPIC_MODES[topic], 'context_tokens': int(ctx),
                             'cases': len(per_case), 'plans': sum(r['plans'] for r in per_case),
                             'hit_recall': round(statistics.mean(r['hit_recall'] for r in per_case), 3),
                             'evidence_recall': round(statistics.mean(r['evidence_recall'] for r in per_case), 3),
                             'stability': round(statistics.mean(stable), 3) if stable else None,
                             'stability_cases': len(stable),
                             'tokens': round(statistics.mean(r['tokens'] for r in per_case)),
                             'omitted': round(statistics.mean(r['omitted'] for r in per_case), 2)}
            print(name, json.dumps(summary[name], ensure_ascii=False), flush=True)
    finally:
        pipe.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump({'silver': 'kept-claim pages of correct/partial adjudicated reports + user-preferred reports',
                   'cases': {c['id']: {'question': q, 'pages': sorted(map(list, c['pages'])), 'sources': c['sources']}
                             for q, c in cases.items()},
                   'summary': summary, 'rows': rows}, stream, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
