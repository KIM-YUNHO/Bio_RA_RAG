"""Restart this job only after a completed document, preserving every saved extraction."""
import argparse,ctypes,json,subprocess,time
from pathlib import Path
from pipelines.pipeline_h.prepare import read,write,ROOT

kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.OpenProcess.restype=ctypes.c_void_p
kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
kernel.CloseHandle.argtypes=[ctypes.c_void_p]
kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
kernel.TerminateProcess.argtypes=[ctypes.c_void_p,ctypes.c_uint]

def alive(pid):
    handle=kernel.OpenProcess(0x1000,0,pid)
    if not handle:return False
    try:
        code=ctypes.c_ulong();return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
    finally:kernel.CloseHandle(handle)

def main(run,worker_pid,controller_pid,index):
    run=Path(run).resolve();doc=read(run/'intake_manifest.json')['documents'][index]
    checkpoint=run/'docling'/f"{index:03d}_{doc['version_id']}.json"
    while True:
        try:
            if checkpoint.exists() and read(checkpoint).get('status')=='complete':break
        except (ValueError,PermissionError):pass
        if not alive(worker_pid):raise RuntimeError('Worker exited before the requested checkpoint')
        time.sleep(5)
    script=f"Get-CimInstance Win32_Process -Filter 'ProcessId = {int(worker_pid)}' | Select-Object CommandLine | ConvertTo-Json -Compress"
    info=json.loads(subprocess.check_output(['powershell','-NoProfile','-Command',script],text=True,encoding='utf-8'))
    command=info.get('CommandLine','')
    if 'pipelines.pipeline_h.docling_worker' not in command or str(run).casefold() not in command.casefold():raise ValueError('Refusing to stop a different process')
    write(run/'checkpoint_restart.json',{'reason':'release unused PyTorch CUDA cache before independent OCR and expose per-page progress',
        'completed_document_id':doc['document_id'],'completed_checkpoint':str(checkpoint),'worker_pid':worker_pid,
        'original_algorithms_and_model_settings_unchanged':True,'started_unix':time.time()})
    handle=kernel.OpenProcess(1,0,worker_pid)
    if not handle:raise RuntimeError('Cannot open this worker for restart')
    try:
        if not kernel.TerminateProcess(handle,71):raise RuntimeError('Cannot stop this worker')
    finally:kernel.CloseHandle(handle)
    for _ in range(30):
        if not alive(controller_pid):break
        time.sleep(2)
    if alive(controller_pid):raise RuntimeError('Original controller did not exit, refusing a duplicate run')
    config=ROOT/'pipelines/pipeline_h/config.local.json';c=read(config)
    with (run/'controller.log').open('a',encoding='utf-8') as output,(run/'controller.error.log').open('a',encoding='utf-8') as errors:
        process=subprocess.Popen([c['runtimes']['controller'],'-u','-X','utf8','-m','pipelines.pipeline_h.run','--run',str(run),'--config',str(config)],
            cwd=ROOT,stdout=output,stderr=errors,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    write(run/'resumed_controller_process.json',{'pid':process.pid,'checkpoint_preserved':str(checkpoint),'resumed_unix':time.time()})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--worker-pid',type=int,required=True)
    p.add_argument('--controller-pid',type=int,required=True);p.add_argument('--after-document-index',type=int,required=True);a=p.parse_args()
    main(a.run,a.worker_pid,a.controller_pid,a.after_document_index)
