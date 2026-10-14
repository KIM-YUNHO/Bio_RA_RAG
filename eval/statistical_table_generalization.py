"""Statistical-table retrieval generalization checks with zero model calls.

  python eval/statistical_table_generalization.py --label 4.9.4
  python eval/statistical_table_generalization.py --label 4.9.3 --code-root ../biosimilar_collection
  python eval/statistical_table_generalization.py --label 4.9.4-off --disable-module
  python eval/statistical_table_generalization.py --compare 4.9.3 4.9.4 4.9.4-off

1. activation: per product x agency scope, the closed tables matching generic
   descriptive-statistic requests. No question, document or page IDs.
2. frozen_planner: saved development reports' planner tasks and queries are
   retrieved again on one index; only the retrieval code differs by label.
3. wording: the same frozen queries with reworded task requests, which only
   the statistical-table module reads.
Target tables (document + printed caption) score the output; they are never
retrieval input. The local bge-m3 encoder embeds queries; no LLM is called.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

SHARED = Path(__file__).resolve().parents[1]
RUNS = SHARED / 'data/runs/h_v2_rag_development_2026-10-14_luna_v1'
INDEX = SHARED / 'data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/embedded/release_final/candidate'
OUT = SHARED / 'data/runs/statistical_table_generalization_2026-10-14'
K = (5, 12)  # main retrieval pages; research supplement for table_statistics

GENERIC = {
    'cmax_mean_sd': 'Cmax 산술평균과 표준편차',
    'aucinf_mean_sd': 'AUCinf 산술평균과 표준편차',
    'auclast_mean_sd': 'AUClast 산술평균과 표준편차',
    'tmax_median_range': 'Tmax 중앙값과 범위',
    'thalf_mean_sd': 't1/2 평균과 표준편차',
    'cmax_geometric_mean': 'Cmax 기하평균',
    'aucinf_geometric_mean': 'AUCinf 기하평균',
}
# Scoring only: the adjudicated source tables of the development table cases.
TARGETS = {'T01': ('EMA:63563', 'Table 8.'), 'T03': ('EMA:63563', 'Table 8.'),
           'T02': ('EMA:64196', 'Table 4.'), 'T04': ('EMA:64196', 'Table 4.')}
WORDING = {
    'T01': ['EMA SB17-1001 PK 분석표에서 SB17·EU Stelara·US Stelara의 Cmax를 확인해줘. 각 군의 산술평균과 표준편차를 원문 단위와 함께 정리하고 중앙값이나 기하평균과 구분해줘.',
            'EMA SB17-1001 PK 분석표에서 산술평균과 표준편차로 SB17·EU Stelara·US Stelara의 최고혈중농도를 정리해줘. 기하평균이 아닌 값이어야 해.',
            'From the EMA SB17-1001 PK table, give the arithmetic mean and SD of Cmax for SB17, EU Stelara and US Stelara, not the median or geometric mean.'],
    'T02': ['EMA ABP 654의 Study 20190230 PK 요약표에서 ABP 654군의 Tmax와 반감기를 확인해줘. Tmax는 중앙값·범위, 반감기는 평균·표준편차로 단위와 함께 정리해줘.',
            'In the EMA ABP 654 Study 20190230 PK summary table, report the median and range of Tmax and the mean and SD of terminal half-life for the ABP 654 group.'],
    'T03': ['EMA SB17-1001 PK 분석표에서 각 군의 Cmax 중앙값만 ng/mL 단위로 알려줘. 산술평균은 필요 없어.',
            'Median Cmax (ng/mL) for SB17, EU Stelara and US Stelara in the EMA SB17-1001 PK table, as opposed to the arithmetic mean.'],
    'T04': ['EMA ABP 654 Study 20190230 PK 요약표에서 US ustekinumab군과 EU ustekinumab군의 t½ 평균·표준편차와 Tmax 중앙값·범위를 시간 단위로 확인해줘.',
            'For the US and EU ustekinumab groups in the EMA ABP 654 Study 20190230 PK summary table, give the median and range of Tmax and the mean and SD of t1/2 in hours.'],
}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_target(case, chunk):
    document, label = TARGETS.get(case, (None, None))
    return (document is not None and chunk['document_id'] == document
            and chunk.get('structure', {}).get('kind') == 'table_candidate'
            and (chunk.get('structure', {}).get('caption') or '').startswith(label))


def run(args):
    root = Path(args.code_root).resolve()
    sys.path.insert(0, str(root))
    from rag import answer, ra_retrieval, ra_statistical_tables
    from rag.search import filter_idx
    from rag.ra_sources import permitted
    from rag.service_scope import allowed
    if args.disable_module:
        ra_statistical_tables.hits = lambda *a, **k: []
    corpus = answer.Corpus('bge-m3', INDEX, local_only=True, shared_cache=True)
    chunks = corpus.chunks
    eligible = [int(i) for i in filter_idx(chunks, include_nondefault=True)
                if allowed(chunks[int(i)]) and permitted(chunks[int(i)].get('document_metadata') or {})]
    default_ids = set(int(i) for i in filter_idx(chunks))
    scopes = sorted({(chunks[i]['product_id'], chunks[i]['jurisdiction']) for i in eligible if chunks[i]['product_id']})

    activation = {}
    for name, request in GENERIC.items():
        rows = {}
        for product, agency in scopes:
            idx = [i for i in eligible if i in default_ids and chunks[i]['product_id'] == product
                   and chunks[i]['jurisdiction'] == agency]
            found = ra_statistical_tables.hits(request, idx, chunks, limit=10**6)
            if found:
                rows[product + '/' + agency] = [{'document_id': chunks[i]['document_id'], 'pdf_page': chunks[i].get('pdf_page'),
                    'caption': (chunks[i].get('structure', {}).get('caption') or '')[:120]} for _, i in found]
        activation[name] = {'request': request, 'requested': sorted(map(list, ra_statistical_tables.requested(request))),
                            'tables': sum(len(v) for v in rows.values()), 'scopes': len(rows), 'by_scope': rows}

    def replay(understanding, case):
        result = {}
        for k in K:
            groups = ra_retrieval.retrieve(corpus, understanding, None, k=k)
            result[str(k)] = [{'task_id': g['task_id'], 'product': g['product'], 'jurisdiction': g['jurisdiction'],
                'hits': [h['chunk']['chunk_id'] for h in g['hits']],
                'target_rank': next((n for n, h in enumerate(g['hits'], 1) if is_target(case, h['chunk'])), None)}
                for g in groups if g['product'] is not None]
        return result

    frozen = {}
    for phase in ('paired', 'table_focus'):
        for path in sorted((RUNS / phase / 'v2').glob('*.json')):
            report = read(path)
            frozen[phase + '/' + path.stem] = {'case': path.stem, 'replay': replay(report['understanding'], path.stem),
                'requested': [sorted(map(list, ra_statistical_tables.requested(t['request'])))
                              for t in report['understanding']['tasks']]}

    wording = {}
    for case, variants in WORDING.items():
        base = read(RUNS / 'table_focus/v2' / (case + '.json'))['understanding']
        product_tasks = [t for t in base['tasks'] if set(t['source_roles']) - {'guideline'}]
        assert len(product_tasks) == 1, case
        for n, request in enumerate(variants, 1):
            understanding = {**base, 'tasks': [{**t, 'request': request} if t is product_tasks[0] else t for t in base['tasks']]}
            wording[f'{case}.{n}'] = {'case': case, 'request': request,
                'requested': sorted(map(list, ra_statistical_tables.requested(request))),
                'replay': replay(understanding, case)}

    save(OUT / (args.label + '.json'), {
        'created_utc': datetime.now(timezone.utc).isoformat(), 'label': args.label, 'code_root': str(root),
        'module_disabled': bool(args.disable_module), 'model_calls': 0,
        'module_version': getattr(ra_statistical_tables, 'VERSION', None), 'retrieval_version': ra_retrieval.VERSION,
        'code_sha256': {f: sha(root / f) for f in ('rag/ra_statistical_tables.py', 'rag/ra_retrieval.py')},
        'index': {'directory': str(INDEX), 'chunks_sha256': corpus.index_meta['chunks_sha256'], 'chunks': len(chunks)},
        'k': list(K), 'activation': activation, 'frozen_planner': frozen, 'wording': wording})
    print(json.dumps({'label': args.label, 'activation_tables': {n: a['tables'] for n, a in activation.items()}}, ensure_ascii=False))


def compare(labels):
    data = {label: read(OUT / (label + '.json')) for label in labels}
    first = data[labels[0]]
    summary = {'labels': labels, 'activation': {}, 'targets': {}, 'wording': {}, 'unchanged_groups': {}}
    for name in first['activation']:
        summary['activation'][name] = {l: {'tables': d['activation'][name]['tables'], 'scopes': d['activation'][name]['scopes']}
                                       for l, d in data.items()}
    for key, item in first['frozen_planner'].items():
        if item['case'] in TARGETS:
            summary['targets'][key] = {l: {k: [g['target_rank'] for g in d['frozen_planner'][key]['replay'][k]] for k in map(str, K)}
                                       for l, d in data.items()}
    for key, item in first['wording'].items():
        summary['wording'][key] = {'request': item['request'], **{l: {
            'requested': d['wording'][key]['requested'],
            **{k: [g['target_rank'] for g in d['wording'][key]['replay'][k]] for k in map(str, K)}} for l, d in data.items()}}
    # Questions outside the module's scope must keep the same retrieved hits.
    for l in labels[1:]:
        same = total = 0
        changed = []
        for key, item in first['frozen_planner'].items():
            for k in map(str, K):
                for a, b in zip(item['replay'][k], data[l]['frozen_planner'][key]['replay'][k]):
                    total += 1
                    if a['hits'] == b['hits']:
                        same += 1
                    else:
                        changed.append(f'{key}@k{k}')
        summary['unchanged_groups'][f'{labels[0]} vs {l}'] = {'same': same, 'total': total, 'changed': sorted(set(changed))}
    save(OUT / ('compare_' + '_vs_'.join(labels) + '.json'), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--label')
    p.add_argument('--code-root', default=str(SHARED))
    p.add_argument('--disable-module', action='store_true')
    p.add_argument('--compare', nargs='+')
    args = p.parse_args()
    if args.compare:
        compare(args.compare)
    elif args.label:
        run(args)
    else:
        p.error('--label or --compare is required')


if __name__ == '__main__':
    main()
