"""Rebuild visual/table context from completed extraction without model calls."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from .hybrid import read_jsonl, save_outputs
from .hybrid_validate import validate_extraction
from .ingest import Document, sha256
from .inventory import write_json
from .reporting import build_report, now, save_report
from .visual_groups import GROUPING_VERSION, build_visual_groups


def regroup(run, out, metadata_catalog=None):
    run, out = Path(run).resolve(), Path(out).resolve()
    if out == run or out.is_relative_to(run) or run.is_relative_to(out):
        raise ValueError('Choose a separate preprocessing output directory')
    if out.exists() and any(out.iterdir()):
        raise ValueError('Regroup output must be empty; do not overwrite a completed run')
    manifest = json.loads((run/'extraction_manifest.json').read_text(encoding='utf-8'))
    if manifest['status'] not in {'partial', 'extraction_complete'}:
        raise ValueError('Cannot regroup an unfinished extraction')
    if (run/'processing_status.json').exists() and json.loads((run/'processing_status.json').read_text(encoding='utf-8'))['lifecycle'] != 'finished':
        raise ValueError('Cannot regroup a running or interrupted extraction')
    validate_extraction(run)
    names = ['extraction_manifest.json', 'extraction_summary.json', 'extraction_integrity.json',
             'extracted_assets.jsonl', 'extraction_pages.jsonl', 'source_units.jsonl']
    if (run/'visual_groups.jsonl').exists():
        names.append('visual_groups.jsonl')
    hashes = {name: sha256(run/name) for name in names}
    original = read_jsonl(run/'extracted_assets.jsonl')
    old_parents = {a['asset_id']: a for a in original if a.get('is_logical_group')}
    assets = deepcopy([a for a in original if not a.get('is_logical_group')])
    from .native_visual_context import recover_pages
    from .table_quality import inspect_table, route_asset, QUALITY_VERSION
    documents = [Document(**d) for d in manifest['documents']]
    metadata_update = None
    if metadata_catalog:
        from .ingest import refresh_catalog_metadata
        metadata_catalog = Path(metadata_catalog).resolve()
        metadata_digest = sha256(metadata_catalog)
        refresh_catalog_metadata(documents, metadata_catalog)
        metadata_update = {'catalog': str(metadata_catalog), 'sha256': metadata_digest,
                           'matching': 'exact_document_id_and_version', 'intake_policies_preserved': True}
    page_rows = read_jsonl(run/'extraction_pages.jsonl')
    recover_pages(documents, page_rows, assets)
    for a in assets:
        if a['asset_type'] != 'table':
            continue
        native = a['native_reference']
        quality = inspect_table(a['base']['content'], native['text'], native['reliable'])
        old_issues = a['base']['quality']['issues']
        a['base']['quality'] = quality
        a['route'] = route_asset('table', quality, a['base']['content'])
        from .visual_router import reapply
        reapply(a)
        if quality['issues'] != old_issues:
            a['selected'] = {'origin':'paddle','content':a['base']['content']}
            a['enrichment'] = None
    groups, jobs = build_visual_groups(assets)
    preserved = 0
    for job in jobs:
        previous = old_parents.get(job['asset_id'])
        identical = previous and all(previous.get(key) == job.get(key) for key in (
            'document_id', 'version_id', 'pdf_page', 'bbox_pdf', 'bbox_input_px',
            'source_refs', 'member_asset_ids', 'base', 'native_reference'))
        if identical:
            for key in ('selected', 'enrichment', 'validation_status'):
                job[key] = deepcopy(previous[key])
            preserved += 1
    assets.extend(jobs)
    summary = json.loads((run/'extraction_summary.json').read_text(encoding='utf-8'))
    summary['prior_enrichment_execution'] = summary.pop('qwen', {})
    jobs_with_routes = [a for a in assets if a['route']['task']]
    summary['qwen'] = {'attempted_calls': 0, 'cache_reused': 0, 'failed': 0,
        'inherited_candidates': sum(bool(a.get('enrichment')) for a in jobs_with_routes),
        'pending': sum(not a.get('enrichment') or (a['enrichment'].get('status', '').startswith('pending')) for a in jobs_with_routes)}
    update = {'updated_at_kst': now(), 'source_run': str(run), 'source_hashes': hashes,
              'grouping_version': GROUPING_VERSION, 'new_ocr_calls': 0, 'new_vlm_calls': 0,
              'preserved_figure_candidates': preserved, 'rebuilt_figure_parents': len(jobs),
              'source_files_shared': True}
    summary.update(quality_version=QUALITY_VERSION, assets=len(assets), physical_assets=len(assets)-len(jobs), logical_groups=len(groups),
        asset_types=dict(Counter(a['asset_type'] for a in assets)), grouping_version=GROUPING_VERSION, grouping_update=update)
    manifest = deepcopy(manifest)
    manifest['documents'] = [d.to_dict() for d in documents]
    if metadata_update:
        if sha256(metadata_catalog) != metadata_digest:
            raise ValueError('Metadata catalog changed during regroup')
        manifest['metadata_update'] = metadata_update
        update['metadata_update'] = metadata_update
    manifest.pop('status_run_id', None)
    manifest.update(grouping_version=GROUPING_VERSION, grouping_update=update)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out/'extraction_manifest.json', manifest)
    save_outputs(out, assets, read_jsonl(run/'extraction_pages.jsonl'),
                 [Document(**d) for d in manifest['documents']], summary, groups)
    if (run/'sample_manifest.json').exists():
        (out/'sample_manifest.json').write_bytes((run/'sample_manifest.json').read_bytes())
    if any(sha256(run/name) != digest for name, digest in hashes.items()):
        raise ValueError('Source extraction changed during regroup')
    validate_extraction(out)
    save_report(build_report(out), out)
    return update
