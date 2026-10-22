"""Quality routing, literal preservation and SDK recognition bypass without a GPU."""
from copy import deepcopy
import types
import tempfile
from pathlib import Path
import json
import unittest
from unittest.mock import patch

import fitz
from preprocess.native_text import packet, extract, project, NativeRecognizer
from preprocess.hybrid import normalize_page, run_hybrid
from preprocess.files import read_jsonl
from preprocess.outputs import save_outputs
from preprocess.ingest import Document,from_inputs


class NativeTextTests(unittest.TestCase):
    def setUp(self):
        self.pdf=fitz.open();self.page=self.pdf.new_page(width=400,height=400)
        self.page.insert_text((40,70),'Dose 10 mg / Week 12')
        self.data=packet(self.page,(800,800));self.box=[60,100,600,160]
    def tearDown(self):self.pdf.close()

    def test_literal_text_is_taken_only_from_the_region(self):
        self.assertEqual(extract(self.data,'text',self.box),'Dose 10 mg / Week 12')
        self.assertIsNone(extract(self.data,'text',[0,200,800,300]))

    def test_visual_and_formula_blocks_are_never_replaced(self):
        for label in ('table','image','chart','inline_formula','display_formula','seal'):
            self.assertIsNone(extract(self.data,label,self.box))

    def test_inventory_quality_gate_and_empty_layer_trigger_visual_fallback(self):
        with patch('collection.text_layer.inspect',return_value=({'needs_ocr':True,'reasons':['unmapped_glyphs']},{})):
            self.assertIsNone(extract(packet(self.page,(800,800)),'text',self.box))
        page=self.pdf.new_page(width=400,height=400)
        self.assertFalse(packet(page,(800,800))['eligible'])

    def test_mixed_image_overlap_does_not_use_a_partial_native_layer(self):
        self.data['images']=[self.box]
        self.assertIsNone(extract(self.data,'text',self.box))

    def test_rotated_page_uses_rendered_image_coordinates(self):
        self.page.set_rotation(90)
        data=packet(self.page,(800,800))
        rect=fitz.Rect(30,50,300,80)*self.page.rotation_matrix
        box=[v*2 for v in rect]
        self.assertEqual(extract(data,'text',box),'Dose 10 mg / Week 12')

    def test_cached_projection_preserves_visual_output_and_original_cache(self):
        raw={'res':{'parsing_res_list':[
            {'block_label':'text','block_bbox':self.box,'block_content':'WRONG'},
            {'block_label':'table','block_bbox':self.box,'block_content':'<table>10</table>'}]}}
        before=deepcopy(raw);result=project(raw,self.data,cached=True)
        self.assertEqual(raw,before)
        self.assertEqual(result['res']['parsing_res_list'][0]['block_content'],'Dose 10 mg / Week 12')
        self.assertEqual(result['res']['parsing_res_list'][1],raw['res']['parsing_res_list'][1])
        self.assertTrue(result['ra_runtime']['legacy_full_cache_reused'])

    def test_sdk_gets_only_the_remaining_visual_entries(self):
        calls=[]
        def collect(i,blocks,images,config):
            return [(i,j,'image','OCR:',(1,2),{}) for j in range(len(blocks))],False,set()
        def recognize(batches,*args):
            for info in batches.values():
                calls.extend(info['vlm_block_ids'])
                info['vlm_results']=[{'result':'model table'} for _ in info['vlm_block_ids']]
        core=types.SimpleNamespace(_paddleocr_vl_collect_page_vlm_entries_core=collect,
            _paddleocr_vl_run_vl_recognition_batches=recognize)
        hook=NativeRecognizer(types.SimpleNamespace(paddlex_pipeline=core));hook.begin(self.data)
        blocks=[{'label':'text','box':self.box},{'label':'table','box':self.box}]
        core._paddleocr_vl_collect_page_vlm_entries_core(0,blocks,[],{})
        info={'images':[1,2],'queries':['OCR:','Table Recognition:'],'figure_token_maps':[{},{}],
            'vlm_block_ids':[(0,0),(0,1)],'curr_vlm_block_idx':0}
        core._paddleocr_vl_run_vl_recognition_batches({(1,2):info},False,{})
        self.assertEqual(calls,[(0,1)])
        self.assertEqual([x['result'] for x in info['vlm_results']],['Dose 10 mg / Week 12','model table'])
        self.assertEqual((hook.native_count,hook.vlm_count),(1,1))
        hook.begin(None)
        core._paddleocr_vl_run_vl_recognition_batches({(1,2):info},False,{})
        self.assertEqual(calls[-2:],[(0,0),(0,1)])

    def test_asset_provenance_reports_native_instead_of_model_transcription(self):
        raw={'res':{'width':800,'height':800,'model_settings':{'use_layout_detection':True},
            'parsing_res_list':[{'block_label':'text','block_bbox':self.box,'block_content':'WRONG'}]}}
        raw=project(raw,self.data)
        doc=Document('fixture','sha','pdf','source.pdf','source.pdf',[],{})
        assets,invalid=normalize_page(doc,self.page,'image.png',[800,800],raw,'parser')
        self.assertFalse(invalid)
        self.assertEqual(assets[0]['base']['origin'],'native_text_layer')
        self.assertEqual(assets[0]['selected']['origin'],'native_text_layer')
        self.assertFalse(assets[0]['numeric_evidence_eligible'])

    def test_saved_native_text_is_rechecked_even_with_valid_artifact_checksums(self):
        from preprocess.hybrid_validate import validate_extraction
        from preprocess.hybrid_config import HybridConfig
        from PIL import Image
        fixture=self
        class Backend:
            def fingerprint(self):return {'name':'native fixture'}
            def close(self):pass
            def parse(self,image):
                with Image.open(image) as raster:w,h=raster.size
                box=[v*w/800 for v in fixture.box]
                raw={'res':{'width':w,'height':h,'model_settings':{'use_layout_detection':True},
                    'parsing_res_list':[{'block_label':'text','block_bbox':box,'block_content':'unused'}]}}
                return project(raw,packet(fixture.page,(w,h)))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source.pdf';out=root/'extraction'
            self.pdf.save(source);documents=from_inputs([source],out)
            run_hybrid(documents,out,HybridConfig(),use_qwen=False,paddle=Backend())
            validate_extraction(out)
            assets=read_jsonl(out/'extracted_assets.jsonl')
            assets[0]['base']['content']='Dose 999 mg'
            assets[0]['selected']['content']='Dose 999 mg'
            summary=json.loads((out/'extraction_summary.json').read_text())
            save_outputs(out,assets,read_jsonl(out/'extraction_pages.jsonl'),documents,summary,
                         read_jsonl(out/'visual_groups.jsonl'))
            with self.assertRaisesRegex(ValueError,'Native block text differs'):
                validate_extraction(out)


if __name__=='__main__':unittest.main()
