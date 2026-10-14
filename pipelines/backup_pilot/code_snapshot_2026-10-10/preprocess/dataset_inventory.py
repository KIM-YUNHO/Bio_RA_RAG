"""Compose verified inventory components without rescanning unchanged sources."""
import argparse
from collections import Counter
from pathlib import Path

from .dataset import load, rows, verify
from .ingest import from_catalog, sha256
from .inventory import write_json, write_jsonl
from .schema import JSON_SCHEMA
from .validate import validate_run


def compose(dataset, baseline_run, delta_run, out):
    dataset, baseline_run, delta_run, out = map(Path, (dataset, baseline_run, delta_run, out))
    if out.exists():
        raise ValueError('Combined inventory output exists; use a new directory')
    verify(dataset)
    for component in (baseline_run, delta_run):
        validate_run(component)
    baseline_manifest, delta_manifest = load(baseline_run/'manifest.json'), load(delta_run/'manifest.json')
    if baseline_manifest['configuration'] != delta_manifest['configuration']:
        raise ValueError('Inventory detection configurations differ; cannot reuse counts as one inventory')
    summary = load(dataset/'dataset.json')
    root = summary['source_root']
    registered = from_catalog(dataset/'catalog.jsonl', root)
    catalog = {(d.document_id, d.version_id): d for d in registered}
    specs = {(d['document_id'], d['version_id']): d for d in rows(dataset/'catalog.jsonl')}
    doc_rows, page_rows, assets, origins = [], [], [], []
    component_rows = {str(run): {kind: rows(run/(kind+'.jsonl')) for kind in ('documents', 'pages', 'assets')}
                      for run in (baseline_run, delta_run)}
    for key, doc in catalog.items():
        run = baseline_run if specs[key]['baseline_status'] == 'baseline_unchanged' else delta_run
        data = component_rows[str(run)]
        matches = [r for r in data['documents'] if (r['document_id'], r['version_id']) == key]
        if len(matches) != 1:
            raise ValueError(f'Inventory component missing or duplicating source {key}')
        record = {**matches[0], 'metadata': doc.metadata}
        if record['raw_sha256'] != doc.expected_sha256:
            raise ValueError('Reused inventory source version differs from dataset')
        doc_rows.append(record)
        page_rows.extend(r for r in data['pages'] if (r['document_id'], r['version_id']) == key)
        assets.extend(r for r in data['assets'] if (r['document_id'], r['version_id']) == key)
        origins.append({'document_id': key[0], 'version_id': key[1], 'component_run': str(run.resolve())})
    configuration = delta_manifest['configuration']
    result = {'configuration': configuration, 'documents': len(doc_rows),
        'document_statuses': dict(Counter(d['status'] for d in doc_rows)),
        'pdf_pages': sum(p['locator_type'] == 'pdf_page' for p in page_rows),
        'html_documents_assessed': sum(p['locator_type'] == 'html_document' for p in page_rows),
        'failed_pages': sum(p['detection_status'] == 'failed' for p in page_rows),
        'pdf_pages_needing_ocr': sum(p['needs_ocr'] and p['locator_type'] == 'pdf_page' for p in page_rows),
        'candidate_regions': len(assets), 'candidates_by_type': dict(Counter(a['asset_type'] for a in assets)),
        'candidates_by_modality': dict(Counter(a['modality'] for a in assets)),
        'next_steps': dict(Counter(a['next_step'] for a in assets)),
        'unchanged_documents_reused': sum(d['baseline_status'] == 'baseline_unchanged' for d in specs.values()),
        'new_or_changed_documents': sum(d['baseline_status'] != 'baseline_unchanged' for d in specs.values()),
        'vlm_calls': 0, 'ocr_calls': 0, 'layout_model_calls': 0,
        'scope_notice': 'Verified source-bound composition; heuristic candidates, not logical table counts or extraction accuracy.'}
    out.mkdir(parents=True)
    for kind, records in [('documents', doc_rows), ('pages', page_rows), ('assets', assets)]:
        write_jsonl(out/(kind+'.jsonl'), records)
    reviews = [{'document_id': d['document_id'], 'version_id': d['version_id'], 'reason': 'document_incomplete'}
               for d in doc_rows if d['status'] != 'heuristic_inventory_complete']
    reviews.extend({'document_id': a['document_id'], 'asset_id': a['asset_id'], 'reason': 'candidate_classification_pending'}
                   for a in assets if a['next_step'] == 'layout_classify')
    from .dataset import write_rows
    write_rows(out/'review_queue.jsonl', reviews)
    write_json(out/'inventory_summary.json', result)
    write_json(out/'inventory.schema.json', JSON_SCHEMA)
    components = {str(run.resolve()): {name: sha256(run/name) for name in ('manifest.json', 'documents.jsonl', 'pages.jsonl', 'assets.jsonl')}
                  for run in (baseline_run, delta_run)}
    write_json(out/'composition.json', {'dataset_seal_sha256': sha256(dataset/'seal.json'),
        'component_hashes': components, 'document_origins': origins})
    write_json(out/'manifest.json', {'configuration': configuration, 'documents': [d.to_dict() for d in registered],
        'status': 'inventory_complete', 'workers': 2, 'composition': 'composition.json'})
    verification = validate_run(out)
    write_json(out/'inventory_validation.json', verification)
    write_json(out/'seal.json', {'files': {p.name: sha256(p) for p in sorted(out.iterdir()) if p.is_file()}})
    return {**result, 'validation': verification}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--baseline-run', type=Path, required=True)
    parser.add_argument('--delta-run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    import json
    print(json.dumps(compose(args.dataset, args.baseline_run, args.delta_run, args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
