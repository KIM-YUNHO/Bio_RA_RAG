import contextlib, io, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import fitz
from preprocess.ingest import from_inputs, sha256
from preprocess.source.html_render import prepare
from preprocess.extraction.hybrid import run_hybrid, resume_enrichment
from preprocess.files import read_jsonl
from preprocess.extraction.hybrid_validate import validate_extraction
from preprocess.hybrid_config import HybridConfig
from preprocess.source.native_page import parse
from tests.preprocessing.test_preprocessing_hybrid import Parser, Qwen


class CorpusUpdates(unittest.TestCase):
    def test_html_original_and_render_have_independent_checked_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'saved.html'
            source.write_text('<html><body><nav>exclude menu</nav><main><h1>Decision</h1><p>Trial A has 67 subjects.</p><img src="https://example.org/never.png"><script>alert(1)</script></main></body></html>',encoding='utf-8')
            doc=from_inputs([source],root/'run')[0];original=sha256(doc.raw_path)
            prepare([doc],root/'run')
            self.assertEqual(sha256(doc.raw_path),original)
            self.assertNotEqual(doc.version_id,doc.rendered_sha256)
            with fitz.open(doc.pdf_path) as pdf:
                text=''.join(p.get_text() for p in pdf)
            self.assertIn('67 subjects',text);self.assertNotIn('alert',text);self.assertNotIn('exclude menu',text)
            with contextlib.redirect_stdout(io.StringIO()):run_hybrid([doc],root/'extraction',HybridConfig(),paddle=Parser(),use_qwen=False)
            validate_extraction(root/'extraction')
            a=read_jsonl(root/'extraction/extracted_assets.jsonl')[0]
            self.assertEqual(a['source_refs'][0]['rendered_sha256'],doc.rendered_sha256)
            Path(doc.rendered_path).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'rendering checksum'):validate_extraction(root/'extraction')

    def test_native_prose_fastpath_rejects_drawings_and_tables(self):
        with fitz.open() as pdf:
            page=pdf.new_page();page.insert_text((50,60),'This is an ordinary paragraph of regulatory text.')
            self.assertIsNotNone(parse(page,(595,842)))
            page.draw_line((50,80),(300,80))
            self.assertIsNone(parse(page,(595,842)))

    def test_wrapped_source_heading_and_primary_analysis_are_boundaries(self):
        from rag.index.document_structure import Tracker
        from tests.index.test_document_structure import refs
        texts=['Clinical Phase I study\n(SB17-1001)','Primary analysis','The actual result.','Post-hoc analyses','Adjusted result.']
        _,_,_,states=Tracker('doc','version','SB17').scan(texts,refs(texts),1)
        self.assertEqual(states[2]['heading_studies'],['ST_SB17_1001'])
        self.assertNotEqual(states[2]['section_id'],states[4]['section_id'])
