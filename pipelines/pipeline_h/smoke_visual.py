"""Structural adapter regression on existing source-bound outputs; no new model ranking."""
from pathlib import Path
import copy,shutil
import fitz
from pipelines.pipeline_h.prepare import ROOT,read,write
from pipelines.pipeline_h.metadata_tools import normalize_block_metadata
from preprocess.ingest import sha256,stable_hash
from pipelines.pipeline_h.export import export
from pipelines.pipeline_h.chunks import build

def main():
    src=ROOT/'pipelines/pipeline_h/runs/ustekinumab_20261014_v1'
    fingerprint=stable_hash({name:sha256(ROOT/'pipelines/pipeline_h'/name) for name in
        ('export.py','figure_literals.py','caption_recovery.py','structure.py','metadata_tools.py','chunks.py')})[:10]
    out=ROOT/'pipelines/pipeline_h/runs'/('smoke_visual_structure_'+fingerprint)
    intake=read(src/'intake_manifest.json');docs=[intake['documents'][i] for i in (0,3)];intake['documents']=docs
    write(out/'intake_manifest.json',intake)
    plans={docs[0]['document_id']:[37,44],docs[1]['document_id']:[27,35]};write(out/'validation_page_plan.json',plans)
    states=[]
    for new,(old,doc) in enumerate(zip((0,3),docs)):
        raw_path=src/'docling'/f"{old:03d}_{doc['version_id']}.json";raw=read(raw_path);blocks=copy.deepcopy(raw['result']['blocks'])
        normalization=normalize_block_metadata(blocks,doc['metadata'])
        states.append({'document':doc,'raw_path':str(raw_path),'blocks':blocks,'source_metadata_normalization':normalization,
                       'suppressed_table_candidates':[],'visual_context_regions':[]})
        with fitz.open(doc.get('rendered_path') or doc['raw_path']) as pdf:
            for number in plans[doc['document_id']]:
                page=pdf[number-1];image=out/'page_images'/f'{new:03d}'/f'{number:04d}.png';image.parent.mkdir(parents=True,exist_ok=True)
                pix=page.get_pixmap(dpi=150,alpha=False);pix.save(image)
                write(out/'layout'/f'{new:03d}'/f'{number:04d}.json',{'status':'complete','image':str(image),'pixels':[pix.width,pix.height],
                      'size':[page.rect.width,page.rect.height],'boxes':[],'fixture_only':True})
    write(out/'selected_documents.json',states)
    write(out/'selection_manifest.json',{'jobs':[],'selected_documents_sha256':sha256(out/'selected_documents.json'),
          'fixture_only':True,'note':'Source-bound adapter QA using existing Docling raw blocks; independent detector/VLM ranking is not evaluated'})
    shutil.copyfile(src/'docling_runtime.json',out/'docling_runtime.json')
    summary=export(out);chunks=build(out)
    from preprocess.hybrid import read_jsonl
    footer=next(a for a in read_jsonl(out/'extraction/extracted_assets.jsonl') if a['pdf_page']==27 and a['selected']['content']=='13')
    rotated=next(d for d in docs if d['document_id']==footer['document_id'])
    with fitz.open(rotated.get('rendered_path') or rotated['raw_path']) as pdf:
        if pdf[26].get_text('text',clip=fitz.Rect(footer['bbox_pdf'])).strip()!='13':
            raise ValueError('Rotated source bbox does not recover its actual printed footer')
    write(out/'smoke_report.json',{'status':'passed','extraction':summary,'chunks':chunks['chunks'],
          'coverage':['physical source identities','rotated page bbox','table cell spans','figure captions/grouping','metadata to existing chunker'],
          'full_corpus_inference':False})
    write(ROOT/'pipelines/pipeline_h/runs/latest_smoke_visual.json',{'run':str(out),'report':str(out/'smoke_report.json')})
    print('SMOKE',summary,'chunks',chunks['chunks'])

if __name__=='__main__':main()
