import contextlib,io,json,tempfile,unittest
from pathlib import Path
import fitz
from preprocess.hybrid import run_hybrid,read_jsonl
from preprocess.hybrid_config import HybridConfig
from preprocess.hybrid_validate import validate_extraction
from preprocess.ingest import from_inputs,sha256
from preprocess.light_refinement import refine as light_refine
from preprocess.structural_refinement import refine
from test_preprocessing_hybrid import Parser,BAD
from test_light_refinement import OCR


class StructuralRefinementTests(unittest.TestCase):
    def test_cached_structure_recovers_native_cells_and_preserves_both_source_bundles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pdf_path=root/'input.pdf';source=root/'base';light=root/'light';out=root/'structure'
            with fitz.open() as pdf:
                page=pdf.new_page(width=400,height=400)
                for x in (30,140,250):page.draw_line((x,50),(x,140))
                for y in (50,80,110,140):page.draw_line((30,y),(250,y))
                for x,y,text in ((40,70,'Group'),(150,70,'N'),(40,100,'A'),(150,100,'10'),(40,130,'B'),(150,130,'20')):
                    page.insert_text((x,y),text)
                pdf.save(pdf_path)
            docs=from_inputs([pdf_path],source);config=HybridConfig()
            with contextlib.redirect_stdout(io.StringIO()):run_hybrid(docs,source,config,paddle=Parser(BAD),use_qwen=False)
            asset=read_jsonl(source/'extracted_assets.jsonl')[0];backend=OCR()
            light_refine(source,light,[asset['asset_id']],config,backend)
            before={p:sha256(p/'extraction_integrity.json') for p in (source,light)}
            review=root/'review.json';review.write_text(json.dumps({'decisions':[]}),encoding='utf-8')
            stats=refine(light,out,review)
            self.assertEqual(stats['new_ocr_calls'],0);self.assertEqual(backend.calls,1)
            updated=read_jsonl(out/'extracted_assets.jsonl')[0]
            self.assertEqual(updated['selected']['origin'],'source_geometry_table_candidate')
            self.assertIn('10',updated['selected']['content']);self.assertNotIn('<td>1</td>',updated['selected']['content'])
            self.assertEqual(updated['region_structure']['quality']['issues'],[])
            self.assertEqual(validate_extraction(out)['status'],'passed')
            for p,digest in before.items():self.assertEqual(sha256(p/'extraction_integrity.json'),digest)


if __name__=='__main__':unittest.main()
