"""Controlled ROI experiment. One cached detector/OCR pass; identical crop pixels, internal OCR disclosed."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import fitz
from PIL import Image
from .settings import ROOT,activate,configuration
from .bench.common import read,write,sha,parse_table


def prepare(run,target):
    target=Path(target);target.mkdir(parents=True,exist_ok=False)
    start=time.perf_counter();jobs=[]
    for job in read(Path(run)/'inputs.json')['native']:
        key=job['document']['key']
        bundle=read(Path(run)/'native/CUR/raw'/key/'cascade_assets.json')
        write(target/'common_ocr'/f'{key}.json',bundle)
        for a in bundle['assets']:
            task=a.get('route',{}).get('task')
            if task not in ('repair_table','describe_figure'):continue
            if task=='describe_figure' and not a.get('is_logical_group') and a.get('route',{}).get('parent_asset_id'):continue
            identity=f"{key}_p{a['pdf_page']}_{task}"
            bbox=a['bbox_pdf'];png=target/(identity+'.png');pdfpath=target/(identity+'.pdf')
            with fitz.open(job['path']) as pdf:
                pix=pdf[a['pdf_page']-1].get_pixmap(dpi=300,clip=fitz.Rect(bbox),alpha=False)
                image=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
                image.thumbnail((1800,1800),Image.Resampling.LANCZOS);image.save(png)
            with fitz.open() as crop_pdf:
                p=crop_pdf.new_page(width=bbox[2]-bbox[0],height=bbox[3]-bbox[1])
                p.insert_image(p.rect,filename=str(png));crop_pdf.save(pdfpath)
            jobs.append({'id':identity,'task':task,'source_document':Path(job['original_path']).name,
                'source_page':job['page_map'][a['pdf_page']-1],'bbox':bbox,'image':str(png.resolve()),
                'image_sha256':sha(png),'crop_pdf':str(pdfpath.resolve()),'crop_pdf_sha256':sha(pdfpath),
                'pixels':list(image.size),'render_dpi':300,'max_edge':1800,
                'effective_dpi':image.width/(bbox[2]-bbox[0])*72,
                'common_ocr':a['base'].get('printed_text_reference') or a['base']['content'],
                'complexity_route':a['route'],'source_asset':a,'document':job['document']})
    cur=read(Path(run)/'native/CUR/run.json')
    manifest={'source_run':str(Path(run).resolve()),'jobs':jobs,'prepare_seconds':time.perf_counter()-start,
        'common_frontend_processing_seconds':cur['processing_seconds'],
        'common_frontend_total_seconds':cur['total_seconds'],
        'common_frontend':'PP-DocLayoutV3 + PP-OCRv5 mobile; source CUR pass reused once, not charged three times',
        'selection':'only routed repair_table and caption-group describe_figure; no gold-assisted ROI selection',
        'scope':'same crop pixels/geometry; Paddle layout bypass, Qwen direct crop; Docling retains internal layout/RapidOCR/TableFormer'}
    write(target/'inputs.json',manifest)
    return manifest


def execute(case,target,config):
    activate(config)
    sys.path.insert(0,str(ROOT/'compare_study/bench/vendor'))
    target=Path(target);manifest=read(target/'inputs.json');out=target/case;out.mkdir(exist_ok=False)
    if case=='paddle':
        from .bench.adapters.paddle import Paddle
        adapter=Paddle('PVL',out)
    elif case=='qwen':
        from .bench.adapters.cascade_qwen import CascadeQwen
        adapter=CascadeQwen()
    else:
        from .bench.adapters.docling import Docling
        adapter=Docling('cuda')
    record={'case':case,'status':'running','jobs':[]};begin=time.perf_counter()
    write(out/'run.json',record)
    try:
        record['startup']=adapter.start('image');record['startup_seconds']=time.perf_counter()-begin
        for job in manifest['jobs']:
            if sha(job['image'])!=job['image_sha256'] or sha(job['crop_pdf'])!=job['crop_pdf_sha256']:
                raise ValueError('Frozen ROI input changed')
            start=time.perf_counter();result={'id':job['id'],'task':job['task'],'image_sha256':job['image_sha256']}
            if case=='paddle':
                raw=adapter.parser._predict(job['image'],'chart' if job['task']=='describe_figure' else 'table')
                content='\n'.join(b['block_content'] for b in raw.get('res',raw)['parsing_res_list'])
                result.update(raw=raw,content=content,tables=[parse_table(content)] if job['task']=='repair_table' else [],
                    interpretation={'task':'chart_transcription','content':content} if job['task']=='describe_figure' else None,
                    status='needs_review' if job['task']=='describe_figure' else 'transcribed_unverified',model='PaddleOCR-VL-1.6-0.9B')
            elif case=='qwen':
                from preprocess.qwen_adapter import validate_answer
                context={'target_page':job['source_page'],'context_items':[],
                    'image_roles':[{'role':'target_crop','pdf_page':job['source_page']}],
                    'table_layout':{'column_count_hint':len(parse_table(job['source_asset']['base']['content'])[0]) if parse_table(job['source_asset']['base']['content']) else None}}
                reply=adapter.qwen.generate(job['task'],[Path(job['image']).read_bytes()],context)
                checked=validate_answer(job['task'],reply,'',job['common_ocr'],context)
                answer=checked['answer']
                valid=not checked['issues']
                result.update(raw=reply,validation=checked,status='heuristic_checks_passed' if valid else 'needs_review',
                    candidate_tables=[parse_table(answer['table_html'])] if answer and job['task']=='repair_table' else [],
                    tables=[parse_table(answer['table_html'])] if valid and job['task']=='repair_table' else [],
                    interpretation=answer if job['task']=='describe_figure' else None,model=adapter.config.qwen.model)
            else:
                work=out/job['id'];work.mkdir()
                doc={**job['document'],'key':job['id']}
                rows=adapter.document(Path(job['crop_pdf']),doc,[1],work,'image')
                result.update(pages=rows,tables=rows[0]['tables'],status='unsupported_interpretation' if job['task']=='describe_figure' else 'extracted_unverified',
                    interpretation=None,model='RapidOCR + TableFormer accurate + ra_prep',
                    note='same crop content; Docling repeats internal OCR/layout, external tokens are not injected')
            result['seconds']=time.perf_counter()-start
            write(out/(job['id']+'.json'),result)
            record['jobs'].append({'id':job['id'],'seconds':result['seconds'],'status':result['status']})
            write(out/'run.json',record);print(case,job['id'],result['status'],result['seconds'],flush=True)
        record['status']='complete'
    except BaseException as exc:
        record.update(status='failed',error=f'{type(exc).__name__}: {exc}');raise
    finally:
        adapter.close();record['total_seconds']=time.perf_counter()-begin
        record['processing_seconds']=sum(j['seconds'] for j in record['jobs'])
        write(out/'run.json',record)


def launch(target,config):
    config=configuration(config)
    for case in ('paddle','qwen','docling'):
        runtime=config['runtimes']['docling' if case=='docling' else 'controller']
        with (Path(target)/(case+'.log')).open('w',encoding='utf-8') as stream:
            result=subprocess.run([runtime,'-B','-X','utf8','-m','compare_study.roi','worker',
                '--case',case,'--target',str(Path(target).resolve()),'--config',config['_path']],
                cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise RuntimeError(f'ROI {case} failed; inspect {target}/{case}.log')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','worker','run'])
    p.add_argument('--run');p.add_argument('--target',required=True);p.add_argument('--config')
    p.add_argument('--case',choices=['paddle','qwen','docling']);args=p.parse_args()
    if args.command=='prepare':prepare(args.run,args.target)
    elif args.command=='worker':execute(args.case,args.target,args.config)
    else:launch(args.target,args.config)
