"""Compose validated preprocessing candidates with explicit product readiness."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import shutil
from threading import Lock
import numpy as np

from rag.index.bundle import Bundle, read, rows, digest
from rag.index.portable_paths import resolve

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'rag-release-1'


def readiness(chunks):
    catalog = rows(ROOT/'data/catalog/documents.jsonl')
    products = [p for p in rows(ROOT/'data/catalog/products.jsonl') if p.get('kind')=='biosimilar']
    from rag.retrieval.search import filter_idx
    eligible = [chunks[int(i)] for i in filter_idx(chunks)]
    result = []
    for product in products:
        key=product['product_id']; own=[c for c in chunks if c['product_id']==key]
        default=[c for c in eligible if c['product_id']==key]
        count=len({(c['document_id'],c['version_id']) for c in own})
        from rag.retrieval.service_scope import SOURCES
        total=len({(d['document_id'],d['version_id']) for d in catalog if d.get('product_id')==key and d.get('source') in SOURCES})
        result.append({'product_id':key,'name':product.get('ema_name') or product.get('us_brand') or key,
            'catalog_documents':total,'indexed_documents':count,'indexed_chunks':len(own),
            'default_chunks':len(default),'indexed_pages':len({(c['document_id'],c['version_id'],c['pdf_page']) for c in own}),
            'status':'partial' if default else 'not_indexed' if not own else 'excluded_by_default',
            'reason':'전처리·색인된 범위만 검색 가능 · 수치·의미 검토는 별도' if default else
                '수집 완료 · 검색용 전처리와 색인 연결 대기' if not own else '기본 검색에서 제외된 자료만 존재',
            'complete_corpus':False})
    return result


def unique_rows(components, name):
    keyfields = {'source_units.jsonl':'unit_id','contexts.jsonl':'context_id',
        'chunk_contexts.jsonl':'chunk_id','chunk_exclusions.jsonl':'unit_id','visual_groups.jsonl':'group_id',
        'visual_members.jsonl':'asset_id','visual_context_units.jsonl':'context_unit_id',
        'visual_context_links.jsonl':'link_id','visual_context_targets.jsonl':'visual_id'}
    result=[]; seen={}
    for b in components:
        path=b.candidate/name
        if not path.exists():continue
        for row in rows(path):
            field=keyfields.get(name)
            if field not in row:field=None
            identity=(field,row[field]) if field else json.dumps(row,sort_keys=True)
            if identity in seen:
                if row != seen[identity]:raise ValueError('Conflicting release row: '+name)
                continue
            seen[identity]=row; result.append(row)
    return result


def compose(specs, out):
    from rag.retrieval.search import load_index
    out=Path(out).resolve()
    if not out.is_relative_to(ROOT) or out.exists():raise ValueError('Choose a new release directory inside the workspace')
    components=[Bundle(run,candidate) for run,candidate in specs]
    identities=[identity for b in components for identity in b.doc_by_identity]
    if len(set(identities)) != len(identities):raise ValueError('One source version must have exactly one release representation')
    chunks=[]; vectors=[]; metadata=[]; records=[]
    visual_bundles=[]
    for b in components:
        from preprocess.visual.visual_context import assemble, load_bundle as load_visual
        contract=read(b.candidate/'chunks_summary.json').get('visual_context_contract')
        visual_bundles.append(load_visual(b.candidate,contract) if contract else assemble(
            list(b.assets.values()),list(b.units.values()),list(b.groups.values()),list(b.docs.values()),list(b.pages.values())))
        c,v,m=load_index('bge-m3',b.candidate)
        model=m.get('model') or m.get('doc')
        if model!='BAAI/bge-m3' or m.get('similarity')!='cosine' or m.get('truncated_inputs')!=0:
            raise ValueError('Incompatible release embedding contract')
        if v.shape!=(len(c),m['dim']) or not np.isfinite(v).all() or not np.allclose(np.linalg.norm(v,axis=1),1,atol=.002):
            raise ValueError('Invalid normalized release vectors')
        for chunk in c:
            policy=chunk.get('document_metadata') or {}
            if policy.get('evaluation_cohort') not in (None,'development') or policy.get('model_execution_allowed') is False:
                raise ValueError('Protected evaluation/execution-denied data cannot be promoted')
        chunks.extend(c);vectors.append(v);metadata.append(m)
        names=['chunks_summary.json','chunks.jsonl','emb/bge-m3/meta.json','emb/bge-m3/doc.npy','emb/bge-m3/chunk_ids.json']
        records.append({'run':str(b.root),'candidate':str(b.candidate),
                        'hashes':{n:digest(b.candidate/n) for n in names}})
    if len({m['dim'] for m in metadata})!=1 or len({m.get('revision') for m in metadata})!=1:
        raise ValueError('Embedding revisions/dimensions differ')
    if len({c['chunk_id'] for c in chunks})!=len(chunks):raise ValueError('Duplicate release chunk IDs')
    candidate=out/'candidate'; emb=candidate/'emb/bge-m3';emb.mkdir(parents=True)
    def save(name,value): (candidate/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def save_rows(name,value): (candidate/name).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in value),encoding='utf-8')
    save_rows('chunks.jsonl',chunks)
    names=['source_units.jsonl','contexts.jsonl','chunk_contexts.jsonl','chunk_exclusions.jsonl','visual_groups.jsonl',
           'visual_members.jsonl','visual_context_units.jsonl','visual_context_links.jsonl','visual_context_targets.jsonl']
    visual_names={'visual_context_units.jsonl':'units','visual_context_links.jsonl':'links','visual_context_targets.jsonl':'targets'}
    for name in names:
        save_rows(name,[r for b in visual_bundles for r in b[visual_names[name]]] if name in visual_names else unique_rows(components,name))
    shutil.copyfile(components[0].candidate/'chunk.schema.json',candidate/'chunk.schema.json')
    visual_versions={b['version'] for b in visual_bundles}
    if len(visual_versions)!=1:raise ValueError('Visual context versions differ')
    save('visual_context_summary.json',{'composition':VERSION,'components':len(components),'semantic_approval':False})
    visual={'version':next(iter(visual_versions)),'files':{n:digest(candidate/n) for n in
        ['visual_context_units.jsonl','visual_context_links.jsonl','visual_context_targets.jsonl','visual_context_summary.json']}}
    sha=digest(candidate/'chunks.jsonl')
    summary={'composition':VERSION,'chunks':len(chunks),'chunks_sha256':sha,'input_runs':records,
        'active_index_updated':False,'visual_context_contract':visual,
        'artifacts':{n:digest(candidate/n) for n in ['chunks.jsonl','chunk.schema.json',*names,'visual_context_summary.json']},
        'product_readiness':readiness(chunks),'output_directory':str(candidate)}
    save('chunks_summary.json',summary)
    np.save(emb/'doc.npy',np.concatenate(vectors))
    save('emb/bge-m3/chunk_ids.json',[c['chunk_id'] for c in chunks])
    save('emb/bge-m3/meta.json',{**metadata[0],'n_chunks':len(chunks),'chunks_sha256':sha,
        'composition':VERSION,'components':records,'identical_component_embedding_inputs':True})
    manifest={'version':VERSION,'candidate':str(candidate),'components':records,
        'candidate_hashes':{n:digest(candidate/n) for n in ['chunks_summary.json','chunks.jsonl','emb/bge-m3/meta.json','emb/bge-m3/doc.npy','emb/bge-m3/chunk_ids.json']},
        'product_readiness':summary['product_readiness'],'expected_products':sorted(r['product_id'] for r in summary['product_readiness']),
        'protected_evaluation_promoted':False,'complete_corpus':False}
    (out/'release_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


class ReleaseBundle(Bundle):
    def __init__(self, run, candidate=None):
        self.root=Path(run).resolve(); release=read(self.root/'release_manifest.json')
        if release['version']!=VERSION:raise ValueError('Unsupported release manifest')
        self.candidate=Path(resolve(release['candidate'])).resolve()
        if candidate and Path(resolve(candidate)).resolve()!=self.candidate:raise ValueError('Release candidate mismatch')
        for n,sha in release['candidate_hashes'].items():
            if digest(self.candidate/n)!=sha:raise ValueError('Changed release artifact: '+n)
        components=[]
        for item in release['components']:
            for n,sha in item['hashes'].items():
                if digest(Path(resolve(item['candidate']))/n)!=sha:raise ValueError('Changed release component: '+n)
            components.append(Bundle(item['run'],item['candidate']))
        self.folder=self.root;self.workflow={'content_state':'partial_product_release'}
        self.manifest={'documents':[d for b in components for d in b.docs.values()]}
        self.render_lock=Lock();self.docs={};self.doc_by_identity={}
        for d in self.manifest['documents']:
            identity=(d['document_id'],d['version_id'])
            if identity in self.doc_by_identity:raise ValueError('Duplicate release document version')
            key=f'd{len(self.docs)+1}';self.docs[key]=d;self.doc_by_identity[identity]=key
        for name in ['pages','assets','units','groups','images','search_text']:
            merged={}
            for b in components:
                for key,value in getattr(b,name).items():
                    if key in merged:raise ValueError('Duplicate release identity: '+name)
                    merged[key]=value
            setattr(self,name,merged)
        for name in ['reviews','contexts','images_by_asset','exclusions','chunks_by_page','chunks_by_asset']:
            merged=defaultdict(list)
            for b in components:
                for key,value in getattr(b,name).items():merged[key].extend(value)
            setattr(self,name,merged)
        self.pages_by_doc=defaultdict(list);self.page_by_location={};self.assets_by_page=defaultdict(list)
        for p in self.pages.values():
            self.pages_by_doc[self.doc_by_identity[(p['document_id'],p['version_id'])]].append(p)
            self.page_by_location[(p['document_id'],p['version_id'],p['pdf_page'])]=p['page_id']
        for pages in self.pages_by_doc.values():pages.sort(key=lambda p:p['pdf_page'])
        for a in self.assets.values():self.assets_by_page[self.location(a)].append(a)
        summary=read(self.candidate/'chunks_summary.json')
        for n,sha in summary['artifacts'].items():
            if digest(self.candidate/n)!=sha:raise ValueError('Changed composed artifact: '+n)
        self.chunks_sha256=summary['chunks_sha256'];self.chunk_count=summary['chunks']
        self.report={'content_state':'partial_product_release'};self.release=release


def load_bundle(run,candidate=None):
    return ReleaseBundle(run,candidate) if (Path(run)/'release_manifest.json').is_file() else Bundle(run,candidate)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component',nargs=2,action='append',required=True,metavar=('RUN','CANDIDATE'))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();result=compose(args.component,args.out)
    print(json.dumps(result,ensure_ascii=False,indent=2))
