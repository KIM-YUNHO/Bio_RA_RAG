"""Create a new source-preserving extraction subset without changing its parent."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
from .hybrid import read_jsonl, save_outputs
from .hybrid_validate import validate_extraction
from .ingest import Document, sha256
from .inventory import write_json
from .reporting import build_report, save_report
import json


def create(source, out, document_ids):
    source,out=Path(source).resolve(),Path(out).resolve()
    if out.exists():raise ValueError('Choose a new extraction subset directory')
    validate_extraction(source)
    manifest=json.loads((source/'extraction_manifest.json').read_text(encoding='utf-8'))
    wanted=set(document_ids)
    docs=[Document(**d) for d in manifest['documents'] if d['document_id'] in wanted]
    if {d.document_id for d in docs}!=wanted:raise ValueError('Subset source documents missing')
    assets=[a for a in read_jsonl(source/'extracted_assets.jsonl') if a['document_id'] in wanted]
    pages=[p for p in read_jsonl(source/'extraction_pages.jsonl') if p['document_id'] in wanted]
    groups=[g for g in read_jsonl(source/'visual_groups.jsonl') if g['document_id'] in wanted] if (source/'visual_groups.jsonl').exists() else []
    summary=json.loads((source/'extraction_summary.json').read_text(encoding='utf-8'))
    summary.update(documents=len(docs),processed_documents=len(docs),pages=len(pages),assets=len(assets),
        physical_assets=sum(not a.get('is_logical_group') for a in assets),logical_groups=len(groups),
        page_statuses=dict(Counter(p['status'] for p in pages)),asset_types=dict(Counter(a['asset_type'] for a in assets)),
        subset_parent={'run':str(source),'integrity_sha256':sha256(source/'extraction_integrity.json')},
        new_parser_calls=0,new_qwen_calls=0)
    manifest=deepcopy(manifest);manifest.pop('status_run_id',None)
    manifest['documents']=[d.to_dict() for d in docs]
    if manifest.get('pages_by_document'):manifest['pages_by_document']={k:v for k,v in manifest['pages_by_document'].items() if k in wanted}
    out.mkdir(parents=True)
    write_json(out/'extraction_manifest.json',manifest)
    save_outputs(out,assets,pages,docs,summary,groups if manifest.get('grouping_version') else None)
    validate_extraction(out);save_report(build_report(out),out)
    return summary
