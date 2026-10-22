"""Verified font derivatives preserve page identity and reject altered PDF content."""
import tempfile,unittest,re
from pathlib import Path
import fitz
from preprocess.native_fonts import prepare,validate,validate_delta
from preprocess.ingest import from_inputs


class NativeFontRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        font=Path('C:/Windows/Fonts/arial.ttf')
        if not font.is_file():self.skipTest('Arial reference font unavailable')
        self.source=self.root/'source.pdf'
        with fitz.open() as pdf:
            page=pdf.new_page(width=400,height=400)
            xref=page.insert_font(fontname='SourceArial',fontfile=str(font))
            page.insert_text((40,70),'Dose 10 mg / Week 12',fontname='SourceArial')
            pdf.update_object(xref,re.sub(r'\s*/ToUnicode[^\n]*','',pdf.xref_object(xref)))
            pdf.xref_set_key(xref,'BaseFont','/ArialMT')
            pdf.save(self.source)
        self.doc=from_inputs([self.source],self.root/'input')[0]
        prepare([self.doc],self.root/'prepared')
    def tearDown(self):self.tmp.cleanup()

    def test_verified_source_glyphs_preserve_pixels_and_original_reference(self):
        self.assertTrue(self.doc.native_font_repair)
        validate(self.doc)
        with fitz.open(self.doc.raw_path) as original,fitz.open(self.doc.pdf_path) as repaired:
            self.assertEqual(original[0].get_pixmap().samples,repaired[0].get_pixmap().samples)
            self.assertIn('Dose 10 mg',repaired[0].get_text())
        ref=self.doc.source_ref(1,[30,40,300,80])
        self.assertEqual(ref['sha256'],self.doc.version_id)
        self.assertEqual(ref['locator_type'],'verified_cmap_pdf_page')
        self.assertEqual(ref['rendered_sha256'],self.doc.rendered_sha256)

    def test_changed_derivative_hash_is_rejected(self):
        Path(self.doc.pdf_path).write_bytes(Path(self.doc.pdf_path).read_bytes()+b'changed')
        from preprocess.hybrid import assert_sources
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):assert_sources([self.doc])

    def test_graphics_change_cannot_be_hidden_by_new_checksum(self):
        import json
        proof=json.loads(Path(self.doc.native_font_repair['path']).read_text(encoding='utf-8'))
        altered=self.root/'altered.pdf'
        with fitz.open(self.doc.pdf_path) as pdf:
            pdf[0].draw_line((40,90),(250,90));pdf.save(altered)
        with self.assertRaisesRegex(ValueError,'Unexpected objects|original PDF'):
            validate_delta(self.doc.raw_path,altered,proof['fonts'])


if __name__=='__main__':unittest.main()
