"""Resume the isolated 92-document operational build, then validate and publish a separate release."""
import argparse,datetime,json,subprocess,time,traceback
from pathlib import Path
from pipelines.pipeline_h.prepare import ROOT,read,write,prepare

def state(run,stage,status='running',**extra):
    value={'stage':stage,'status':status,'updated_kst':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(),**extra}
    write(run/'pipeline_status.json',value);print(json.dumps(value,ensure_ascii=False),flush=True)

def launch(run,stage,command):
    state(run,stage);logs=run/'logs';logs.mkdir(exist_ok=True);start=time.perf_counter()
    with (logs/(stage+'.log')).open('a',encoding='utf-8') as stream:
        process=subprocess.Popen(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        write(run/(stage+'_process.json'),{'pid':process.pid,'command':command,'started':time.time()})
        while process.poll() is None:time.sleep(5)
    if process.returncode:raise RuntimeError(f'{stage} exited {process.returncode}; see {logs/(stage+".log")}')
    timings=read(run/'stage_timings.json') if (run/'stage_timings.json').exists() else {}
    timings[stage]=timings.get(stage,0)+time.perf_counter()-start;write(run/'stage_timings.json',timings)

def docling_complete(run):
    docs=read(run/'intake_manifest.json')['documents']
    paths=[run/'docling'/f"{i:03d}_{d['version_id']}.json" for i,d in enumerate(docs)]
    return all(p.exists() and read(p).get('status')=='complete' for p in paths)

def execute(run,config,wait_docling=False):
    run=Path(run).resolve();config=Path(config).resolve();run.mkdir(parents=True,exist_ok=True)
    if not (run/'intake_manifest.json').exists():prepare(run)
    if len(read(run/'intake_manifest.json')['documents'])!=92 or (run/'validation_page_plan.json').exists():raise ValueError('Operational run must contain the full approved 92 documents without a test page filter')
    c=read(config);runtime=c['runtimes'];controller=runtime['controller']
    from pipelines.pipeline_h.source_router import ensure
    ensure(run)
    try:
        if wait_docling:
            state(run,'docling_wait',note='Waiting for the already-running isolated Docling worker')
            while not docling_complete(run):
                progress=read(run/'docling_progress.json') if (run/'docling_progress.json').exists() else {}
                if progress.get('completed',0)+progress.get('failed',0)==92:break
                time.sleep(5)
        if not docling_complete(run):launch(run,'docling',[runtime['docling'],'-X','utf8','-m','pipelines.pipeline_h.docling_worker','--run',str(run),'--config',str(config)])
        if not docling_complete(run):raise RuntimeError('Unresolved failed Docling documents; retry retained checkpoints')
        if not (run/'layout_runtime.json').exists():launch(run,'layout',[runtime['paddle'],'-X','utf8','-m','pipelines.pipeline_h.paddle_worker','--run',str(run),'--config',str(config),'--stage','layout'])
        if not (run/'selection_manifest.json').exists():launch(run,'selection',[controller,'-X','utf8','-m','pipelines.pipeline_h.selection','--run',str(run)])
        if not (run/'vlm_runtime.json').exists():launch(run,'vlm',[runtime['paddle'],'-X','utf8','-m','pipelines.pipeline_h.paddle_worker','--run',str(run),'--config',str(config),'--stage','vlm'])
        if read(run/'vlm_runtime.json')['failed']:raise RuntimeError('Failed VLM calls retained for retry, no final promotion')
        if not (run/'extraction/validation_report.json').exists():launch(run,'export',[controller,'-X','utf8','-m','pipelines.pipeline_h.export','--run',str(run)])
        if not (run/'candidate/chunks_summary.json').exists():launch(run,'chunks',[controller,'-X','utf8','-m','pipelines.pipeline_h.chunks','--run',str(run)])
        if not (run/'candidate/emb/bge-m3/meta.json').exists():launch(run,'embedding',[controller,'-X','utf8','-m','pipelines.pipeline_h.finish','--run',str(run),'--stage','embed'])
        launch(run,'validate_release',[controller,'-X','utf8','-m','pipelines.pipeline_h.finish','--run',str(run),'--stage','release'])
        state(run,'finished','complete',report=str(run/'FINAL_REPORT.md'),release=str(run/'release'))
    except BaseException as exc:
        state(run,'error','failed',error=str(exc),traceback=traceback.format_exc());raise

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--config',default=str(ROOT/'pipelines/pipeline_h/config.local.json'))
    p.add_argument('--wait-docling',action='store_true');a=p.parse_args()
    import msvcrt
    folder=Path(a.run).resolve();folder.mkdir(parents=True,exist_ok=True)
    with (folder/'controller.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):lock.write(b'0');lock.flush()
        lock.seek(0)
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:raise SystemExit('This run already has an active controller; inspect pipeline_status.json')
        try:execute(a.run,a.config,a.wait_docling)
        finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
