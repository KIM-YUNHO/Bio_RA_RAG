"""Batch the unchanged selected crops and verify result-to-source identity."""
from pathlib import Path
import time

def build_model(settings):
    from paddleocr import PaddleOCRVL
    return PaddleOCRVL(pipeline_version='v1.6',engine='transformers',device=settings['device'],
        layout_detection_model_name='PP-DocLayoutV3',layout_detection_model_dir=settings['layout_model_dir'],
        vl_rec_model_name='PaddleOCR-VL-1.6-0.9B',vl_rec_model_dir=settings['vl_model_dir'],
        use_doc_orientation_classify=False,use_doc_unwarping=False,use_layout_detection=False,
        use_chart_recognition=False,use_ocr_for_image_block=False,markdown_ignore_labels=[],format_block_content=False,use_queues=False)

def configure(model,size):
    core=getattr(model.paddlex_pipeline,'_pipeline',model.paddlex_pipeline)
    core.batch_sampler.batch_size=size
    rec=core.vl_rec_model
    rec.batch_sampler.batch_size=size
    # The installed Transformers predictor does not request padding. Crops have
    # different image-token lengths; decoder-only generation needs left padding.
    if not getattr(rec,'_h_batch_padding',False):
        original=rec.preprocess_images
        def preprocess(images,**kwargs):
            if len(images)>1:
                rec.processor.tokenizer.padding_side='left'
                kwargs['padding']=True
            return original(images,**kwargs)
        rec.preprocess_images=preprocess
        rec._h_batch_padding=True
    return core

def predict(model,jobs,settings,size):
    if len({j['task'] for j in jobs})!=1:raise ValueError('Batch tasks must match')
    configure(model,size)
    begin=time.perf_counter()
    results=list(model.predict(input=[j['image'] for j in jobs],use_layout_detection=False,
        prompt_label=jobs[0]['task'],use_chart_recognition=False,use_queues=False,
        temperature=0,max_new_tokens=settings['max_new_tokens']))
    elapsed=time.perf_counter()-begin
    if len(results)!=len(jobs):raise ValueError('Batch result count mismatch')
    reports=[]
    for job,result in zip(jobs,results):
        raw=result.json;data=raw.get('res',raw)
        if Path(data['input_path']).resolve()!=Path(job['image']).resolve():
            raise ValueError('Batch result source identity mismatch')
        content='\n'.join(b['block_content'] for b in data['parsing_res_list'])
        reports.append({'status':'complete','job':job,'seconds':elapsed/len(jobs),
            'raw':raw,'content':content,'execution':{'batch_size':len(jobs),
            'batch_elapsed_seconds':elapsed,'timing_is_amortized':len(jobs)>1}})
    return reports,elapsed
