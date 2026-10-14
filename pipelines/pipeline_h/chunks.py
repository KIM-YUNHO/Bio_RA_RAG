"""Keep the current packing strategy; correct parser provenance for the new H source."""
import argparse,time
from collections import Counter
from pathlib import Path
from pipelines.pipeline_h.prepare import read,write
from preprocess.ingest import sha256
from preprocess.hybrid import read_jsonl,save_rows

def correct_provenance(folder):
    folder=Path(folder);chunks=read_jsonl(folder/'chunks.jsonl');changed=False
    for chunk in chunks:
        parser=('Python HTMLParser original DOM; PyMuPDF Story preview (no OCR/VLM)' if any(r.get('content_locator_type')=='html_dom_node' for r in chunk['provenance']['source_refs'])
            else 'Heron + RapidOCR/PP-OCRv6 + TableFormer accurate + ra_prep rules; PP-DocLayoutV3 + selected PaddleOCR-VL-1.6-0.9B')
        if chunk['provenance']['parser']!=parser:chunk['provenance']['parser']=parser;changed=True
    if changed:
        save_rows(folder/'chunks.jsonl',chunks);summary=read(folder/'chunks_summary.json');digest=sha256(folder/'chunks.jsonl')
        summary['chunks_sha256']=digest;summary['artifacts']['chunks.jsonl']=digest;write(folder/'chunks_summary.json',summary)
    return changed

def build(run):
    from rag import preprocessed_chunks as original
    from pipelines.pipeline_h.chunk_runtime import accelerate
    accelerate(original)
    run=Path(run).resolve();out=run/'candidate';start=time.perf_counter()
    if (out/'chunks_summary.json').exists():
        existing=read(out/'chunks_summary.json')
        if any(sha256(run/'extraction'/name)!=digest for name,digest in existing['input_hashes'].items()):
            raise ValueError('Existing chunks reference an older extraction; choose a fresh candidate/run version')
        if existing.get('production_h',{}).get('complete') and (out/'chunk_structure_links.jsonl').exists():return existing
        summary=existing
    else:summary=original.write(run/'extraction',out)
    chunks=read_jsonl(out/'chunks.jsonl')
    for chunk in chunks:
        if any(r.get('content_locator_type')=='html_dom_node' for r in chunk['provenance']['source_refs']):
            chunk['provenance']['parser']='Python HTMLParser original DOM; PyMuPDF Story preview (no OCR/VLM)'
        else:chunk['provenance']['parser']='Heron + RapidOCR/PP-OCRv6 + TableFormer accurate + ra_prep rules; PP-DocLayoutV3 + selected PaddleOCR-VL-1.6-0.9B'
    save_rows(out/'chunks.jsonl',chunks)
    summary['chunks_sha256']=sha256(out/'chunks.jsonl');summary['artifacts']['chunks.jsonl']=summary['chunks_sha256']
    summary['production_h']={'packing_strategy_preserved':True,'old_chunker_sha256':sha256(Path(original.__file__)),
                             'provenance_label_corrected':True,'build_seconds':time.perf_counter()-start}
    write(out/'chunks_summary.json',summary)
    loaded=original.load_input(run/'extraction')
    units=loaded[3];assets=loaded[4];pages=loaded[5]
    original.verify_ranges(chunks,units,assets,pages,require_full_coverage=False)
    unitmap={u['unit_id']:u for u in units};links=[]
    for chunk in chunks:
        ids=chunk['representation']['unit_ids']
        links.append({'chunk_id':chunk['chunk_id'],'document_id':chunk['document_id'],'version_id':chunk['version_id'],
                      'units':[{'unit_id':uid,'source_refs':unitmap[uid]['source_refs'],
                                'docling_structure':assets[uid].get('docling_structure'),
                                'source_structure':assets[uid].get('source_structure'),
                                'h_candidates':assets[uid].get('h_candidates',[])} for uid in ids]})
    save_rows(out/'chunk_structure_links.jsonl',links)
    summary['production_h']['complete']=True;write(out/'chunks_summary.json',summary)
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);a=p.parse_args();print(build(a.run)['chunks'])
