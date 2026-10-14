"""Read registered artifacts only; rendering never calls OCR or language models."""
from collections import defaultdict
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from threading import Lock

from rag.portable_paths import resolve


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


class Bundle:
    def __init__(self, run, candidate=None):
        self.render_lock=Lock()
        self.root=Path(resolve(run)).resolve()
        self.workflow=read(self.root/'workflow_report.json') if (self.root/'workflow_report.json').is_file() else {}
        self.folder=Path(resolve(self.workflow.get('final_extraction_directory',self.root))).resolve()
        self.manifest=read(self.folder/'extraction_manifest.json')
        if self.manifest.get('status') not in ('partial','extraction_complete'):
            raise ValueError('완료된 전처리 체크포인트를 선택하세요.')
        status_path=self.folder/'processing_status.json'
        if status_path.is_file() and read(status_path).get('lifecycle')!='finished':
            raise ValueError('처리 중인 결과는 열 수 없습니다. 완료된 체크포인트를 선택하세요.')
        integrity=read(self.folder/'extraction_integrity.json')
        required={'extraction_pages.jsonl','extracted_assets.jsonl','source_units.jsonl'}
        if not required<=integrity['files'].keys():raise ValueError('필수 파일의 무결성 정보가 없습니다.')
        for name,value in {**integrity['files'],**integrity.get('visual_context',{}).get('files',{})}.items():
            path=(self.folder/name).resolve()
            if not path.is_relative_to(self.folder) or digest(path)!=value:raise ValueError(f'추출 파일 변경: {name}')
        self.docs={};self.doc_by_identity={}
        for i,doc in enumerate(self.manifest['documents']):
            # Stored paths are the original project's; use this project's copy when there is one (rag/portable_paths.py).
            doc={**doc,'raw_path':str(resolve(doc['raw_path'])),
                'rendered_path':str(resolve(doc['rendered_path'])) if doc.get('rendered_path') else doc.get('rendered_path')}
            if digest(doc['raw_path'])!=doc['version_id']:raise ValueError(f"원본 PDF 변경: {doc['document_id']}")
            if doc.get('rendered_path') and digest(doc['rendered_path'])!=doc.get('rendered_sha256'):
                raise ValueError(f"HTML 렌더 변경: {doc['document_id']}")
            key=f'd{i+1}'
            self.docs[key]=doc;self.doc_by_identity[(doc['document_id'],doc['version_id'])]=key
        self.pages={p['page_id']:p for p in rows(self.folder/'extraction_pages.jsonl')}
        self.assets={a['asset_id']:a for a in rows(self.folder/'extracted_assets.jsonl')}
        self.units={u['unit_id']:u for u in rows(self.folder/'source_units.jsonl')}
        self.groups={g['group_id']:g for g in rows(self.folder/'visual_groups.jsonl')} if (self.folder/'visual_groups.jsonl').is_file() else {}
        self.pages_by_doc=defaultdict(list);self.page_by_location={};self.assets_by_page=defaultdict(list)
        for page in self.pages.values():
            doc=self.doc_by_identity[(page['document_id'],page['version_id'])]
            self.pages_by_doc[doc].append(page)
            self.page_by_location[(page['document_id'],page['version_id'],page['pdf_page'])]=page['page_id']
        for pages in self.pages_by_doc.values():pages.sort(key=lambda p:p['pdf_page'])
        for asset in self.assets.values():
            self.assets_by_page[self.location(asset)].append(asset)
        report_path=self.folder/'processing_report.json'
        self.report=self.workflow or (read(report_path) if report_path.is_file() else {})
        self.reviews=defaultdict(list)
        for review in self.report.get('review_items',[]):self.reviews[review['asset_id']].append(review)
        self.candidate=Path(resolve(candidate or self.workflow.get('candidate_directory',self.root/'candidate'))).resolve()
        self.chunks_by_page=defaultdict(list);self.chunks_by_asset=defaultdict(list)
        self.exclusions=defaultdict(list);self.chunk_count=0;self.chunks_sha256=None
        if (self.candidate/'chunks_summary.json').is_file():
            summary=read(self.candidate/'chunks_summary.json')
            self.chunks_sha256=summary['chunks_sha256']
            if Path(resolve(summary['input_run'])).resolve()!=self.folder:raise ValueError('청크와 전처리 버전이 다릅니다.')
            for name,value in summary['input_hashes'].items():
                path=(self.folder/name).resolve()
                if not path.is_relative_to(self.folder) or digest(path)!=value:raise ValueError(f'청크 입력 변경: {name}')
            for name,value in summary.get('artifacts',{}).items():
                path=(self.candidate/name).resolve()
                if not path.is_relative_to(self.candidate) or digest(path)!=value:raise ValueError(f'청크 파일 변경: {name}')
            for chunk in rows(self.candidate/'chunks.jsonl'):
                self.chunks_by_page[self.location(chunk)].append(chunk);self.chunk_count+=1
                for uid in chunk['representation']['unit_ids']:self.chunks_by_asset[uid].append(chunk)
            exclusions=self.candidate/'chunk_exclusions.jsonl'
            if exclusions.is_file():
                for item in rows(exclusions):self.exclusions[item.get('unit_id',item.get('asset_id',''))].append(item)
        self.contexts=defaultdict(list)
        context_path=self.folder/'visual_context_links.jsonl'
        if context_path.is_file():
            for link in rows(context_path):self.contexts[link['visual_id']].append(link)
        self.search_text={p['page_id']:'\n'.join([p.get('native_page_text','')] +
            [a['selected']['content'] for a in self.assets_by_page[p['page_id']]]).casefold() for p in self.pages.values()}
        self.images={};self.images_by_asset=defaultdict(list)
        allowed=(self.root,self.folder)
        for asset in self.assets.values():
            images=(asset.get('enrichment') or {}).get('image_metadata',{}).get('images',[])
            for index,item in enumerate(images):
                path=Path(resolve(item['path'])).resolve()
                if path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp') or not any(path.is_relative_to(base) for base in allowed):continue
                if not path.is_file():continue
                key=f"{asset['asset_id']}-{index}"
                self.images[key]=path
                self.images_by_asset[asset['asset_id']].append({**{k:item[k] for k in ('role','pdf_page','width','height') if k in item},'url':f'/image/{key}'})

    def location(self,item):
        return self.page_by_location[(item['document_id'],item['version_id'],item['pdf_page'])]

    def asset_meta(self,asset):
        unit=self.units[asset['asset_id']];review=self.reviews.get(asset['asset_id'],[])
        return {**{k:asset.get(k) for k in ('asset_id','asset_type','block_label','block_order','bbox_input_px','input_image_size','logical_group_id','member_asset_ids','is_logical_group')},
            'page_id':self.location(asset),'pdf_page':asset['pdf_page'],
            'origin':unit['content_origin'],'retrieval_eligible':unit['retrieval_eligible'],
            'review_required':bool(review or unit.get('review_required') or unit.get('description_review_required')),
            'review_count':len(review),'snippet':asset['selected']['content'][:180]}

    def page_meta(self,page):
        assets=self.assets_by_page[page['page_id']]
        return {**{k:page.get(k) for k in ('page_id','pdf_page','status','image_size')},
            'asset_count':len(assets),'tables':sum(a['asset_type']=='table' for a in assets),
            'figures':sum(a['asset_type'] in ('figure','figure_group') and not self.units[a['asset_id']].get('retrieval_role')=='logical_figure_member' for a in assets),
            'reviews':sum(len(self.reviews.get(a['asset_id'],[])) for a in assets),
            'chunks':len(self.chunks_by_page[page['page_id']])}

    def summary(self):
        documents=[]
        for key,doc in self.docs.items():
            pages=self.pages_by_doc[key];metadata=doc['metadata']
            documents.append({'key':key,'document_id':doc['document_id'],'version_id':doc['version_id'],
                'title':metadata.get('title') or doc['document_id'],'source':metadata.get('source') or '미상',
                'product':metadata.get('product_id') or '미상','pages':len(pages),'source_kind':doc['kind'],
                'reviews':sum(self.page_meta(p)['reviews'] for p in pages)})
        return {'run_name':self.root.name,'documents':documents,'pages':len(self.pages),'assets':len(self.assets),
            'chunks':self.chunk_count,'review_items':sum(len(v) for v in self.reviews.values()),
            'finished_at':self.workflow.get('finished_at'),'content_state':self.report.get('content_state',self.report.get('state')),
            'read_only':True,'chunks_sha256':self.chunks_sha256,'integrity':'registered_files_and_source_hashes_checked'}

    def page_list(self,doc,query='',kind='all'):
        if doc not in self.docs:raise KeyError(doc)
        result=[]
        for page in self.pages_by_doc[doc]:
            meta=self.page_meta(page)
            if query.casefold() not in self.search_text[page['page_id']]:continue
            if kind=='visual' and not(meta['tables'] or meta['figures']):continue
            if kind=='review' and not meta['reviews']:continue
            result.append(meta)
        return result

    def page_detail(self,key):
        page=self.pages[key];doc=self.doc_by_identity[(page['document_id'],page['version_id'])]
        assets=sorted(self.assets_by_page[key],key=lambda a:(bool(a.get('is_logical_group')),a.get('block_order') is None,a.get('block_order') or 0))
        return {**self.page_meta(page),'doc_key':doc,'image_url':f'/page-image/{key}',
            'pdf_url':f'/source/{doc}.pdf#page={page["pdf_page"]}',
            'source_kind':self.docs[doc]['kind'],
            'locator_notice':'HTML 로컬 렌더 페이지' if self.docs[doc]['kind']=='html' else '원본 PDF 페이지',
            'native_text':page.get('native_page_text',''),'error':page.get('error'),
            'assets':[self.asset_meta(a) for a in assets],
            'chunks':self.chunks_by_page[key],
            'reviews':[r for a in assets for r in self.reviews.get(a['asset_id'],[])]}

    def asset_detail(self,key):
        asset=self.assets[key];group=self.groups.get(asset.get('logical_group_id'),{})
        ids=list(dict.fromkeys([*group.get('caption_part_asset_ids',[]),*group.get('member_asset_ids',[]),
            *group.get('context_asset_ids',[]),*group.get('panel_title_asset_ids',[]),
            *[l['source_unit_id'] for l in self.contexts.get(group.get('group_id',key),[]) if l.get('source_unit_id')]]))
        return {'asset':asset,'unit':self.units[key],'group':group,
            'reviews':self.reviews.get(key,[]),'exclusions':self.exclusions.get(key,[]),
            'chunks':self.chunks_by_asset[key],'images':self.images_by_asset[key],
            'context_links':self.contexts.get(group.get('group_id',key),[]),
            'related':[self.asset_meta(self.assets[uid]) for uid in ids if uid in self.assets and uid!=key]}

    @lru_cache(maxsize=16)
    def render(self,key):
        with self.render_lock:
            return self._render(key)

    def _render(self,key):
        import fitz
        page=self.pages[key];doc=self.docs[self.doc_by_identity[(page['document_id'],page['version_id'])]]
        with fitz.open(doc.get('rendered_path') or doc['raw_path']) as pdf:
            source=pdf[page['pdf_page']-1]
            # Match the actual inference raster, including page rotation.
            width,height=page['image_size']
            pix=source.get_pixmap(matrix=fitz.Matrix(width/source.rect.width,height/source.rect.height),alpha=False)
            if (pix.width,pix.height)!=(width,height):
                from PIL import Image
                from io import BytesIO
                image=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
                output=BytesIO();image.resize((width,height)).save(output,format='PNG');return output.getvalue()
            return pix.tobytes('png')
