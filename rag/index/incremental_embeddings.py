"""Reuse BGE vectors only for byte-identical, complete embedding inputs."""
from collections import defaultdict
import json,time
from pathlib import Path
import numpy as np
from preprocess.ingest import sha256
from preprocess.files import write_json
from rag.retrieval.search import load_index

VERSION='identical-input-vector-reuse-1'

def embedding_input(chunk):return chunk['header']+'\n'+chunk['text']

def reuse_plan(previous,current):
    by_input=defaultdict(list)
    for i,c in enumerate(previous):by_input[embedding_input(c)].append(i)
    pairs=[];changed=[]
    for i,c in enumerate(current):
        old=by_input.get(embedding_input(c))
        if old:pairs.append((i,old[0]))
        else:changed.append(i)
    return pairs,changed

def build(folder,previous,device='cuda',factory=None):
    folder,previous=Path(folder).resolve(),Path(previous).resolve()
    if folder==previous:raise ValueError('Incremental embedding requires a separate candidate')
    old,vectors,meta=load_index('bge-m3',previous)
    if (meta.get('model')!='BAAI/bge-m3' or meta.get('similarity')!='cosine' or meta.get('truncated_inputs')!=0
            or vectors.shape!=(len(old),meta['dim']) or not np.isfinite(vectors).all()
            or not np.allclose(np.linalg.norm(vectors,axis=1),1,atol=.002)):
        raise ValueError('Incompatible source embedding contract')
    source_hashes={n:sha256(previous/n) for n in ('chunks.jsonl','emb/bge-m3/doc.npy','emb/bge-m3/meta.json')}
    chunks=[json.loads(line) for line in (folder/'chunks.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    if not chunks:raise ValueError('No chunks to embed')
    pairs,changed=reuse_plan(old,chunks);out=np.empty((len(chunks),meta['dim']),dtype=np.float32)
    for i,j in pairs:out[i]=vectors[j]
    started=time.perf_counter();encoder=None;embedding_device=meta.get('device')
    try:
        if changed:
            from rag.index.embed_index import Embedder
            encoder=(factory or Embedder)('bge-m3',device=device,local_only=True)
            if encoder.revision!=meta.get('revision'):raise ValueError('Embedding revision changed; source vectors cannot be mixed')
            out[changed]=encoder.encode_docs([chunks[i] for i in changed])
            if encoder.truncated:raise ValueError('Embedding truncated a new input')
            embedding_device=encoder.device
    finally:
        if encoder is not None:
            del encoder
            import gc
            gc.collect()
            if factory is None:
                import torch
                if torch.cuda.is_available():torch.cuda.empty_cache()
    if not np.isfinite(out).all() or not np.allclose(np.linalg.norm(out,axis=1),1,atol=.002):raise ValueError('Invalid incremental vectors')
    if any(sha256(previous/n)!=h for n,h in source_hashes.items()):raise ValueError('Source vectors changed during incremental build')
    dest=folder/'emb/bge-m3';dest.mkdir(parents=True,exist_ok=True)
    if any(dest.iterdir()):raise ValueError('Do not overwrite an existing embedding build')
    np.save(dest/'doc.npy',out);write_json(dest/'chunk_ids.json',[c['chunk_id'] for c in chunks])
    report={'version':VERSION,'source_candidate':str(previous),'source_hashes':source_hashes,
        'reused_vectors':len(pairs),'new_vectors':len(changed),'embedding_input':'header + literal newline + text',
        'reused_pairs':pairs,'new_chunk_indices':changed,'new_api_calls':0}
    newmeta={**meta,'n_chunks':len(chunks),'chunks_sha256':sha256(folder/'chunks.jsonl'),'device':embedding_device,
        'incremental':{k:v for k,v in report.items() if k not in ('reused_pairs','new_chunk_indices')},
        'embed_seconds':round(time.perf_counter()-started,3),'truncated_inputs':0,'non_finite_values':0}
    write_json(dest/'meta.json',newmeta);write_json(folder/'embedding_reuse.json',report)
    load_index('bge-m3',folder)
    return report
