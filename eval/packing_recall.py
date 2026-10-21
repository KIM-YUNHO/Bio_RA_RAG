"""Does the LLM input contain the answer? Context packing with and without checklist-first admission.

Saved plans (report 'understanding') are re-retrieved with the current default profile and
packed by ra_retrieval.context twice: RA_CHECKLIST_PACKING=0 and =1. Each evidence block is
scored as if it were a claim (its own product and agency), with the same expected values as
the answer evaluation, so a score is "the answer is present in what the model reads".
No model calls.

  python eval/packing_recall.py data/runs/planner_facets_2026-10-15/e2e_final --output data/runs/packing_2026-10-16/recall.json
"""
import argparse, glob, json, os, sys, time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# name -> (RA_CHECKLIST_PACKING, k per group, retrieval profile overrides)
VARIANTS = {
    'packing': [('off', '0', 8, None), ('on', '1', 8, None)],
    'rerank': [('base_k8', '0', 8, None), ('k16', '0', 16, None),
               ('rerank_request', '0', 8, {'rerank': 'request', 'rerank_pool': 40}),
               ('rerank_ask', '0', 8, {'rerank': 'ask', 'rerank_pool': 40})],
    'rerank16': [('base_k8', '0', 8, None), ('k16', '0', 16, None),
                 ('k16_rerank_ask', '0', 16, {'rerank': 'ask', 'rerank_pool': 40})],
}


def pseudo_report(evidence):
    claims = [{'id': f'C{n}', 'text': b.get('text', ''), 'products': [b['product_id']] if b.get('product_id') else [],
               'sources': [{'evidence': key, 'quote': ''}], 'task_ids': []}
              for n, (key, b) in enumerate(evidence.items(), 1)]
    return {'results': [{'claims': claims, 'evidence': evidence, 'validation': {}, 'status': 'packed'}]}


def score(case, evidence, units):
    from eval.answer_scoring import score_question, GUARD
    from eval.prompt_variants import metrics
    report = pseudo_report(evidence)
    s = score_question(case, report, units) if case[0] in 'QG' else metrics(case, report, units)
    return s['score'], s['of']


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('reports', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--variants', choices=sorted(VARIANTS), default='packing')
    a = p.parse_args()
    from rag import ra_retrieval
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    from eval.answer_scoring import timeline_gold
    run = ROOT / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release'
    units = timeline_gold(run)
    pipe = RAPipeline(run, None, load_config(ROOT / 'configs/preprocess.local.json'),
                      guideline_snapshot=ROOT / 'data/guidelines/ra_guidelines_native_2026-10-12_v2',
                      research_rounds=0, retrieval_pages=8,
                      chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    rows, totals = [], {}
    try:
        pipe.warmup()
        corpus = pipe.retrieval_corpus()
        from rag import answer
        corpus._emb = pipe._shared_encoder or answer.Embedder('bge-m3', local_only=True)
        corpus.allowed_documents = {(d['document_id'], d['version_id']) for d in pipe.documents}
        for path in sorted(glob.glob(str(a.reports / '*_[12].json'))):
            report = json.loads(Path(path).read_text(encoding='utf-8'))
            case, question, u = Path(path).stem.split('_')[0], report['question'], report['understanding']
            row = {'report': Path(path).stem}
            for variant, flag, k, profile in VARIANTS[a.variants]:
                os.environ['RA_CHECKLIST_PACKING'] = flag
                started = time.perf_counter()
                groups = ra_retrieval.retrieve(corpus, deepcopy(u), pipe.guidelines, documents=corpus.allowed_documents,
                                               k=k, profile=profile)
                seconds = round(time.perf_counter() - started, 2)
                evidence, budget = ra_retrieval.context(corpus, deepcopy(groups), question, deepcopy(u), pipe.client,
                                                        'spans', guideline_index=pipe.guidelines)
                got, of = score(case, evidence, units)
                totals.setdefault(variant, [0, 0, 0.0])
                totals[variant][0] += got; totals[variant][1] += of; totals[variant][2] += seconds
                row[variant] = {'score': got, 'of': of, 'blocks': len(evidence), 'retrieve_seconds': seconds,
                                'tokens': budget.get('estimated_prompt_and_schema_tokens')}
            rows.append(row)
            print(row['report'], ' '.join(f"{v}={row[v]['score']}/{row[v]['of']}({row[v]['blocks']}b,{row[v]['retrieve_seconds']}s)"
                                          for v in row if v != 'report'), flush=True)
    finally:
        os.environ.pop('RA_CHECKLIST_PACKING', None)
        pipe.close()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps({'totals': totals, 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    print(totals)


if __name__ == '__main__':
    main()
