import time,fitz,os
from ..common import BENCH,ROOT,write,parse_table,sha

FURNITURE={'header','footer','page_header','page_footer','number','page_number'}

class Paddle:
    def __init__(self,candidate,work): self.candidate=candidate;self.work=work
    def start(self,mode):
        from compare_study.settings import extraction_config
        from preprocess.paddle_adapter import PaddleAdapter
        self.config=extraction_config()
        self.config.paddle.parser_backend='cascade' if self.candidate=='CUR' else 'paddle_vl'
        self.config.paddle.native_text=True
        self.config.paddle.release_qwen_before_parser=False
        t=time.perf_counter();self.parser=PaddleAdapter(self.config.paddle,self.work/'worker')
        # Substitute only the benchmark-owned worker command; production files stay read-only.
        import subprocess
        original=subprocess.Popen
        def launch(args,*pos,**kw):
            if isinstance(args,list) and '-m' in args and args[args.index('-m')+1] in ['preprocess.cascade_worker','preprocess.paddle_worker']:
                args=args[:];args[args.index('-m')+1]='compare_study.bench.paddle_gpu_worker'
            return original(args,*pos,**kw)
        subprocess.Popen=launch
        try:self.parser._start()
        finally:subprocess.Popen=original
        load=time.perf_counter()-t
        return {'model_load_seconds':load,'development_warmup_seconds':0,
            'settings':self.config.to_dict(),'gpu_verification':'benchmark worker verifies all torch model parameters are CUDA',
            'ocr_recognition_batch_size':int(os.environ.get('BENCHMARK_CASCADE_OCR_BATCH','1')) if self.candidate=='CUR' else None,
            'ocr_batch_note':('benchmark runtime compatibility fix; production config unchanged' if int(os.environ.get('BENCHMARK_CASCADE_OCR_BATCH','1'))==1 else 'current production batch size; complete source stages imported unchanged'),
            'load_note':'layout lazy-load occurs during the first input page; no fixed development PDF is used'}
    def close(self): self.parser.close()
    def document(self,path,doc,page_map,work,mode,selected_pages=None):
        from preprocess.native_page import parse as native_page
        from preprocess.hybrid import normalize_page,pdf_box
        from preprocess.ingest import Document
        document=Document(doc['document_id'],sha(path),'pdf',str(path),str(path),[str(path)],
            {'source':doc['agency'],'document_type':doc['doc_type'],'model_execution_allowed':True})
        out=[];all_assets=[];all_units=[];page_records={}
        with fitz.open(path) as pdf:
            physical_pages=selected_pages or list(range(1,len(pdf)+1))
            if len(physical_pages)!=len(page_map):raise ValueError('Physical page map mismatch')
            for i,physical_page in enumerate(physical_pages):
                page=pdf[physical_page-1]
                t=time.perf_counter();pix=page.get_pixmap(dpi=self.config.paddle.dpi,alpha=False)
                image=work/f'p{i+1:03d}.png';pix.save(image);size=[pix.width,pix.height]
                # Standalone PVL always runs layout; current reproduces native prose bypass.
                raw=native_page(page,size) if self.candidate=='CUR' else None
                if raw is None:raw=self.parser.parse_page(image,page)
                write(work/f'p{i+1:03d}_raw.json',raw)
                row={'document':doc['key'],'page':page_map[i],'text':[],'tables':[],'table_bboxes':[],
                    'flags':{'runtime':raw.get('ra_runtime'),'raw_path':str(work/f'p{i+1:03d}_raw.json'),'image_size':size}}
                if self.candidate=='CUR':
                    assets,invalid=normalize_page(document,page,image,size,raw,'compare_study')
                    if invalid:raise RuntimeError(f'Invalid current blocks: {invalid}')
                    write(work/f'p{i+1:03d}_assets.json',assets)
                    all_assets.extend(assets)
                    all_units.extend({'unit_id':a['asset_id'],'document_id':a['document_id'],'version_id':a['version_id'],'kind':a['asset_type'],'text':a['selected']['content']} for a in assets)
                    page_records[(document.document_id,document.version_id,physical_page)]={}
                    blocks=[{'block_label':a['block_label'],'block_content':a['selected']['content'],'bbox':a['bbox_pdf'],'asset_id':a['asset_id']} for a in assets if a['asset_type']!='figure']
                else:
                    blocks=[{**b,'bbox':pdf_box(page,b['block_bbox'],size)} for b in raw.get('res',raw)['parsing_res_list']]
                    from preprocess.visual_groups import build_visual_groups
                    from compare_study.figures import from_assets
                    visual_assets,invalid=normalize_page(document,page,image,size,raw,'paddle_visual_output_v2')
                    if invalid:raise RuntimeError(f'Invalid visual output: {invalid}')
                    groups,visual_jobs=build_visual_groups(visual_assets)
                    row['figures']=from_assets(visual_assets+visual_jobs,physical_page,'PaddleOCR-VL-1.6-0.9B')
                    for f in row['figures']:
                        members=f.get('member_ids') or [f['figure_id']]
                        pieces=[a['base']['content'] for a in visual_assets if a['asset_id'] in members]
                        if self.config.paddle.chart_recognition and pieces:
                            f.update(interpretation={'task':'chart_transcription','content':'\n'.join(pieces),
                                'semantic_status':'unverified; generated chart values are not printed-value gold'},
                                status='needs_review')
                            f['interpretation_warning']='Chart task can generate estimated curve values; these are not printed source values and require visual review.'
                row['layout_tables']=[];row['text_assets']=[]
                for b in blocks:
                    label=b['block_label'];content=b['block_content'];box=b['bbox']
                    if label=='table':
                        row['layout_tables'].append({'bbox':box,'content':content})
                        row['tables'].append(parse_table(content));row['table_bboxes'].append(box)
                    elif label not in ({'image','chart','picture','figure'} | (FURNITURE if self.candidate!='CUR' else set())) and content.strip():
                        row['text'].append(content);row['text_assets'].append(b.get('asset_id'))
                row['seconds']=time.perf_counter()-t;out.append(row)
                from compare_study.figures import figure
                for b in raw.get('res',raw)['parsing_res_list']:
                    if b['block_label']=='header_image':
                        row.setdefault('figures',[]).append(figure(f"header_{i}_{len(row.get('figures',[]))}",
                            pdf_box(page,b['block_bbox'],size),'header_image',b['block_content'],model='PP-DocLayoutV3',status='decorative'))
                print(f"{self.candidate} {doc['key']} p{page_map[i]} {row['seconds']:.2f}s",flush=True)
        if self.candidate=='CUR':
            # Read-only furniture helper; no chunks, tokenizer, embeddings or retrieval are run.
            from preprocess.furniture import furniture_units,CONFIG
            from preprocess.visual_groups import build_visual_groups
            groups,jobs=build_visual_groups(all_assets)
            from .cascade_stages import refine_regions
            stage_summary=refine_regions(all_assets,groups,path,work,self.parser)
            groups,jobs=build_visual_groups(all_assets)
            for i,row in enumerate(out):
                selected=[a for a in all_assets if a['pdf_page']==physical_pages[i] and a['asset_type']=='table']
                row['tables']=[parse_table(a['selected']['content']) for a in selected]
                row['table_bboxes']=[a['bbox_pdf'] for a in selected]
                row['layout_tables']=[{'bbox':a['bbox_pdf'],'content':a['selected']['content']} for a in selected]
                row['flags']['current_stages']=stage_summary
                row['seconds']+=stage_summary['wall_seconds']/len(out)
                write(work/f'p{i+1:03d}_refined_assets.json',[a for a in all_assets if a['pdf_page']==physical_pages[i]])
            write(work/'cascade_assets.json',{'assets':all_assets+jobs,'groups':groups,'document':document.to_dict()})
            from compare_study.figures import from_assets
            for i,row in enumerate(out):
                row['figures']=row.get('figures',[])+from_assets(all_assets+jobs,physical_pages[i],'PP-OCRv5 mobile')
            excluded=furniture_units(all_units,{a['asset_id']:a for a in all_assets},page_records,CONFIG)
            for row in out:
                row['text']=[t for t,aid in zip(row['text'],row['text_assets']) if aid not in excluded]
                row['flags']['furniture_exclusions']=sum(aid in excluded for aid in row['text_assets'])
        from compare_study.figures import finalize
        for i,row in enumerate(out):
            row.pop('text_assets',None)
            finalize(row,path,physical_pages[i],work)
        return out
