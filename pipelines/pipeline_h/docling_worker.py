"""Persistent isolated GPU extraction; per-document checkpoints and raw structural cells."""
import argparse,json,sys,time,traceback
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'pipelines/pipeline_h/vendor/compare_study_standalone'))
sys.path.insert(0,str(ROOT/'pipelines/pipeline_h/vendor/compare_study_standalone/compare_study/bench/vendor'))
from compare_study.settings import activate
from compare_study.bench.adapters.docling import Docling
from compare_study.bench.common import read
from pipelines.pipeline_h.prepare import write
from ra_prep.pipeline import process_document
from ra_prep.select import Selection

def main(run,config):
    run=Path(run).resolve();activate(config,run)
    intake=read(run/'intake_manifest.json')
    adapter=Docling('cuda');startup=adapter.start('native')
    from pipelines.pipeline_h.docling_runtime import attach
    live=attach(adapter,run)
    startup['cache_management']='release unused PyTorch CUDA cache between conversion and independent OCR; weights, crop scales and decision rules unchanged'
    write(run/'docling_runtime.json',startup)
    reports=[]
    for index,doc in enumerate(intake['documents']):
        target=run/'docling'/f"{index:03d}_{doc['version_id']}.json"
        if target.exists():
            stored=read(target)
            if stored.get('status')=='complete':reports.append(stored['summary']);continue
        started=time.perf_counter()
        live.update(document_id=doc['document_id'],decisions=0)
        print(f"DOCLING {index+1}/{len(intake['documents'])} {doc['metadata']['product_id']} {doc['document_id']}",flush=True)
        meta=doc['metadata'];kind=meta.get('family') or {'assessment-report':'assessment_report','product-information':'product_information','rmp':'risk_management'}.get(meta['document_type'],meta['document_type'])
        agency='HC' if meta['source']=='Health Canada' else meta['source']
        path=doc.get('rendered_path') or doc['raw_path']
        try:
            sel=Selection(index+1,doc['version_id'],path,agency,kind,None,'process')
            if doc['kind']=='html':
                from pipelines.pipeline_h.source_router import result as html_result
                result=html_result(doc,run)
            else:result=process_document(sel,adapter.extractor)
            summary={'document_id':doc['document_id'],'version_id':doc['version_id'],'seconds':time.perf_counter()-started,
                     'pages':len(result['routes']),'blocks':len(result['blocks']),'qa':result['qa']}
            write(target,{'status':'complete','document':doc,'summary':summary,'result':result,
                          'visual_audit':{'content_source':'original HTML DOM','before_blocks':[]} if doc['kind']=='html' else getattr(adapter,'visual_audit',{})})
            reports.append(summary)
        except Exception as exc:
            error={'document_id':doc['document_id'],'error':f'{type(exc).__name__}: {exc}','traceback':traceback.format_exc()}
            write(target,{'status':'failed','document':doc,'summary':error})
            reports.append(error);print('FAILED',error['error'],flush=True)
        write(run/'docling_progress.json',{'completed':sum('pages' in r for r in reports),'failed':sum('error' in r for r in reports),
                                         'total':len(intake['documents']),'documents':reports})
    print('DOCLING DONE',len(reports),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--config',required=True);a=p.parse_args();main(a.run,a.config)
