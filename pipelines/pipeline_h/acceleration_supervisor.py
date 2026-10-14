"""Checkpoint this job, benchmark batching, and always resume its controller."""
import argparse,ctypes,json,subprocess,time,traceback
from pathlib import Path
from pipelines.pipeline_h.prepare import read,write,ROOT
from pipelines.pipeline_h.checkpoint_restart import alive,kernel

def main(run,config,worker_pid,controller_pid):
    run=Path(run).resolve();config=Path(config).resolve();settings=read(config)
    audit={'started_unix':time.time(),'reason':'benchmark CUDA crop batching; retain frozen selection and completed responses'}
    write(run/'acceleration_status.json',audit)
    before=read(run/'vlm_progress.json')['completed']
    deadline=time.time()+300
    while time.time()<deadline:
        if read(run/'vlm_progress.json')['completed']>before:break
        if not alive(worker_pid):raise RuntimeError('Worker already exited')
        time.sleep(1)
    else:raise RuntimeError('No new VLM checkpoint; refusing an unverified restart')
    script=f"Get-CimInstance Win32_Process -Filter 'ProcessId = {int(worker_pid)}' | Select-Object CommandLine | ConvertTo-Json -Compress"
    info=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command',script],text=True,encoding='utf-8'))
    command=info.get('CommandLine','')
    if 'pipelines.pipeline_h.paddle_worker' not in command or '--stage vlm' not in command or str(run).casefold() not in command.casefold():
        raise ValueError('Refusing to stop a different worker')
    audit.update(checkpoint_completed=read(run/'vlm_progress.json')['completed'],stopped_worker=worker_pid,status='benchmarking')
    write(run/'acceleration_status.json',audit)
    handle=kernel.OpenProcess(1,0,worker_pid)
    if not handle:raise RuntimeError('Cannot open this job worker')
    try:
        if not kernel.TerminateProcess(handle,72):raise RuntimeError('Cannot restart this worker')
    finally:kernel.CloseHandle(handle)
    for _ in range(30):
        if not alive(controller_pid):break
        time.sleep(1)
    if alive(controller_pid):raise RuntimeError('Controller still active; refusing duplicate work')
    try:
        with (run/'logs/batch_benchmark.log').open('a',encoding='utf-8') as log:
            code=subprocess.call([settings['runtimes']['paddle'],'-u','-X','utf8','-m','pipelines.pipeline_h.benchmark_batch',
                '--run',str(run),'--config',str(config)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=600)
        if code:raise RuntimeError(f'Batch benchmark exited {code}')
        report=read(run/'batch_benchmark/report.json')
        settings['paddle']['vl_rec_batch_size']=report['selected_batch_size']
        audit['selected_batch_size']=report['selected_batch_size']
    except Exception as exc:
        settings['paddle']['vl_rec_batch_size']=1;audit.update(benchmark_error=str(exc),traceback=traceback.format_exc())
    finally:
        resumed_config=run/'accelerated_config.json';write(resumed_config,settings)
        with (run/'controller.log').open('a',encoding='utf-8') as output,(run/'controller.error.log').open('a',encoding='utf-8') as errors:
            process=subprocess.Popen([settings['runtimes']['controller'],'-u','-X','utf8','-m','pipelines.pipeline_h.run',
                '--run',str(run),'--config',str(resumed_config)],cwd=ROOT,stdout=output,stderr=errors,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        audit.update(status='resumed',controller_pid=process.pid,resumed_unix=time.time())
        write(run/'acceleration_status.json',audit)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--config',required=True)
    p.add_argument('--worker-pid',type=int,required=True);p.add_argument('--controller-pid',type=int,required=True)
    a=p.parse_args();main(a.run,a.config,a.worker_pid,a.controller_pid)
