import contextlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import fitz
from preprocess.hybrid import run_hybrid,read_jsonl
from preprocess.hybrid_config import HybridConfig
from preprocess.hybrid_validate import validate_extraction
from preprocess.ingest import from_inputs,sha256
from preprocess.light_refinement import refine
from test_preprocessing_hybrid import Parser,BAD,GOOD


class OCR:
    def __init__(self):self.calls=0
    def fingerprint(self):return {'name':'test-ordinary-ocr'}
    def ocr(self,image):
        self.calls+=1
        return {'ra_light_ocr':{'0':{'ocr':{'tokens':[{'text':'Group','confidence':.99,'bbox':[1,1,10,10]}]}}}}


class LightRefinementTests(unittest.TestCase):
    def test_source_is_preserved_cache_is_reused_and_tampering_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pdf_path=root/'input.pdf';source=root/'old';out=root/'new'
            with fitz.open() as pdf:
                page=pdf.new_page(width=400,height=400);page.insert_text((40,70),'Group')
                pdf.save(pdf_path)
            docs=from_inputs([pdf_path],source);config=HybridConfig()
            with contextlib.redirect_stdout(io.StringIO()):run_hybrid(docs,source,config,paddle=Parser(table=BAD),use_qwen=False)
            a=read_jsonl(source/'extracted_assets.jsonl')[0];before=sha256(source/'extraction_integrity.json');backend=OCR()
            grid={'html':GOOD,'cells':[],'complexity':{'simple_grid_complete':True}}
            with patch('preprocess.simple_grid.reconstruct',return_value=grid):
                result=refine(source,out,[a['asset_id']],config,backend)
                second=refine(source,out,[a['asset_id']],config,backend)
            self.assertEqual(backend.calls,1);self.assertEqual(result['ocr_calls'],1);self.assertEqual(second['ocr_calls'],0)
            self.assertEqual(sha256(source/'extraction_integrity.json'),before)
            updated=read_jsonl(out/'extracted_assets.jsonl')[0]
            self.assertEqual(updated['base'],a['base']);self.assertFalse(updated['numeric_evidence_eligible'])
            Path(updated['light_region_ocr']['raw_path']).write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'binding changed'):validate_extraction(out)

    def test_protected_policy_stops_before_any_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();backend=OCR()
            (source/'extraction_manifest.json').write_text(json.dumps({'documents':[{
                'document_id':'protected','version_id':'v','kind':'pdf','raw_path':'missing','text_path':'missing',
                'source_paths':[],'metadata':{'model_execution_allowed':False}}]}),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Model execution denied'):refine(source,root/'out',[],HybridConfig(),backend)
            self.assertEqual(backend.calls,0);self.assertFalse((root/'out').exists())

    def test_verified_native_grid_skips_region_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pdf_path=root/'input.pdf';source=root/'old';out=root/'new'
            with fitz.open() as pdf:
                page=pdf.new_page(width=400,height=400)
                for x in (30,140,250):page.draw_line((x,50),(x,140))
                for y in (50,80,110,140):page.draw_line((30,y),(250,y))
                for x,y,text in ((40,70,'Group'),(150,70,'N'),(40,100,'A'),(150,100,'10'),(40,130,'B'),(150,130,'20')):
                    page.insert_text((x,y),text)
                pdf.save(pdf_path)
            class NativeParser(Parser):
                def parse(self,image):
                    from preprocess.native_text import packet,project
                    raw=super().parse(image)
                    with fitz.open(pdf_path) as pdf:
                        return project(raw,packet(pdf[0],(raw['res']['width'],raw['res']['height'])))
            docs=from_inputs([pdf_path],source);config=HybridConfig()
            with contextlib.redirect_stdout(io.StringIO()):run_hybrid(docs,source,config,paddle=NativeParser(),use_qwen=False)
            asset=read_jsonl(source/'extracted_assets.jsonl')[0]
            self.assertEqual(asset['selected']['origin'],'native_table_grid')
            backend=OCR();result=refine(source,out,[asset['asset_id']],config,backend)
            self.assertEqual(result['ocr_calls'],0);self.assertEqual(result['native_reused'],1)
            self.assertEqual(backend.calls,0)
            updated=read_jsonl(out/'extracted_assets.jsonl')[0]
            self.assertEqual(updated['selected'],asset['selected']);self.assertEqual(updated['route']['next_stage'],'native')


if __name__=='__main__':unittest.main()
