"""Read-only production workers with CUDA parameter verification for benchmark runs."""
import json,sys,os
from pathlib import Path
from .gpu import torch_devices

def main():
    settings=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    if settings['device']!='gpu:0':raise RuntimeError('Benchmark requires gpu:0')
    if settings['parser_backend']=='cascade':
        from preprocess.ocr import cascade_worker as worker
        original_init=worker.CascadeParser.__init__
        def initialize(self,*args,**kwargs):
            original_init(self,*args,**kwargs)
            # Installed Transformers OCR resizes every image to the widest
            # image's shape. Mixed-width batches stretch short numeric crops.
            # Single-image batches preserve the source aspect ratio without
            # replacing weights or permitting CPU model inference.
            batch=int(os.environ.get('BENCHMARK_CASCADE_OCR_BATCH','1'))
            if batch not in (1,16):raise ValueError('Unknown comparison OCR batch size')
            self.ocr.pipeline.paddlex_pipeline.text_rec_model.batch_sampler.batch_size=batch
        worker.CascadeParser.__init__=initialize
        original=worker.CascadeParser.parse
        def parse(self,*args,**kwargs):
            import torch
            raw=original(self,*args,**kwargs)
            raw['ra_runtime']['gpu_models']={'ocr':torch_devices(self.ocr.pipeline),
                'layout':torch_devices(self.layout) if self.layout is not None else {}}
            raw['ra_runtime']['cuda_peak_allocated_bytes']=torch.cuda.max_memory_allocated()
            raw['ra_runtime']['benchmark_ocr_recognition_batch_size']=self.ocr.pipeline.paddlex_pipeline.text_rec_model.batch_sampler.batch_size
            raw['ra_runtime']['benchmark_ocr_batch_reason']='installed_transformers_mixed_width_resize_compatibility' if raw['ra_runtime']['benchmark_ocr_recognition_batch_size']==1 else 'current_production_batch_size'
            return raw
        worker.CascadeParser.parse=parse
    else:
        from preprocess.ocr import paddle_worker as worker
        import paddleocr
        original=paddleocr.PaddleOCRVL
        def construct(*args,**kwargs):
            model=original(*args,**kwargs)
            print('BENCHMARK_CUDA_MODELS '+json.dumps(torch_devices(model)),file=sys.stderr,flush=True)
            return model
        paddleocr.PaddleOCRVL=construct
    worker.main()

if __name__=='__main__':main()
