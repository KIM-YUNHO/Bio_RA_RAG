import contextlib,io,json,tempfile,unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import fitz
from PIL import Image,ImageDraw
from preprocess import source_grid as sg
from preprocess.complex_geometry import raster_grid,table_gate
from preprocess.ingest import sha256


class SourceGridTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.pdf=fitz.open();self.page=self.pdf.new_page(width=200,height=200)
        for x in (20,100,180):self.page.draw_line((x,20),(x,180))
        for y in (20,100,180):self.page.draw_line((20,y),(180,y))
        for x,y,text in ((30,55,'Endpoint'),(110,55,'N'),(30,135,'AUC'),(110,135,'12.5')):self.page.insert_text((x,y),text)
        self.crop=self.root/'crop.png';self.page.get_pixmap(clip=fitz.Rect(20,20,180,180)).save(self.crop)
        self.asset={'asset_id':'table','asset_type':'table','bbox_pdf':[20,20,180,180],
            'bbox_input_px':[20,20,180,180],'input_image_size':[200,200], 'source_refs':[{'pdf_page':1}],
            'base':{'content':'unreliable old candidate'},'selected':{'origin':'paddle','content':'old'},'enrichment':None,
            'light_region_ocr':{'metadata':{'image_size':[160,160],'crop_path':str(self.crop),'crop_sha256':sha256(self.crop)},
                'raw_sha256':'cached-ocr','provenance':{'ocr':{'tokens':[]}}}}
        self.geometry={'method':'test_source_borders','cells':[{'row':r,'column':c,'row_span':1,'col_span':1,
            'bbox_pdf':[20+c*80,20+r*80,100+c*80,100+r*80]} for r in range(2) for c in range(2)]}

    def tearDown(self):self.pdf.close();self.tmp.cleanup()

    def test_native_literals_keep_coordinates_and_numbers_without_models(self):
        p=sg.prepare(self.asset,self.page,self.geometry)
        self.assertEqual(p['issues'],[]);self.assertIn('12.5',p['table_html'])
        self.assertEqual(p['cell_origins'],{'verified_native_cell':4})
        self.assertTrue(all(c['native_char_indices'] for c in p['cells']));self.assertFalse(p['numeric_evidence_eligible'])

    def test_merged_source_span_is_immutable_and_complete(self):
        g=deepcopy(self.geometry);g['cells']=g['cells'][2:]+[{'row':0,'column':0,'row_span':1,'col_span':2,'bbox_pdf':[20,20,180,100]}]
        p=sg.prepare(self.asset,self.page,g);self.assertIn('colspan="2"',p['table_html'])
        self.assertEqual(p['issues'],[]);self.assertEqual(len(p['cells']),3)

    def test_missing_side_column_cannot_become_context_or_disappear(self):
        g=deepcopy(self.geometry);g['cells']=[{**c,'bbox_pdf':[20+c['column']*40,20+c['row']*80,60+c['column']*40,100+c['row']*80]} for c in g['cells']]
        p=sg.prepare(self.asset,self.page,g)
        self.assertIn('native_glyphs_unassigned_to_physical_cells',p['issues'])
        self.assertEqual(p['preserved_context'],[])

    def test_source_title_above_grid_is_preserved_verbatim(self):
        self.page.insert_text((25,15),'INDICATION: trial A',fontsize=7)
        self.asset['bbox_pdf']=[20,0,180,180];self.asset['bbox_input_px']=[20,0,180,180]
        self.page.get_pixmap(clip=fitz.Rect(self.asset['bbox_pdf'])).save(self.crop)
        self.asset['light_region_ocr']['metadata'].update(image_size=[160,180],crop_sha256=sha256(self.crop))
        p=sg.prepare(self.asset,self.page,self.geometry)
        self.assertEqual(p['issues'],[]);self.assertTrue(p['representation'].startswith('<p>INDICATION: trial A</p>'))

    def test_crop_tamper_and_overlapping_cells_are_rejected(self):
        self.crop.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'crop changed'):sg.prepare(self.asset,self.page,self.geometry)
        g=deepcopy(self.geometry);g['cells'].append(g['cells'][0])
        with self.assertRaisesRegex(ValueError,'overlap'):sg.validate_geometry(g)
        g['cells']=g['cells'][:-2]
        with self.assertRaisesRegex(ValueError,'missing'):sg.validate_geometry(g)

    def test_blank_cell_requires_no_text_and_no_interior_ink(self):
        im=Image.new('RGB',(80,80),'white');draw=ImageDraw.Draw(im)
        draw.rectangle((0,0,79,79),outline='black',width=4)
        self.assertEqual(sg.ink_fraction(im,[0,0,80,80]),0)
        draw.text((30,30),'7',fill='black')
        self.assertGreater(sg.ink_fraction(im,[0,0,80,80]),.002)
        self.assertEqual(sg.ink_fraction(im,[0,0,5,5]),1)

    def test_native_boundary_crossing_is_flagged(self):
        data={'image_size':[200,200],'lines':[[{'text':'7','box':[95,40,108,50]}]]}
        cells=sg.physical_cells(self.asset,self.page,self.geometry)
        mapped,_=sg.native_cells(data,cells)
        self.assertIn('native_glyph_crosses_cell_boundary',mapped['R0C1']['issues'])

    def test_cell_schema_and_transcription_cannot_change_geometry_or_known_text(self):
        p=sg.prepare(self.asset,self.page,self.geometry);before=deepcopy(p)
        with self.assertRaisesRegex(ValueError,'overwrite'):sg.resolve_transcription(p,['R1C1'],{'R1C1':{'text':'999','state':'visible'}})
        self.assertEqual(p,before);schema=sg.response_schema(['R1C1'])
        self.assertEqual(schema['required'],['R1C1']);self.assertFalse(schema['additionalProperties'])
        with self.assertRaisesRegex(ValueError,'immutable'):sg.resolve_transcription(p,['R1C1'],{'R9C9':{}})

    def test_unsupported_model_number_stays_unresolved(self):
        p=sg.prepare(self.asset,self.page,self.geometry);cell=p['cells'][-1]
        cell.update(origin='unresolved_cell',text='',state='unreadable');p['unresolved_cells']=['R1C1']
        out=sg.resolve_transcription(p,['R1C1'],{'R1C1':{'text':'999','state':'visible'}})
        self.assertEqual(out['unresolved_cells'],['R1C1']);self.assertNotIn('999',out['table_html'])
        out=sg.resolve_transcription(p,['R1C1'],{'R1C1':{'text':'12.5','state':'visible'}})
        self.assertEqual(out['unresolved_cells'],[]);self.assertEqual(out['geometry'],p['geometry'])

if __name__=='__main__':unittest.main()
