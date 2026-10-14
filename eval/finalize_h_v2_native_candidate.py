"""Correct legacy chunk-writer labels in a new copy, preserving all vectors."""
from pathlib import Path
import argparse
import shutil
from eval.h_v2_development_eval import read,save,digest
from eval.h_v2_native_candidate import correct_provenance


def finalize(run, previous):
    from rag.release import compose, ReleaseBundle
    build=read(run/'candidate_build.json')
    if not build['untouched_units_preserved']:raise ValueError('Unrelated source units changed')
    candidate=run/'candidate_final'
    if candidate.exists():raise ValueError('Final candidate already exists')
    shutil.copytree(run/'candidate',candidate)
    before={name:digest(candidate/name) for name in ('emb/bge-m3/doc.npy','emb/bge-m3/chunk_ids.json')}
    correction=correct_provenance(candidate,previous)
    assert all(digest(candidate/name)==h for name,h in before.items())
    final=run/'release_final';compose([(run/'extraction',candidate)],final)
    bundle=ReleaseBundle(final)
    result={**correction,'release':str(final),'documents':len(bundle.docs),'chunks':bundle.chunk_count,
        'vector_and_chunk_ids_unchanged':True,'source_units_changed':build['changed_unit_count'],
        'original_source_refs_preserved':True,'new_provider_calls':0,'production_pointer_changed':False,
        'code_sha256':{str(p):digest(p) for p in [Path(__file__),Path(__file__).with_name('h_v2_native_candidate.py')]}}
    save(run/'final_candidate_audit.json',result)
    print(result,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--previous',type=Path,required=True)
    a=p.parse_args();finalize(a.run.resolve(),a.previous.resolve())
