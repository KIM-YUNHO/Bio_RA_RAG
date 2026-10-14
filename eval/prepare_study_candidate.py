"""Derive a metadata-only candidate and retain vectors only for identical inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag.preprocessed_studies import annotate_all


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path): return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists(): raise ValueError('Choose a new candidate directory')
    summary = json.loads((a.source/'chunks_summary.json').read_text(encoding='utf-8'))
    for name, sha in summary['artifacts'].items():
        if digest(a.source/name) != sha: raise ValueError('Changed candidate input: '+name)
    original = read(a.source/'chunks.jsonl')
    chunks, report = annotate_all(original, read(Path(summary['input_run'])/'source_units.jsonl'))
    if [(c['chunk_id'], c['header'], c['text']) for c in chunks] != [(c['chunk_id'], c['header'], c['text']) for c in original]:
        raise ValueError('Embedding input changed; vectors cannot be reused')
    shutil.copytree(a.source, a.out)
    (a.out/'chunks.jsonl').write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in chunks), encoding='utf-8')
    schema = json.loads((a.out/'chunk.schema.json').read_text(encoding='utf-8'))
    schema['properties']['study_candidates'] = {'type': 'object'}
    (a.out/'chunk.schema.json').write_text(json.dumps(schema,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    new_sha = digest(a.out/'chunks.jsonl')
    summary['artifacts']['chunks.jsonl'] = new_sha
    summary['artifacts']['chunk.schema.json'] = digest(a.out/'chunk.schema.json')
    summary['chunks_sha256'] = new_sha
    summary['output_directory'] = str(a.out.resolve())
    summary['study_candidates'] = {**report, 'parent_directory': str(a.source.resolve()),
        'parent_chunks_sha256': digest(a.source/'chunks.jsonl'), 'identical_embedding_inputs': True}
    (a.out/'chunks_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for path in (a.out/'emb').rglob('meta.json'):
        meta = json.loads(path.read_text(encoding='utf-8'))
        if meta.get('chunks_sha256') != digest(a.source/'chunks.jsonl'): raise ValueError('Parent embedding hash mismatch')
        meta.update(chunks_sha256=new_sha, metadata_derivation=summary['study_candidates'])
        path.write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__ == '__main__': main()
