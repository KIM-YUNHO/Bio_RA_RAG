import unittest
import fitz
from PIL import Image,ImageDraw
from preprocess.tables.complex_geometry import raster_grid,vector_grid


class ComplexGeometry(unittest.TestCase):
    def image(self,merged=False,broken=False):
        im=Image.new('RGB',(330,330),'white');d=ImageDraw.Draw(im)
        for y in (20,110,200,290):d.line((20,y,290,y),fill='black',width=2)
        for x in (20,110,200,290):
            top=110 if merged and x==110 else 20
            d.line((x,top,x,290),fill='black',width=2)
        if broken:d.rectangle((108,150,112,165),fill='white')
        return im

    def test_flat_and_actual_merged_rules_are_distinguished_without_ocr_confidence(self):
        self.assertFalse(raster_grid(self.image())['merged_cells'])
        result=raster_grid(self.image(merged=True))
        self.assertTrue(result['merged_cells'])
        self.assertEqual(sum(c['row_span']*c['col_span'] for c in result['cells']),9)

    def test_broken_rule_is_not_a_positive_merged_cell(self):
        self.assertIsNone(raster_grid(self.image(broken=True)))

    def test_double_rule_band_does_not_create_an_empty_row(self):
        image=self.image();draw=ImageDraw.Draw(image)
        draw.line((20,23,290,23),fill='black',width=1)
        result=raster_grid(image)
        self.assertEqual(max(c['row']+c['row_span'] for c in result['cells']),3)
        self.assertTrue(any(len(b)>1 for b in result['rule_bands']['y']))

    def test_single_column_rule_preserves_real_row_spans(self):
        image=Image.new('RGB',(550,300),'white');draw=ImageDraw.Draw(image)
        for x in (20,180,340,500):draw.line((x,20,x,280),fill='black',width=2)
        for y in (20,100,280):draw.line((20,y,500,y),fill='black',width=2)
        draw.line((340,190,500,190),fill='black',width=2)
        result=raster_grid(image)
        self.assertEqual(max(c['row']+c['row_span'] for c in result['cells']),3)
        self.assertEqual(sum(c['row_span']==2 for c in result['cells']),2)

    def test_prose_underline_is_not_a_table_row(self):
        image=self.image();draw=ImageDraw.Draw(image)
        draw.line((35,160,90,160),fill='black',width=2)
        result=raster_grid(image)
        self.assertEqual(len(result['cells']),9)

    def test_vector_cell_spans_use_source_borders(self):
        with fitz.open() as doc:
            page=doc.new_page(width=330,height=330)
            for y in (20,110,200,290):page.draw_line((20,y),(290,y))
            for x in (20,110,200,290):page.draw_line((x,110 if x==110 else 20),(x,290))
            for x,y,text in ((30,50,'Header'),(210,50,'N'),(30,140,'A'),(120,140,'B'),(210,140,'C'),
                (30,230,'D'),(120,230,'E'),(210,230,'F')):page.insert_text((x,y),text)
            result=vector_grid(page,[20,20,290,290])
            self.assertTrue(result['merged_cells'])


if __name__=='__main__':unittest.main()
