import tempfile,unittest
from pathlib import Path
import fitz
from PIL import Image,ImageDraw
from preprocess.tables.region_structure import native_grid,partition_ocr,chart_geometry,borders_supported


class RegionStructureTests(unittest.TestCase):
    def test_all_borders_are_required_and_gaps_are_not_filled(self):
        rect=fitz.Rect(0,0,100,50)
        lines=[(0,0,100,0),(0,50,100,50),(0,0,0,50),(100,0,100,50)]
        self.assertTrue(borders_supported(rect,lines))
        self.assertFalse(borders_supported(rect,lines[:-1]))

    def test_native_grid_preserves_multiline_literal_cells_without_ocr(self):
        with fitz.open() as pdf:
            page=pdf.new_page(width=300,height=220)
            for x in (20,140,280):page.draw_line((x,20),(x,180))
            for y in (20,60,120,180):page.draw_line((20,y),(280,y))
            for x,y,text in ((30,45,'Treatment'),(150,45,'N'),(30,80,'Group A'),(30,100,'Second line'),
                (150,90,'67'),(30,150,'Group B'),(150,150,'68')):page.insert_text((x,y),text)
            a={'bbox_pdf':[20,20,280,180],'input_image_size':[300,220]}
            grid=native_grid(page,a)
            self.assertIsNotNone(grid);self.assertIn('Second line',grid['html']);self.assertIn('67',grid['html'])
            self.assertEqual(grid['quality']['issues'],[])

    def test_native_grid_can_preserve_a_physically_merged_header(self):
        with fitz.open() as pdf:
            page=pdf.new_page(width=300,height=220)
            for x in (20,280):page.draw_line((x,20),(x,180))
            page.draw_line((140,60),(140,180))
            for y in (20,60,120,180):page.draw_line((20,y),(280,y))
            for x,y,text in ((80,45,'Treatment'),(30,90,'A'),(150,90,'67'),(30,150,'B'),(150,150,'68')):
                page.insert_text((x,y),text)
            grid=native_grid(page,{'bbox_pdf':[20,20,280,180],'input_image_size':[300,220]})
            self.assertIsNotNone(grid);self.assertIn('colspan="2"',grid['html'])

    def test_caption_mask_preserves_body_and_uncertain_boundary_tokens(self):
        asset={'bbox_pdf':[0,0,100,100]}
        proof={'metadata':{'image_size':[100,100]},'provenance':{'ocr':{'tokens':[
            {'text':'Table 1','bbox':[1,1,40,9],'confidence':.99},
            {'text':'45','bbox':[5,40,20,50],'confidence':.99},
            {'text':'boundary','bbox':[5,8,40,16],'confidence':.99}]}}}
        body,context,ambiguous=partition_ocr(asset,proof,[{'asset_id':'caption','bbox_pdf':[0,0,100,10]}])
        self.assertEqual([t['text'] for t in body['tokens']],['45','boundary'])
        self.assertEqual(context,[{'token_index':0,'context_asset_id':'caption'}]);self.assertEqual(ambiguous,[2])

    def test_physical_axes_with_numeric_ticks_are_complex_but_step_numbers_are_not(self):
        image=Image.new('RGB',(500,400),'white');draw=ImageDraw.Draw(image)
        draw.line((80,50,80,300),fill='black',width=3);draw.line((80,300,450,300),fill='black',width=3)
        tokens=[{'text':str(i),'confidence':.99,'bbox':[x,310,x+15,325]} for i,x in enumerate((100,200,300,400))]
        tokens += [{'text':str(i),'confidence':.99,'bbox':[45,y,65,y+15]} for i,y in enumerate((60,130,210,280))]
        self.assertTrue(chart_geometry(image,{'tokens':tokens})['chart_axes'])
        self.assertFalse(chart_geometry(image,{'tokens':tokens[:3]})['chart_axes'])


if __name__=='__main__':unittest.main()
