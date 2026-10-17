"""Re-chunk existing extractions with the current chunker and compose a release.

Extraction outputs (source units, assets, pages) are read only and never rebuilt.
Each component is re-chunked the way it was originally built, compared chunk by
chunk with its reference candidate, embedded incrementally (vectors reused for
byte-identical header+text inputs) and composed with rag.release.compose.

  control:  python -m pipelines.pipeline_h.rechunk_release --out <scratch> --stage chunk
            (with unchanged rules the chunks must equal the reference candidates)
  release:  python -m pipelines.pipeline_h.rechunk_release --out data/versions/05_.../embedded --stage all
"""
import argparse, json
from collections import Counter
from pathlib import Path

from pipelines.pipeline_h.prepare import ROOT, write
from preprocess.hybrid import read_jsonl

# (name, extraction, reference candidate, built through pipeline_h chunks.build)
COMPONENTS = [
    ('base_03', ROOT / 'data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/preprocessed/extraction',
     ROOT / 'data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/embedded/candidate_final', True),
    ('supplement_20261014', ROOT / 'pipelines/pipeline_h/runs/supplement_20261014/extraction',
     ROOT / 'pipelines/pipeline_h/runs/supplement_20261014/candidate', True),
]


def rechunk(extraction, out, h_provenance, section_rules=None):
    from rag import preprocessed_chunks
    from pipelines.pipeline_h.chunk_runtime import accelerate
    from pipelines.pipeline_h.chunks import correct_provenance
    accelerate(preprocessed_chunks)
    cfg = {'section_rules_version': section_rules, 'structure_version': section_rules} if section_rules else None
    summary = preprocessed_chunks.write(extraction, out, cfg=cfg)
    if h_provenance:
        correct_provenance(out)
    return summary


def compare(reference, candidate):
    """Chunk-level difference; chunk IDs carry the config profile, so match on location+text."""
    old = read_jsonl(Path(reference) / 'chunks.jsonl')
    new = read_jsonl(Path(candidate) / 'chunks.jsonl')
    key = lambda c: (c['document_id'], c['version_id'], c['pdf_page'], c['text'])
    old_by, new_by = {key(c): c for c in old}, {key(c): c for c in new}
    fields = Counter()
    for k in old_by.keys() & new_by.keys():
        for f in set(old_by[k]) | set(new_by[k]):
            if f not in ('chunk_id', 'chunking') and old_by[k].get(f) != new_by[k].get(f):
                fields[f] += 1
    same_input = sum(old_by[k]['header'] == new_by[k]['header'] for k in old_by.keys() & new_by.keys())
    return {'reference_chunks': len(old), 'candidate_chunks': len(new),
            'same_text_and_page': len(old_by.keys() & new_by.keys()),
            'only_reference': len(old_by.keys() - new_by.keys()), 'only_candidate': len(new_by.keys() - old_by.keys()),
            'same_embedding_input': same_input, 'differing_fields_on_matched_chunks': dict(fields),
            'identical': len(old) == len(new) == same_input and not fields}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--stage', choices=['chunk', 'all'], required=True)
    p.add_argument('--section-rules', help='e.g. sections-9 (release 05); default: current rag.document_structure.VERSION')
    p.add_argument('--no-publish', action='store_true', help='with --stage all: do not load the release into PostgreSQL + pgvector')
    a = p.parse_args()
    out = a.out.resolve()
    if out.exists() and any(out.iterdir()):
        p.error('Choose a new, empty output directory')
    report = {'components': {}}
    specs = []
    for name, extraction, reference, h_provenance in COMPONENTS:
        candidate = out / 'components' / name / 'candidate'
        summary = rechunk(extraction, candidate, h_provenance, a.section_rules)
        diff = compare(reference, candidate)
        report['components'][name] = {'extraction': str(extraction), 'reference': str(reference),
            'section_rules_version': summary['config']['section_rules_version'], 'comparison': diff}
        print(name, json.dumps(diff, ensure_ascii=False), flush=True)
        if a.stage == 'all':
            from rag.incremental_embeddings import build
            reuse = build(candidate, reference, device='cuda')
            report['components'][name]['embedding'] = {k: reuse[k] for k in ('reused_vectors', 'new_vectors')}
            print(name, 'vectors reused', reuse['reused_vectors'], 'new', reuse['new_vectors'], flush=True)
        specs.append((extraction, candidate))
    status = 0
    if a.stage == 'all':
        from rag.release import compose
        from storage.publish_release import after_build
        compose(specs, out / 'release')
        report['release'] = str(out / 'release')
        status = after_build(out / 'release', a.no_publish)
        report['published'] = status == 0 and not a.no_publish
    write(out / 'rechunk_report.json', report)
    raise SystemExit(status)


if __name__ == '__main__':
    main()
