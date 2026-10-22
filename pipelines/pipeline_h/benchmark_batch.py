"""Measure unchanged crop batching against saved serial source candidates."""
import argparse,os,time,traceback
from pathlib import Path
from pipelines.pipeline_h.prepare import read,write
from pipelines.pipeline_h.vlm_batch import build_model,configure,predict
from pipelines.pipeline_h.structure import parsed_rows
from pipelines.pipeline_h.policy import candidate_gate
from preprocess.tables.table_quality import comparable_text,numbers

def main(run,config):
    for name in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_DATASETS_OFFLINE'):os.environ[name]='1'
    os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
    run=Path(run);settings=read(config)['paddle'];target=run/'batch_benchmark';target.mkdir(exist_ok=True)
    saved=[read(p) for p in sorted((run/'vlm').glob('*.json'))]
    saved=[r for r in saved if r['status']=='complete' and r['job']['task']=='table']
    # Cover both existing-table repair and independently added source regions.
    replacements=[r for r in saved if r['job']['action']=='replace']
    additions=[r for r in saved if r['job']['action']=='add']
    chosen=(replacements[:2]+additions[:2])
    if len(chosen)<4:chosen=saved[:4]
    jobs=[r['job'] for r in chosen];start=time.perf_counter();model=build_model(settings)
    core=configure(model,1);rec=core.vl_rec_model
    import torch
    param=next(rec.infer.parameters())
    if param.device.type!='cuda':raise ValueError('VLM is not on CUDA')
    actual_batches=[];original_process=rec.process
    def process(data,**kwargs):
        actual_batches.append(len(data));return original_process(data,**kwargs)
    rec.process=process
    report={'model_device':str(param.device),'dtype':str(param.dtype),
        'attention':getattr(rec.infer.config,'_attn_implementation',None),
        'startup_seconds':time.perf_counter()-start,'job_ids':[j['id'] for j in jobs],
        'saved_serial_seconds':sum(r['seconds'] for r in chosen),'trials':[],'selected_batch_size':1}
    write(target/'report.json',report)
    for size in (2,4):
        try:
            torch.cuda.reset_peak_memory_stats();actual_batches.clear();outputs=[];elapsed=0
            for offset in range(0,len(jobs),size):
                batch,seconds=predict(model,jobs[offset:offset+size],settings,size);outputs+=batch;elapsed+=seconds
            comparisons=[]
            for old,new in zip(chosen,outputs):
                rows=parsed_rows(new['content']);oldrows=parsed_rows(old['content'])
                gate=candidate_gate(rows,new['content'],new['job'].get('original_table'))
                oldgate=candidate_gate(oldrows,old['content'],old['job'].get('original_table'))
                comparisons.append({'id':new['job']['id'],
                    'same_normalized_content':comparable_text(new['content'])==comparable_text(old['content']),
                    'same_numeric_multiset':numbers(new['content'])==numbers(old['content']),
                    'same_gate':gate['accepted']==oldgate['accepted'],
                    'same_rows':rows==oldrows})
                write(target/f"batch{size}_{new['job']['id']}.json",new)
            # Preserve the entire transcribed table, not only a numeric aggregate.
            safe=all(c['same_rows'] and c['same_numeric_multiset'] and c['same_gate'] for c in comparisons)
            trial={'batch_size':size,'seconds':elapsed,'actual_model_batch_sizes':list(actual_batches),
                'peak_cuda_reserved_bytes':torch.cuda.max_memory_reserved(),
                'speedup_vs_saved_serial':report['saved_serial_seconds']/elapsed,'comparisons':comparisons,
                'accepted':safe and elapsed<report['saved_serial_seconds']*.9 and max(actual_batches)>1}
            report['trials'].append(trial)
            if trial['accepted']:report['selected_batch_size']=size
        except Exception as exc:
            report['trials'].append({'batch_size':size,'accepted':False,'error':str(exc),'traceback':traceback.format_exc()})
            torch.cuda.empty_cache()
        write(target/'report.json',report);print(report['trials'][-1],flush=True)
    print('SELECTED',report['selected_batch_size'],flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--config',required=True)
    a=p.parse_args();main(a.run,a.config)
