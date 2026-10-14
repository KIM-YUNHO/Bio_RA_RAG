"""Apply previously source-verified native row fragments to a separate release.

No question, product ID, page ID or expected value controls eligibility.
All proofs must pass the existing native_table_fragments binding and fragment
checks. Original releases, review-only boundary rows and source PDFs survive.
"""
from collections import Counter
from copy import deepcopy
import argparse
from pathlib import Path
import time

from eval.h_v2_development_eval import read, save, digest


def correct_provenance(candidate, previous_candidate):
    """Preserve H/DOM provenance; the generic chunk writer has a legacy label."""
    from preprocess.hybrid import read_jsonl, save_rows
    from preprocess.native_table_fragments import ORIGIN
    old={c['header']+'\n'+c['text']:c['provenance']['parser']
         for c in read_jsonl(previous_candidate/'chunks.jsonl')}
    chunks=read_jsonl(candidate/'chunks.jsonl');inputs=[c['header']+'\n'+c['text'] for c in chunks]
    changed=0
    for chunk,key in zip(chunks,inputs):
        origins=set(chunk['representation']['origins'].values())
        parser=(old[key] if key in old else
            'Source PDF borders and native glyphs; native_closed_table_fragment; preserved H context'
            if ORIGIN in origins else
            'Python HTMLParser original DOM; PyMuPDF Story preview (no OCR/VLM)'
            if any(r.get('content_locator_type')=='html_dom_node' for r in chunk['provenance']['source_refs']) else
            'Heron + RapidOCR/PP-OCRv6 + TableFormer accurate + ra_prep rules; PP-DocLayoutV3 + selected PaddleOCR-VL-1.6-0.9B')
        if chunk['provenance']['parser']!=parser:changed+=1
        chunk['provenance']['parser']=parser
    assert inputs==[c['header']+'\n'+c['text'] for c in chunks]
    save_rows(candidate/'chunks.jsonl',chunks);sha=digest(candidate/'chunks.jsonl')
    summary=read(candidate/'chunks_summary.json');summary['chunks_sha256']=sha
    summary['artifacts']['chunks.jsonl']=sha;save(candidate/'chunks_summary.json',summary)
    meta_path=candidate/'emb/bge-m3/meta.json'
    if meta_path.exists():
        meta=read(meta_path);meta['chunks_sha256']=sha;save(meta_path,meta)
    return {'provenance_labels_corrected':changed,'embedding_inputs_unchanged':True}


def build(source, previous_candidate, probe, out):
    from preprocess.native_table_fragments import eligible, binding, VERSION, ORIGIN
    from preprocess.ingest import Document, stable_hash, assert_model_execution_allowed
    from preprocess.planned_enrichment import snapshot
    from preprocess.hybrid import save_rows, read_jsonl
    from preprocess.hybrid_validate import validate_extraction
    from rag import preprocessed_chunks
    from pipelines.pipeline_h.chunk_runtime import accelerate
    from rag.incremental_embeddings import build as embed
    from rag.release import compose
    from preprocess.visual_context import assemble, save_bundle

    if out.exists(): raise ValueError('Use a fresh candidate output')
    scan=read(probe/'summary.json')
    assert scan['source_integrity_sha256']==digest(source/'extraction_integrity.json')
    before={str(p):digest(p) for p in (source/'extraction_integrity.json',
        previous_candidate/'chunks.jsonl',previous_candidate/'emb/bge-m3/doc.npy')}
    started=time.monotonic(); extraction=out/'extraction'; snapshot(source,extraction)
    manifest=read(extraction/'extraction_manifest.json')
    documents=[Document(**d) for d in manifest['documents']];assert_model_execution_allowed(documents)
    assets=read_jsonl(extraction/'extracted_assets.jsonl'); originals={a['asset_id']:deepcopy(a) for a in assets}
    by_id={a['asset_id']:a for a in assets}; recovered=[]
    for row in scan['rows']:
        if not row['recoverable']:continue
        a=by_id[row['asset_id']];proof=read(probe/'proofs'/(row['asset_id']+'.json'))
        if proof['source_binding_sha256']!=stable_hash(binding(a)):raise ValueError('Source-bound proof changed')
        a['superseded_native_fragment_selection']=deepcopy(a['selected'])
        if a.get('enrichment'):a['superseded_native_fragment_enrichment']=deepcopy(a['enrichment'])
        a['enrichment']=None;a['native_table_fragment']=proof
        a['selected']={'origin':ORIGIN,'content':proof['fragment']['html']}
        a['route']={'task':None,'next_stage':'native_closed_rows_complete','policy_version':VERSION,
            'reasons':['physical_native_header_and_closed_row_groups'],'numeric_evidence_eligible':False}
        a['validation_status']='derived_unverified'
        if not eligible(a):raise ValueError('Existing source fragment contract rejected candidate')
        recovered.append(row)
    if not recovered:raise ValueError('No source-verified fragments available')
    # No other asset selection is permitted to change.
    changed={r['asset_id'] for r in recovered}
    assert all(a==originals[a['asset_id']] for a in assets if a['asset_id'] not in changed)
    # H has additional source-preserving selections (including caption
    # fallbacks) beyond the generic save_outputs serializer. Preserve every
    # untouched unit verbatim; only the proof-bound table units may change.
    units=read_jsonl(source/'source_units.jsonl')
    original_units={u['unit_id']:deepcopy(u) for u in units}
    for unit in units:
        if unit['unit_id'] not in changed:continue
        a=by_id[unit['unit_id']];part=a['native_table_fragment']['fragment']
        unit.update(text=a['selected']['content'],content_origin=ORIGIN,
            representation_range=[0,len(a['selected']['content'])],
            validation_status='derived_unverified',review_required=False,retrieval_eligible=True)
        unit['native_table_fragment']={k:deepcopy(part[k]) for k in (
            'version','header_source_rows','closed_source_row_groups','excluded_source_rows','cross_page_relationship')}
    assert all(u==original_units[u['unit_id']] for u in units if u['unit_id'] not in changed)
    assert all(u['source_refs']==original_units[u['unit_id']]['source_refs'] for u in units)
    summary=read(extraction/'extraction_summary.json')
    summary['native_table_fragments']={'version':VERSION,'source':str(source),'recovered':recovered,
        'new_ocr_calls':0,'new_qwen_calls':0,'new_openai_calls':0}
    manifest.pop('status_run_id',None);manifest['native_table_fragments_version']=VERSION;manifest['status']='partial'
    save(extraction/'extraction_manifest.json',manifest)
    save(extraction/'extraction_summary.json',summary)
    save_rows(extraction/'extracted_assets.jsonl',assets);save_rows(extraction/'source_units.jsonl',units)
    pages=read_jsonl(extraction/'extraction_pages.jsonl');groups=read_jsonl(extraction/'visual_groups.jsonl')
    context=save_bundle(extraction,assemble(assets,units,groups,[d.to_dict() for d in documents],pages))
    integrity=read(extraction/'extraction_integrity.json')
    integrity['files']={name:digest(extraction/name) for name in integrity['files']}
    integrity['visual_context']=context;save(extraction/'extraction_integrity.json',integrity)
    validate_extraction(extraction)
    print({'stage':'extraction','recovered_tables':len(recovered)},flush=True)
    accelerate(preprocessed_chunks)
    candidate=out/'candidate'; chunks=preprocessed_chunks.write(extraction,candidate)
    correct_provenance(candidate,previous_candidate)
    print({'stage':'chunks','count':len(chunks) if isinstance(chunks,list) else 'written'},flush=True)
    reuse=embed(candidate,previous_candidate)
    print({'stage':'embedding','new':reuse['new_vectors'],'reused':reuse['reused_vectors']},flush=True)
    release=compose([(extraction,candidate)],out/'release')
    assert all(digest(p)==sha for p,sha in before.items()), 'Original source/index changed'
    final={'method':VERSION,'recovered':recovered,'source_modified':False,'original_hashes':before,
        'embedding_reuse':{k:v for k,v in reuse.items() if k not in ('reused_pairs','new_chunk_indices')},
        'release':str(out/'release'),'seconds':time.monotonic()-started,'deployment':False,
        'semantic_accuracy_certified':False,'untouched_units_preserved':True,
        'changed_unit_count':len(changed),'all_source_refs_preserved':True}
    save(out/'candidate_build.json',final)
    print({'stage':'release','path':str(out/'release'),'seconds':final['seconds']},flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
    p.add_argument('--previous-candidate',type=Path,required=True);p.add_argument('--probe',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    build(a.source.resolve(),a.previous_candidate.resolve(),a.probe.resolve(),a.out.resolve())
