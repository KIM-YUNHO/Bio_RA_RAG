"""Literal, cell geometry, fallback and tampering checks for selective extraction."""
import contextlib,io,json,tempfile,unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import fitz
from preprocess.native_text import packet,extract,project
from preprocess.native_tables import grids,select
from preprocess.hybrid import normalize_page,run_hybrid,read_jsonl,save_outputs
from preprocess.hybrid_validate import validate_extraction
from preprocess.ingest import Document,from_inputs
from preprocess.hybrid_config import HybridConfig


def fixture(redacted=False):
    pdf=fitz.open();page=pdf.new_page(width=400,height=400)
    page.insert_text((40,40),'Table 1. Primary analysis')
    for x in (40,150,260):page.draw_line((x,80),(x,200))
    for y in (80,120,160,200):page.draw_line((40,y),(260,y))
    for r,row in enumerate((('Group','N'),('Drug','10'),('Reference','(b)(4)' if redacted else '20'))):
        for c,text in enumerate(row):page.insert_text((48+c*110,105+r*40),text)
    return pdf,page


class SelectiveNativeTests(unittest.TestCase):
    def test_outer_grid_with_unruled_numeric_rows_falls_back(self):
        with fitz.open() as pdf:
            page=pdf.new_page(width=400,height=400)
            for x in (40,150,260):page.draw_line((x,80),(x,240))
            for y in (80,120,240):page.draw_line((40,y),(260,y))
            for x,text in ((48,'Group'),(158,'N')):page.insert_text((x,105),text)
            for y,a,b in ((145,'Drug A','10'),(180,'Drug B','20'),(215,'Drug C','30')):
                page.insert_text((48,y),a);page.insert_text((158,y),b)
            self.assertFalse(grids(page,(800,800)))

    def test_redacted_table_stays_on_visual_review_path(self):
        pdf,page=fixture(redacted=True)
        try:self.assertFalse(grids(page,(800,800)))
        finally:pdf.close()

    def test_readable_region_survives_damaged_glyph_elsewhere(self):
        pdf,page=fixture()
        try:
            data=packet(page,(800,800));data.update(eligible=False,regional_eligible=True,
                unreliable_boxes=[[600,500,650,530]])
            self.assertEqual(extract(data,'text',[70,45,600,85]),'Table 1. Primary analysis')
            data['unreliable_boxes']=[[70,45,120,85]]
            self.assertIsNone(extract(data,'text',[70,45,600,85]))
        finally:pdf.close()

    def test_hidden_ocr_layer_cannot_become_native_by_region(self):
        pdf,page=fixture()
        try:
            with patch('text_layer.inspect',return_value=({'needs_ocr':True,'reasons':['publisher_ocr_layer']},{})):
                self.assertIsNone(extract(packet(page,(800,800)),'text',[70,45,600,85]))
        finally:pdf.close()

    def test_grid_preserves_cells_and_caption_separately(self):
        pdf,page=fixture()
        try:
            data=packet(page,(800,800));self.assertEqual(len(data['tables']),1)
            grid=data['tables'][0]
            self.assertEqual([c['text'] for c in grid['cells']],['Group','N','Drug','10','Reference','20'])
            self.assertEqual((grid['row_count'],grid['col_count']),(3,2))
            self.assertEqual(select(data,'table',grid['bbox_input_px']),grid)
            self.assertNotIn('Primary analysis',grid['content'])
            raw={'res':{'width':800,'height':800,'model_settings':{'use_layout_detection':True},
                'parsing_res_list':[{'block_label':'table','block_bbox':grid['bbox_input_px'],'block_content':'wrong'}]}}
            result=project(raw,data)
            doc=Document('fixture','sha','pdf','source.pdf','source.pdf',[],{})
            assets,invalid=normalize_page(doc,page,'image.png',[800,800],result,'parser')
            self.assertFalse(invalid);asset=assets[0]
            self.assertEqual(asset['base']['origin'],'native_table_grid')
            self.assertEqual(asset['selected']['origin'],'native_table_grid')
            self.assertFalse(asset['numeric_evidence_eligible'])
            self.assertTrue(all(c['bbox_pdf'] is None for c in asset['base']['quality']['cells']))
        finally:pdf.close()

    def test_merged_and_partially_clipped_tables_fall_back(self):
        pdf,page=fixture()
        try:
            data=packet(page,(800,800));box=data['tables'][0]['bbox_input_px']
            self.assertIsNone(select(data,'table',[box[0],box[1],box[2]-100,box[3]]))
            self.assertIsNone(select({**data,'tables':data['tables']*2},'table',box))
        finally:pdf.close()
        with fitz.open() as pdf:
            page=pdf.new_page(width=400,height=400)
            for x in (40,260):page.draw_line((x,80),(x,200))
            page.draw_line((150,120),(150,200))
            for y in (80,120,160,200):page.draw_line((40,y),(260,y))
            page.insert_text((48,105),'Merged header')
            page.insert_text((48,145),'A');page.insert_text((158,145),'10')
            page.insert_text((48,185),'B');page.insert_text((158,185),'20')
            self.assertFalse(grids(page,(800,800)))

    def test_rotated_table_keeps_original_pdf_cell_boxes(self):
        pdf,page=fixture()
        try:
            original=grids(page,(800,800))[0]
            page.set_rotation(90);rotated=grids(page,(800,800))[0]
            self.assertEqual(rotated['cells'],original['cells'])
            self.assertNotEqual(rotated['bbox_input_px'],original['bbox_input_px'])
        finally:pdf.close()

    def test_source_cell_tampering_rejected_even_after_rehashing(self):
        pdf,page=fixture()
        class Backend:
            def fingerprint(self):return {'name':'native-grid-fixture'}
            def close(self):pass
            def parse(self,image):
                from PIL import Image
                with Image.open(image) as raster:size=raster.size
                data=packet(page,size);grid=data['tables'][0]
                raw={'res':{'width':size[0],'height':size[1],'model_settings':{'use_layout_detection':True},
                    'parsing_res_list':[{'block_label':'table','block_bbox':grid['bbox_input_px'],'block_content':'unused'}]}}
                return project(raw,data)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);source=root/'source.pdf';pdf.save(source)
                docs=from_inputs([source],root/'input');out=root/'extraction'
                with contextlib.redirect_stdout(io.StringIO()):run_hybrid(docs,out,HybridConfig(),paddle=Backend(),use_qwen=False)
                validate_extraction(out)
                assets=read_jsonl(out/'extracted_assets.jsonl');assets[0]['base']['native_text_provenance']['cells'][2]['text']='999'
                summary=json.loads((out/'extraction_summary.json').read_text(encoding='utf-8'))
                save_outputs(out,assets,read_jsonl(out/'extraction_pages.jsonl'),docs,summary,read_jsonl(out/'visual_groups.jsonl'))
                with self.assertRaisesRegex(ValueError,'Native grid differs'):validate_extraction(out)
        finally:pdf.close()


if __name__=='__main__':unittest.main()
