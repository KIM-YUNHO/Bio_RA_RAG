"""Offline, isolated PP-DocLayoutV3 and selected PaddleOCR-VL workers."""
import argparse,json,os,time,traceback
from pathlib import Path
from pipelines.pipeline_h.prepare import read,write

def main(run,config,stage):
    for name in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE'):os.environ[name]='1'
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
    import fitz
    from PIL import Image
    from paddleocr import LayoutDetection,PaddleOCRVL
    run=Path(run).resolve();settings=read(config)['paddle'];start=time.perf_counter()
    if stage=='layout':
        model=LayoutDetection(model_name='PP-DocLayoutV3',model_dir=settings['layout_model_dir'],engine='transformers',device=settings['device'])
        load=time.perf_counter()-start;reports=[]
        for di,doc in enumerate(read(run/'intake_manifest.json')['documents']):
            with fitz.open(doc.get('rendered_path') or doc['raw_path']) as pdf:
                for number,page in enumerate(pdf,1):
                    target=run/'layout'/f'{di:03d}'/f'{number:04d}.json'
                    if target.exists() and read(target).get('status')=='complete':continue
                    image_path=run/'page_images'/f'{di:03d}'/f'{number:04d}.png';image_path.parent.mkdir(parents=True,exist_ok=True)
                    if not image_path.exists():page.get_pixmap(dpi=150,alpha=False).save(image_path)
                    begin=time.perf_counter()
                    try:
                        result=list(model.predict(input=str(image_path)))
                        if len(result)!=1:raise ValueError('One page result required')
                        raw=result[0].json
                        with Image.open(image_path) as im:pixels=list(im.size)
                        write(target,{'status':'complete','document_id':doc['document_id'],'version_id':doc['version_id'],
                              'pdf_page':number,'image':str(image_path),'pixels':pixels,'size':[page.rect.width,page.rect.height],
                              'boxes':raw.get('res',raw)['boxes'],'raw':raw,'seconds':time.perf_counter()-begin})
                    except Exception as exc:
                        write(target,{'status':'failed','error':str(exc)});raise
                reports.append({'document_id':doc['document_id'],'pages':len(pdf)})
            write(run/'layout_progress.json',{'completed_documents':di+1,'total_documents':92,'documents':reports})
            print(f'LAYOUT {di+1}/92 {doc["document_id"]}',flush=True)
        write(run/'layout_runtime.json',{'model':'PP-DocLayoutV3','startup_seconds':load,'elapsed_seconds':time.perf_counter()-start,'dpi':150})
    else:
        from pipelines.pipeline_h.vlm_batch import build_model,configure,predict
        model=build_model(settings);batch_size=int(settings.get('vl_rec_batch_size',1))
        core=configure(model,batch_size)
        param=next(core.vl_rec_model.infer.parameters())
        if settings['device'].startswith('gpu') and param.device.type!='cuda':raise ValueError('Requested GPU model is not on CUDA')
        load=time.perf_counter()-start;jobs=read(run/'selection_manifest.json')['jobs'];reports=[];pending=[];fallbacks=[];cache={};aliases={};inferred=0;exact_reused=0
        from preprocess.ingest import sha256
        for job in jobs:
            target=run/'vlm'/(job['id']+'.json')
            if target.exists() and read(target).get('status')=='complete':
                saved=read(target);reports.append(saved);cache[(job['task'],job['image_sha256'])]=saved;continue
            if sha256(job['image'])!=job['image_sha256']:raise ValueError('Selected crop changed')
            pending.append(job)
        resumed_count=len(reports)
        def save_report(report):
            job=report['job'];write(run/'vlm'/(job['id']+'.json'),report);reports.append(report)
            write(run/'vlm_progress.json',{'completed':len(reports),'total':len(jobs),'failed':sum(r['status']!='complete' for r in reports),
                'batch_size':batch_size,'model_device':str(param.device),'updated_unix':time.time(),'exact_crop_reused':exact_reused})
            print(f'VLM {len(reports)}/{len(jobs)} {job["task"]} {job["id"]} {report["status"]} {report["seconds"]:.1f}s',flush=True)
        def reuse(job,source):
            nonlocal exact_reused
            path=run/'vlm'/(source['job']['id']+'.json');exact_reused+=1
            # Keep the actual raw response and its original input path intact;
            # the new job has its own source PDF, crop and separately run gate.
            save_report({'status':'complete','job':job,'seconds':0,'raw':source['raw'],'content':source['content'],
                'execution':{'mode':'byte_identical_crop_reuse','original_response_path':str(path),
                    'original_response_sha256':sha256(path),'image_sha256':job['image_sha256'],'task':job['task']}})
        unique=[]
        for job in pending:
            key=(job['task'],job['image_sha256'])
            if key in cache:reuse(job,cache[key])
            elif key in aliases:aliases[key].append(job)
            else:unique.append(job);aliases[key]=[]
        pending=unique
        # Similar image areas reduce wasted decoder padding, while IDs, source
        # crops, model options and the frozen selection remain unchanged.
        pending.sort(key=lambda j:(j['task'],j['pixels'][0]*j['pixels'][1]))
        def execute_batch(batch):
            nonlocal batch_size
            begin=time.perf_counter()
            try:
                result,_=predict(model,batch,settings,min(batch_size,len(batch)))
                return result
            except Exception as exc:
                if len(batch)>1:
                    fallbacks.append({'job_ids':[j['id'] for j in batch],'error':str(exc)})
                    write(run/'vlm_batch_fallbacks.json',fallbacks)
                    import torch
                    torch.cuda.empty_cache()
                    middle=len(batch)//2
                    batch_size=min(batch_size,middle)
                    return execute_batch(batch[:middle])+execute_batch(batch[middle:])
                return [{'status':'failed','job':batch[0],'seconds':time.perf_counter()-begin,'error':str(exc),'traceback':traceback.format_exc()}]
        while pending:
            count=min(batch_size,len(pending))
            while pending[count-1]['task']!=pending[0]['task']:count-=1
            batch=pending[:count];pending=pending[count:]
            for report in execute_batch(batch):
                job=report['job'];inferred+=1;save_report(report)
                for alias in aliases[(job['task'],job['image_sha256'])]:
                    if report['status']=='complete':reuse(alias,report)
                    else:save_report({**report,'job':alias,'seconds':0})
        write(run/'vlm_runtime.json',{'model':'PaddleOCR-VL-1.6-0.9B','startup_seconds':load,'elapsed_seconds':time.perf_counter()-start,
                                    'calls':len(jobs),'failed':sum(r['status']!='complete' for r in reports),'max_new_tokens':settings['max_new_tokens'],
                                    'batch_size':batch_size,'model_device':str(param.device),'model_dtype':str(param.dtype),
                                    'batch_fallbacks':len(fallbacks),'reused_completed_calls':resumed_count,
                                    'new_model_inferred_calls':inferred,'exact_crop_reused':exact_reused,
                                    'saved_call_seconds':sum(r['seconds'] for r in reports)})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--config',required=True);p.add_argument('--stage',choices=['layout','vlm'],required=True)
    a=p.parse_args();main(a.run,a.config,a.stage)
