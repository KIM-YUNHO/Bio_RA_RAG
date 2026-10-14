"""Bound unused GPU cache and expose progress without changing OCR/selection rules."""
import sys,time
from pipelines.pipeline_h.prepare import write

def attach(adapter,run):
    import torch
    from ra_prep import extract as extract_module
    progress={'document_id':None,'phase':'initializing','decisions':0}
    last=[0.]
    def record(phase,force=False,**extra):
        now=time.perf_counter()
        if force or now-last[0]>=5:
            write(run/'docling_live_progress.json',{**progress,'phase':phase,'updated_unix':time.time(),**extra,
                  'cuda_allocated_bytes':torch.cuda.memory_allocated(),'cuda_reserved_bytes':torch.cuda.memory_reserved()})
            last[0]=now
    def flush(phase):
        torch.cuda.empty_cache();record(phase,True)
    for mode,converter in adapter.extractor._converters.items():
        original=converter.convert
        def convert(*args,_original=original,_mode=mode,**kwargs):
            record('docling_convert',True,ocr_mode=_mode,page_range=kwargs.get('page_range'))
            result=_original(*args,**kwargs)
            flush('docling_conversion_complete');return result
        converter.convert=convert
    original_second=adapter.extractor._second_reader
    def second(*args,**kwargs):
        flush('independent_page_ocr');return original_second(*args,**kwargs)
    adapter.extractor._second_reader=second
    original_repair=adapter.extractor._repair
    def repair(*args,**kwargs):
        flush('independent_ocr_repair');result=original_repair(*args,**kwargs);flush('independent_ocr_repair_complete');return result
    adapter.extractor._repair=repair
    original_decide=extract_module.decide
    def decide(*args,**kwargs):
        caller=sys._getframe(1);block=caller.f_locals.get('blk',{});cell=caller.f_locals.get('c',{}) if block.get('type')=='table' else {}
        progress['decisions']+=1
        record('independent_ocr_repair',pdf_page=block.get('page'),block_ref=block.get('ref'),cell_rc=[cell.get('r'),cell.get('c')])
        return original_decide(*args,**kwargs)
    extract_module.decide=decide
    return progress
