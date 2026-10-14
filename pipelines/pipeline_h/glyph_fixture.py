"""Actual source OCR regression for generic suspicious cells; no expected-value injection."""
from pathlib import Path
import fitz
from pipelines.pipeline_h.prepare import ROOT,read,write
from pipelines.pipeline_h.structure import box_top_left
from pipelines.pipeline_h.selection import crop
from pipelines.pipeline_h.policy import SUSPECT_GLYPH,glyph_gate

def prepare():
    run=ROOT/'pipelines/pipeline_h/runs/glyph_source_regression';old=ROOT/'pipelines/pipeline_h/vendor/compare_study_standalone/output/abp654_design_20261014_v2'
    source=next(j for j in read(old/'inputs.json')['native'] if j['document']['key'].startswith('ABP654_FDA_ChemR_p012_013'))
    raw=read(old/'native/DOC-GPU/raw'/source['document']['key']/'vendor_document.json');jobs=[]
    with fitz.open(source['path']) as pdf:
        for block in raw['blocks']:
            if block['type']!='table':continue
            page=pdf[block['page']-1]
            for index,cell in enumerate(block['cells']):
                if cell.get('bbox') and SUSPECT_GLYPH.search(cell.get('text','')):
                    job={'id':f"glyph_{len(jobs):03d}",'task':'ocr','action':'glyph','source_pdf':source['path'],
                         'pdf_page':block['page'],'bbox':box_top_left({'bbox':cell['bbox']},page.rect.height,page.rect.width),
                         'original_text':cell['text'],'block_ref':block['ref'],'cell_index':index}
                    crop(run,job);jobs.append(job)
    write(run/'selection_manifest.json',{'jobs':jobs,'source_selection':'generic suspicious receptor glyph regex on saved Docling source cells','gold_consulted':False})
    config=read(ROOT/'pipelines/pipeline_h/config.local.json');config['paddle']['device']='cpu';write(run/'config.cpu.json',config)
    print('SOURCE OCR JOBS',len(jobs));return run

def evaluate(run):
    reports=[]
    for job in read(run/'selection_manifest.json')['jobs']:
        result=read(run/'vlm'/(job['id']+'.json'))
        reports.append({'source_text':job['original_text'],'source_crop':job['image'],'model_text':result.get('content'),
                        'gate':glyph_gate(job['original_text'],result['content']) if result['status']=='complete' else {'accepted':False,'error':result.get('error')}})
    write(run/'source_glyph_regression.json',{'task':'actual source crop OCR','gold_used_for_inference':False,
          'runtime':'CPU regression only; production worker uses GPU','results':reports})
    print(reports)

def prepare_table():
    run=ROOT/'pipelines/pipeline_h/runs/glyph_table_context_regression';old=ROOT/'pipelines/pipeline_h/vendor/compare_study_standalone/output/abp654_design_20261014_v2'
    source=next(j for j in read(old/'inputs.json')['native'] if j['document']['key'].startswith('ABP654_FDA_ChemR_p012_013'))
    raw=read(old/'native/DOC-GPU/raw'/source['document']['key']/'vendor_document.json')
    block=next(b for b in raw['blocks'] if b['type']=='table' and sum(bool(SUSPECT_GLYPH.search(c.get('text',''))) for c in b['cells'])>=2)
    from pipelines.pipeline_h.structure import table_rows
    with fitz.open(source['path']) as pdf:
        page=pdf[block['page']-1];job={'id':'glyph_table_context','task':'table','action':'replace','source_pdf':source['path'],
            'pdf_page':block['page'],'bbox':box_top_left(block,page.rect.height,page.rect.width),'original_table':table_rows(block),
            'source_selection':'multiple ambiguous scientific glyph labels; full table context'}
        crop(run,job)
    write(run/'selection_manifest.json',{'jobs':[job],'gold_consulted':False})
    config=read(ROOT/'pipelines/pipeline_h/config.local.json');config['paddle']['device']='cpu';write(run/'config.cpu.json',config)
    print(run)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--evaluate',action='store_true');p.add_argument('--table',action='store_true');a=p.parse_args()
    prepare_table() if a.table else evaluate(ROOT/'pipelines/pipeline_h/runs/glyph_source_regression') if a.evaluate else prepare()
