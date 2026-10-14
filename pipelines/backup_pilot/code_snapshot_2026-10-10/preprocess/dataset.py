"""Freeze a catalog-bound dataset and incremental, evaluation-aware work queues.

No network or model calls. Native inventory may include locked evaluation sources.
The development model queue excludes locked products and inherited v1 families.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .ingest import from_catalog, sha256, stable_hash
from .inventory import write_json
from .visual_evaluation import family_key

VERSION = 'dataset-intake-1'
HISTORY_SPECS = (
    ('data/preprocessed', '**/extraction_pages.jsonl', 'paddle_page'),
    ('data/preprocessed', '**/sample_manifest.json', 'reference_render_metadata'),
    ('data/ocr', '**/p*_r*.json', 'legacy_ocr_or_native_table'),
    ('data/ocr', '**/text_audit.json', 'text_layer_audit'),
    ('data/evidence', '**/*.json*', 'prior_evidence_or_model'),
    ('data/reviewed', '**/*.jsonl', 'reviewed_evidence'),
    ('data/rag/eval', '**/*.json*', 'prior_benchmark'),
    ('gold', '**/*.json*', 'prior_gold_artifact'),
    ('eval', '**/*.json*', 'prior_evaluation_artifact'),
    ('data/tables', '*.json', 'native_table_candidate'),
    ('product_search/data/tables', '*.json', 'staged_native_table_candidate'),
)
OWNER_KEYS = {'document_id', 'target_document_id', 'doc_id', 'document_sha256', 'version_id',
    'sha256', 'raw_path', 'pdf_key', 'unit_id', 'source_unit_id', 'pdf_file'}


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def write_rows(path, values):
    Path(path).write_text(''.join(json.dumps(d, ensure_ascii=False, allow_nan=False) + '\n' for d in values), encoding='utf-8')


def row_family(row):
    return family_key({'document_id': row['document_id'], 'metadata': row})


def history(root, documents, qa_audit=None):
    """Inspect provenance keys programmatically, never emit answers/text/cells."""
    root = Path(root)
    by_sha, by_id = defaultdict(set), {}
    for d in documents:
        by_sha[d['sha256']].add(d['document_id'])
        by_id[d['document_id']] = d
    events, inputs = [], {}
    for directory, pattern, kind in HISTORY_SPECS:
        for path in sorted((root/directory).glob(pattern)):
            matched, pages = set(), defaultdict(set)
            def owners(value):
                found = set()
                if not isinstance(value, str):
                    return found
                if value in by_id:
                    found.add(value)
                for digest in re.findall(r'[0-9a-f]{64}', value):
                    found.update(by_sha.get(digest, ()))
                for identifier in by_id:
                    if value.startswith(identifier + ':'):
                        found.add(identifier)
                return found
            def visit(value, inherited=()):
                if isinstance(value, dict):
                    own = set().union(*(owners(value.get(k)) for k in OWNER_KEYS))
                    local = own or set(inherited)
                    matched.update(own)
                    page = value.get('pdf_page')
                    if type(page) is int and page > 0:
                        for identifier in local:
                            pages[identifier].add(page)
                    for child in value.values():
                        if isinstance(child, (dict, list)):
                            visit(child, local)
                elif isinstance(value, list):
                    for child in value:
                        visit(child, inherited)
            try:
                visit(rows(path) if path.suffix == '.jsonl' else load(path))
            except (ValueError, UnicodeError):
                # Unknown provenance is recorded, rather than called "unseen".
                events.append({'kind': 'unreadable_history', 'artifact': path.relative_to(root).as_posix()})
                inputs[path.relative_to(root).as_posix()] = sha256(path)
                continue
            if matched:
                relative = path.relative_to(root).as_posix()
                inputs[relative] = sha256(path)
                for identifier in sorted(matched):
                    events.append({'document_id': identifier, 'kind': kind, 'artifact': relative,
                        'artifact_sha256': inputs[relative], 'pages': sorted(pages[identifier]),
                        'notice': 'Artifact provenance is not proof of human inspection or expert validation.'})
    if qa_audit:
        path = Path(qa_audit).resolve()
        inputs[path.relative_to(root).as_posix()] = sha256(path)
        for product_name, source in load(path).get('sources', {}).items():
            for identifier in sorted(by_sha.get(source.get('expected_sha256'), ())):
                events.append({'document_id': identifier, 'kind': 'prior_qa_source_derivation',
                    'artifact': path.relative_to(root).as_posix(), 'artifact_sha256': sha256(path),
                    'product_name': product_name, 'pages': source.get('annotated_pages', []),
                    'source_sha256': source['expected_sha256'],
                    'notice': 'Previously derived QA; human/model inspection history remains incomplete.'})
    return {'events': events, 'input_hashes': inputs,
        'scope': 'Known structured artifacts and prior QA audit; absence of matches does not prove never viewed.'}


def prepare(root, catalog, baseline, exposure, split, policy_path, out, qa_audit=None):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out.exists():
        raise ValueError('Dataset output already exists; choose a new version directory')
    catalog, baseline, exposure, split, policy_path = map(Path, (catalog, baseline, exposure, split, policy_path))
    policy, current = load(policy_path), rows(catalog)
    ids = [d['document_id'] for d in current]
    if len(set(ids)) != len(ids):
        raise ValueError('Catalog document IDs must be unique; resolve versions explicitly before freezing')
    prior = {d['document_id']: d.get('expected_sha256') or d['version_id'] for d in load(baseline)['documents']}
    current_by_id = {d['document_id']: d for d in current}
    if set(prior) - set(current_by_id):
        raise ValueError('Baseline documents disappeared; reconcile removals explicitly')
    old_exposure, old_split = load(exposure), load(split)
    protected_families = {f for c in old_split['cohorts'].values() for f in c['family_ids']}
    protected_keys, protected_ids = set(), set()
    for d in old_exposure['documents']:
        if d['family_id'] in protected_families:
            protected_ids.add(d['document_id'])
            if d['document_id'] in current_by_id:
                protected_keys.add(stable_hash(row_family(current_by_id[d['document_id']])))
    development = set(policy['development_product_ids'])
    locked = policy['locked_product_cohorts']
    if development & set(locked):
        raise ValueError('Development and locked product sets overlap')
    selected, excluded = [], []
    for original in current:
        d = deepcopy(original)
        if d['product_id'] not in development | set(locked):
            excluded.append(d)
            continue
        if d.get('kind') not in {'pdf', 'html'}:
            raise ValueError('Unsupported source kind in target catalog')
        old = prior.get(d['document_id'])
        status = 'baseline_unchanged' if old == d['sha256'] else 'baseline_new_version' if old else 'added_document_id'
        key = stable_hash(row_family(d))
        inherited = d['document_id'] in protected_ids or key in protected_keys
        cohort = locked.get(d['product_id']) or ('legacy_v1_locked' if inherited else 'development')
        d.update(dataset_version=policy['dataset_version'], baseline_status=status,
            evaluation_cohort=cohort, evaluation_family_id='df' + key[:24],
            model_execution_allowed=cohort == 'development', exposure_status='history_not_certified')
        # Version identity always addresses original bytes, even when text_path is repaired.
        if d['version_id'] != d['sha256']:
            raise ValueError(f'Version identity differs from original SHA: {d["document_id"]}')
        for field in ('raw_path', 'text_path'):
            path = (root / (d.get(field) or d['raw_path'])).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f'Missing or out-of-root source: {d["document_id"]} {field}')
        if sha256(root/d['raw_path']) != d['sha256']:
            raise ValueError(f'Raw hash mismatch: {d["document_id"]}')
        selected.append(d)
    delta = [d for d in selected if d['baseline_status'] != 'baseline_unchanged']
    audit = history(root, delta, qa_audit)
    seen = {e.get('document_id') for e in audit['events']}
    for d in selected:
        if d['document_id'] in seen:
            d['exposure_status'] = 'prior_artifact_recorded_inspection_unknown'
    # Same bytes cannot cross model-enabled/locked cohorts unnoticed.
    sha_groups = defaultdict(list)
    for d in selected:
        sha_groups[d['sha256']].append(d)
    duplicate_groups = []
    for digest, group in sha_groups.items():
        if len(group) < 2:
            continue
        locked_group = any(not d['model_execution_allowed'] for d in group)
        if locked_group:
            for d in group:
                if d['model_execution_allowed']:
                    d.update(model_execution_allowed=False, evaluation_cohort='duplicate_of_locked_source')
        duplicate_groups.append({'sha256': digest, 'document_ids': [d['document_id'] for d in group],
                                 'locked_alias_group': locked_group})
    queue = [d for d in delta if d['kind'] == 'pdf' and d['model_execution_allowed']]
    input_paths = [catalog, baseline, exposure, split, policy_path]
    source_hashes = {str(p.resolve()): sha256(p) for p in input_paths}
    summary = {'version': VERSION, 'dataset_version': policy['dataset_version'],
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'source_root': str(root),
        'target_documents': len(selected), 'target_by_product': dict(Counter(d['product_id'] for d in selected)),
        'delta_documents': len(delta), 'delta_by_cohort': dict(Counter(d['evaluation_cohort'] for d in delta)),
        'delta_pdf_pages_from_catalog': sum(d.get('page_count', 0) for d in delta if d['kind'] == 'pdf'),
        'development_model_documents': len(queue),
        'development_model_pages_from_catalog': sum(d.get('page_count', 0) for d in queue),
        'excluded_catalog_records': len(excluded), 'original_baseline_records': len(prior),
        'strict_holdout_ready': False, 'expert_gold_ready': False,
        'cohort_assignments': locked, 'source_input_hashes': source_hashes,
        'notice': policy.get('notice'), 'model_calls': 0,
        'near_visual_duplicate_check': 'pending; exact SHA aliases checked, not clinical equivalence or vector-figure identity'}
    # Freeze only after the entire plan and source checks succeed.
    out.mkdir(parents=True)
    for name, data in [('catalog.jsonl', selected), ('delta_catalog.jsonl', delta),
                       ('development_delta_catalog.jsonl', queue), ('excluded_catalog.jsonl', excluded)]:
        write_rows(out/name, data)
    write_json(out/'dataset.json', summary)
    write_json(out/'policy.json', policy)
    write_json(out/'exposure_history.json', audit)
    write_json(out/'duplicate_sources.json', duplicate_groups)
    write_json(out/'legacy_evaluation_split.json', old_split)
    seal = {'version': VERSION, 'files': {p.name: sha256(p) for p in sorted(out.iterdir()) if p.is_file()}}
    write_json(out/'seal.json', seal)
    return summary


def verify(out, check_live_catalog=True):
    out = Path(out)
    seal, summary = load(out/'seal.json'), load(out/'dataset.json')
    for name, expected in seal['files'].items():
        if sha256(out/name) != expected:
            raise ValueError(f'Dataset artifact changed: {name}')
    if check_live_catalog:
        for path, expected in summary['source_input_hashes'].items():
            if sha256(path) != expected:
                raise ValueError(f'Dataset input changed: {path}')
        for relative, expected in load(out/'exposure_history.json')['input_hashes'].items():
            if sha256(Path(summary['source_root'])/relative) != expected:
                raise ValueError(f'Exposure history input changed: {relative}')
    for d in rows(out/'catalog.jsonl'):
        if sha256(Path(summary['source_root'])/d['raw_path']) != d['sha256']:
            raise ValueError(f'Dataset raw source changed: {d["document_id"]}')
    for d in rows(out/'development_delta_catalog.jsonl'):
        if d['evaluation_cohort'] != 'development' or d['model_execution_allowed'] is not True:
            raise ValueError('Locked source entered development model queue')
    return {'status': 'passed', 'sealed_files': len(seal['files']), 'target_documents': summary['target_documents']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    prep.add_argument('--catalog', type=Path, required=True)
    prep.add_argument('--baseline', type=Path, required=True)
    prep.add_argument('--exposure', type=Path, required=True)
    prep.add_argument('--split', type=Path, required=True)
    prep.add_argument('--policy', type=Path, required=True)
    prep.add_argument('--qa-audit', type=Path)
    prep.add_argument('--out', type=Path, required=True)
    check = commands.add_parser('verify')
    check.add_argument('--run', type=Path, required=True)
    inventory = commands.add_parser('inventory')
    inventory.add_argument('--dataset', type=Path, required=True)
    inventory.add_argument('--out', type=Path, required=True)
    inventory.add_argument('--workers', type=int, default=2, choices=range(1, 9))
    process = commands.add_parser('process', help='Execute the sealed, development-only PDF page plan')
    process.add_argument('--dataset', type=Path, required=True)
    process.add_argument('--plan', type=Path, required=True)
    process.add_argument('--out', type=Path, required=True)
    process.add_argument('--config', type=Path, required=True)
    process.add_argument('--max-qwen-calls', type=int)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        result = prepare(args.root, args.catalog, args.baseline, args.exposure, args.split, args.policy, args.out, args.qa_audit)
    elif args.command == 'verify':
        result = verify(args.run)
    elif args.command == 'inventory':
        verify(args.dataset)
        summary = load(args.dataset/'dataset.json')
        documents = from_catalog(args.dataset/'delta_catalog.jsonl', summary['source_root'])
        from .inventory import run_inventory
        result = run_inventory(documents, args.out, workers=args.workers)
    else:
        from .dataset_duplicates import verify_plan
        from .hybrid import run_hybrid
        from .hybrid_config import load_config
        plan = verify_plan(args.dataset, args.plan)
        if not plan['pages_by_document']:
            raise ValueError('No non-quarantined development PDF pages in this plan')
        summary = load(args.dataset/'dataset.json')
        documents = from_catalog(args.dataset/'development_delta_catalog.jsonl', summary['source_root'], plan['pages_by_document'])
        result = run_hybrid(documents, args.out, load_config(args.config),
            max_qwen_calls=args.max_qwen_calls, pages_by_document=plan['pages_by_document'])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get('failed_pages') or 'failed' in result.get('document_statuses', {}) or result.get('document_errors') or any(k != 'parsed' for k in result.get('page_statuses', {})) or result.get('qwen', {}).get('failed') or result.get('qwen', {}).get('model_error') else 0


if __name__ == '__main__':
    raise SystemExit(main())
