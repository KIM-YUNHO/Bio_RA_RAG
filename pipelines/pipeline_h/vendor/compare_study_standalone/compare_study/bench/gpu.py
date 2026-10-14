"""CUDA inference setup isolated to the benchmark, with fail-closed provider checks."""
import os
from .common import ROOT

_dll_handles=[]

def prepare_cuda():
    import torch
    import onnxruntime as ort
    if not torch.cuda.is_available():raise RuntimeError('CUDA torch unavailable')
    folder=__import__('pathlib').Path(torch.__file__).resolve().parent/'lib'
    if hasattr(os,'add_dll_directory'):_dll_handles.append(os.add_dll_directory(str(folder)))
    ort.preload_dlls()
    if 'CUDAExecutionProvider' not in ort.get_available_providers():raise RuntimeError('ORT CUDA unavailable')
    return {'torch':torch.__version__,'cuda':torch.version.cuda,'gpu':torch.cuda.get_device_name(0),'onnxruntime':ort.__version__}

def providers(reader):
    result={}
    for name in ['text_det','text_cls','text_rec']:
        getattr(reader,{'text_det':'_load_det_model','text_cls':'_load_cls_model','text_rec':'_load_rec_model'}[name])()
        session=getattr(reader,name).session.session
        ps=session.get_providers()
        if not ps or ps[0]!='CUDAExecutionProvider':raise RuntimeError(f'{name} did not select CUDA: {ps}')
        result[name]=ps
    return result

def second_reader(cuda_options=None):
    runtime=prepare_cuda()
    from rapidocr import RapidOCR
    from ra_prep import reocr
    if reocr._ENGINE is None:
        params={'Global.log_level':'error','EngineConfig.onnxruntime.use_cuda':True,
            'EngineConfig.onnxruntime.cuda_ep_cfg.device_id':0}
        from compare_study.settings import configuration
        settings=configuration()['docling']
        for stage,key in [('Det','det_model_path'),('Cls','cls_model_path'),('Rec','rec_model_path')]:
            params[stage+'.model_path']=settings[key]
        for key,value in (cuda_options or {}).items():params['EngineConfig.onnxruntime.cuda_ep_cfg.'+key]=value
        reocr._ENGINE=RapidOCR(params=params)
    return reocr._ENGINE,{'runtime':runtime,'providers':providers(reocr._ENGINE)}

def torch_devices(root):
    import torch
    found={};seen=set()
    def visit(obj,path,depth):
        if id(obj) in seen or depth>7:return
        seen.add(id(obj))
        if isinstance(obj,torch.nn.Module):
            devices=sorted({str(p.device) for p in obj.parameters()})
            if devices:
                if any(not d.startswith('cuda') for d in devices):raise RuntimeError(f'Non-CUDA model {path}: {devices}')
                found[path]=devices
            return
        if isinstance(obj,dict):items=obj.items()
        elif isinstance(obj,(list,tuple)):items=enumerate(obj)
        elif hasattr(obj,'__dict__'):items=vars(obj).items()
        else:return
        for key,val in items:visit(val,path+'.'+str(key),depth+1)
    visit(root,'stage',0)
    if not found:raise RuntimeError('Could not verify torch model devices')
    return found
