"""Re-run retrieval, reading context and generation on frozen plans from any code checkout.

Planner variance is removed by reusing the understanding stored in reference
reports; retrieval/context follow the pipeline (k=8, one bounded research round).
Run once per code root; compare outputs case by case. Context chunk IDs are saved
so identical reading contexts across code versions can be checked.

  python eval/paired_dev.py --code-root ../project_shared_service --reports <dir> --out <dir> --repeat 1
"""
import argparse, glob, json, sys, time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--code-root', type=Path, required=True)
    p.add_argument('--data-root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--reports', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--repeat', type=int, default=1)
    p.add_argument('--only')
    a = p.parse_args()
    code = a.code_root.resolve()
    sys.path.insert(0, str(code))
    from rag import ra_retrieval, ra_answer
    from rag.ra_research import extend
    from rag.openai_chat import OpenAIPipelineChat
    from rag.ra_pipeline import RAPipeline
    from preprocess.hybrid_config import load_config
    data = a.data_root.resolve()
    pipe = RAPipeline(data / 'data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release', None,
                      load_config(code / 'configs/preprocess.local.json'),
                      guideline_snapshot=data / 'data/guidelines/ra_guidelines_native_2026-10-12_v2',
                      research_rounds=1, retrieval_pages=8,
                      chat_client=OpenAIPipelineChat(model='gpt-5.6-luna', context_tokens=65536))
    a.out.mkdir(parents=True, exist_ok=True)
    try:
        pipe.warmup()
        corpus = pipe.retrieval_corpus()
        for path in sorted(glob.glob(str(a.reports / '*_1.json'))):
            ref = json.loads(Path(path).read_text(encoding='utf-8'))
            case = ref['run']['case']
            if a.only and case['id'] not in a.only.split(','):
                continue
            u, question = ref['understanding'], ref['question']
            products, jurisdictions = case.get('products'), case.get('jurisdictions')
            selected = [d for d in pipe.documents if (not products or d['product_id'] in products or d.get('role') == 'guideline')
                        and (not jurisdictions or d['jurisdiction'] in jurisdictions)]
            corpus.allowed_documents = {(d['document_id'], d['version_id']) for d in selected}
            for n in range(1, a.repeat + 1):
                out = a.out / f"{case['id']}_{n}.json"
                if out.exists():
                    continue
                groups = ra_retrieval.retrieve(corpus, json.loads(json.dumps(u)), pipe.guidelines,
                                               documents=corpus.allowed_documents, k=8)
                evidence, budget = ra_retrieval.context(corpus, groups, question, u, pipe.client, 'spans',
                                                        guideline_index=pipe.guidelines)
                evidence, budget, research = extend(corpus, groups, evidence, budget, question, u, pipe.client,
                                                    pipe.guidelines, max_rounds=1, citation_mode='spans')
                pipe.client.begin_request()
                started = time.monotonic()
                try:
                    result = ra_answer.generate(question, u, evidence, pipe.client, 'spans')
                    result.update(evidence=evidence)
                    record = {'results': [result], 'understanding': u, 'question': question, 'case': case,
                              'code_root': str(code), 'context_chunk_ids': [b.get('chunk_id') for b in evidence.values()],
                              'generation_telemetry': list(pipe.client.calls), 'seconds': round(time.monotonic() - started, 1)}
                    out.write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
                    print(case['id'], n, result['status'], len(result['claims']), flush=True)
                except Exception as exc:
                    out.with_suffix('.failure.json').write_text(json.dumps({'error': f'{type(exc).__name__}: {exc}', 'case': case},
                                                                           ensure_ascii=False, indent=1), encoding='utf-8')
                    print(case['id'], n, 'FAILED', type(exc).__name__, str(exc)[:200], flush=True)
                    if 'insufficient_quota' in str(exc):
                        return
                finally:
                    pipe.client.release()
            corpus.allowed_documents = None
    finally:
        pipe.close()


if __name__ == '__main__':
    main()
