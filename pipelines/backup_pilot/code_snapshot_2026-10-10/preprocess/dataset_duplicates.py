"""Offline cross-source page duplicate candidates and a development page plan.

Native text and large raster hashes are candidate signals, not expert equivalence.
Vector figure equivalence and changed axis/cell semantics are not certified here.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from .dataset import load, rows, verify, write_rows
from .ingest import sha256
from .inventory import write_json
from .visual_evaluation import duplicate_pairs, fingerprint_pdf


def build(dataset, catalog, cached_fingerprints, out):
    dataset, catalog, out = Path(dataset), Path(catalog), Path(out)
    if out.exists():
        raise ValueError('Duplicate audit output exists; use a separate version directory')
    verify(dataset)
    summary = load(dataset/'dataset.json')
    root = Path(summary['source_root'])
    target = {d['document_id']: d for d in rows(dataset/'catalog.jsonl')}
    queue = rows(dataset/'development_delta_catalog.jsonl')
    documents = [d for d in rows(catalog) if d.get('kind') == 'pdf']
    cached = defaultdict(list)
    if cached_fingerprints:
        for p in rows(cached_fingerprints):
            cached[(p['document_id'], p['version_id'])].append(p)
    fingerprints, by_sha, source_hashes, reused = [], {}, {}, 0
    for i, d in enumerate(documents):
        path = root/d['raw_path']
        source_hashes[str(path)] = sha256(path)
        if source_hashes[str(path)] != d['sha256']:
            raise ValueError(f'Duplicate audit source changed: {d["document_id"]}')
        old = cached.get((d['document_id'], d['version_id'])) or by_sha.get(d['sha256'])
        if old:
            own = [{**p, 'document_id': d['document_id']} for p in old]
            reused += len(own)
        else:
            own = fingerprint_pdf(d, root)
        by_sha[d['sha256']] = own
        fingerprints.extend(own)
        if (i+1) % 10 == 0:
            print(f'Duplicate fingerprints: {i+1}/{len(documents)} PDF records', flush=True)
    candidates = duplicate_pairs(fingerprints)
    locked_ids = {key for key, d in target.items() if not d['model_execution_allowed']}
    queue_ids = {d['document_id'] for d in queue}
    quarantined, relevant = defaultdict(set), []
    for pair in candidates:
        a, b = pair['a'], pair['b']
        if a[0] not in target and b[0] not in target:
            continue
        relevant.append(pair)
        if a[0] in queue_ids and b[0] in locked_ids:
            quarantined[a[0]].add(a[1])
        if b[0] in queue_ids and a[0] in locked_ids:
            quarantined[b[0]].add(b[1])
    pages = {d['document_id']: [n for n in range(1, d['page_count']+1) if n not in quarantined[d['document_id']]] for d in queue}
    pages = {k: v for k, v in pages.items() if v}
    out.mkdir(parents=True)
    write_rows(out/'page_fingerprints.jsonl', fingerprints)
    write_json(out/'duplicate_candidates.json', relevant)
    report = {'pdf_records': len(documents), 'fingerprinted_pages': len(fingerprints), 'cached_fingerprint_pages': reused,
        'target_related_candidate_pairs': len(relevant),
        'signals': dict(Counter(s['method'] for p in relevant for s in p['signals'])),
        'development_pages_quarantined': sum(len(v) for v in quarantined.values()),
        'development_pages_planned': sum(len(v) for v in pages.values()),
        'quarantine_notice': 'Conservative page exclusion on duplicate signals with a locked source. Not adjudicated clinical equivalence.',
        'scope_notice': 'Native-text/large-raster duplicate candidates only. No OCR/VLM, image viewing, vector matching or semantic validation.',
        'strict_holdout_ready': False}
    write_json(out/'summary.json', report)
    plan = {'version': 'dataset-page-plan-1', 'dataset_seal_sha256': sha256(dataset/'seal.json'),
        'catalog_path': str(catalog.resolve()), 'catalog_sha256': sha256(catalog),
        'source_hashes': source_hashes, 'pages_by_document': pages,
        'quarantined_pages': {k: sorted(v) for k, v in quarantined.items() if v},
        'cached_fingerprints_sha256': sha256(cached_fingerprints) if cached_fingerprints else None,
        'model_calls': 0, 'scope_notice': report['scope_notice']}
    write_json(out/'execution_plan.json', plan)
    write_json(out/'seal.json', {'files': {p.name: sha256(p) for p in sorted(out.iterdir()) if p.is_file()}})
    return report


def verify_plan(dataset, plan_path):
    dataset, plan_path = Path(dataset), Path(plan_path)
    verify(dataset)
    seal = load(plan_path.parent/'seal.json')
    for name, digest in seal['files'].items():
        if sha256(plan_path.parent/name) != digest:
            raise ValueError(f'Duplicate audit artifact changed: {name}')
    plan = load(plan_path)
    if plan['dataset_seal_sha256'] != sha256(dataset/'seal.json'):
        raise ValueError('Page plan belongs to another dataset')
    if sha256(plan['catalog_path']) != plan['catalog_sha256']:
        raise ValueError('Duplicate source catalog changed')
    for path, digest in plan['source_hashes'].items():
        if sha256(path) != digest:
            raise ValueError('Duplicate audit raw source changed')
    queue = {d['document_id']: d for d in rows(dataset/'development_delta_catalog.jsonl')}
    for identifier, numbers in plan['pages_by_document'].items():
        if identifier not in queue or not numbers or len(numbers) != len(set(numbers)):
            raise ValueError('Invalid development page plan')
        if any(type(n) is not int or not 1 <= n <= queue[identifier]['page_count'] for n in numbers):
            raise ValueError('Page plan is outside registered physical pages')
        if set(numbers) & set(plan['quarantined_pages'].get(identifier, [])):
            raise ValueError('Quarantined page entered model plan')
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--cached-fingerprints', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.dataset, args.catalog, args.cached_fingerprints, args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
