"""Compose a release from the latest H build plus a validated supplement run.

Uses rag.release.compose unchanged: every source version keeps exactly one
representation, embedding contracts must match, protected evaluation data is
refused. The base and supplement folders are read only.
"""
import argparse, json
from collections import Counter
from pathlib import Path

from pipelines.pipeline_h.prepare import ROOT, read, write


def merge(base_extraction, base_candidate, supplement_run, out):
    supplement_run = Path(supplement_run).resolve()
    status = read(supplement_run / 'pipeline_status.json')
    if status.get('stage') != 'finished' or status.get('status') != 'complete':
        raise ValueError('Supplement run has not passed its own release validation')
    audit = read(supplement_run / 'metadata_and_integrity_audit.json')
    if audit.get('status') != 'passed':
        raise ValueError('Supplement integrity audit did not pass')
    from rag.release import compose, ReleaseBundle
    manifest = compose([(Path(base_extraction), Path(base_candidate)),
                        (supplement_run / 'extraction', supplement_run / 'candidate')], out)
    bundle = ReleaseBundle(out)
    from rag.search import load_index
    chunks, vectors, meta = load_index('bge-m3', bundle.candidate)
    intake = read(supplement_run / 'intake_manifest.json')
    added = {(d['document_id'], d['version_id']) for d in intake['documents']}
    indexed = {(c['document_id'], c['version_id']) for c in chunks}
    if added - indexed:
        raise ValueError('Supplement documents missing from merged release: ' + str(sorted(added - indexed)))
    summary = {'release': str(Path(out).resolve()), 'documents': len(bundle.docs), 'chunks': len(chunks),
               'vectors': list(vectors.shape), 'chunks_sha256': meta['chunks_sha256'],
               'added_documents': len(added), 'added_chunks': sum((c['document_id'], c['version_id']) in added for c in chunks),
               'documents_by_product': dict(Counter(d['metadata']['product_id'] for d in bundle.docs.values())),
               'supplement_scope': intake['scope'], 'supplement_decision': intake['decision'],
               'base': {'extraction': str(base_extraction), 'candidate': str(base_candidate)},
               'service_pointer_changed': False}
    write(Path(out) / 'merge_summary.json', summary)
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-extraction', required=True)
    p.add_argument('--base-candidate', required=True)
    p.add_argument('--supplement-run', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--no-publish', action='store_true', help='do not load the release into PostgreSQL + pgvector (storage.publish_release)')
    a = p.parse_args()
    print(json.dumps(merge(a.base_extraction, a.base_candidate, a.supplement_run, a.out), ensure_ascii=False, indent=1))
    from storage.publish_release import after_build
    raise SystemExit(after_build(a.out, a.no_publish))
