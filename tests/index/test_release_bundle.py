"""A finished extraction checkpoint opens as a read-only bundle; no OCR/VLM or external services."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import fitz
from PIL import Image
from tests.preprocessing.test_preprocessing_hybrid import Parser
from tests.index.test_chunking import CharacterCounter
from preprocess.extraction.hybrid import run_hybrid
from preprocess.hybrid_config import HybridConfig
from preprocess.ingest import from_inputs
from rag.index.preprocessed_chunks import write
from rag.index.bundle import Bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/'source.pdf';self.run=self.root/'extraction';self.candidate=self.root/'candidate'
        with fitz.open() as pdf:
            for rotation in (0,90):
                page=pdf.new_page(width=400,height=400)
                for x,y,text in ((40,70,'Group'),(150,70,'N'),(40,100,'A'),(150,100,'10'),(40,130,'B'),(150,130,'20'),(40,190,'Time Value A B Week 12')):page.insert_text((x,y),text)
                page.set_rotation(rotation)
            pdf.save(self.source)
        docs=from_inputs([self.source],self.run)
        with contextlib.redirect_stdout(io.StringIO()):
            run_hybrid(docs,self.run,HybridConfig(),paddle=Parser(figure=True),use_qwen=False)
            write(self.run,self.candidate,counter=CharacterCounter())
        self.bundle=Bundle(self.run,self.candidate)

    def tearDown(self):
        self.temp.cleanup()

    def test_pages_assets_and_chunks_preserve_same_source_and_review_state(self):
        summary=self.bundle.summary()
        self.assertEqual((summary['pages'],summary['assets']),(2,4))
        self.assertTrue(summary['read_only'])
        page=self.bundle.page_detail(self.bundle.page_list('d1')[0]['page_id'])
        table=next(a for a in page['assets'] if a['asset_type']=='table')
        figure=next(a for a in page['assets'] if a['asset_type']=='figure')
        self.assertTrue(table['retrieval_eligible']);self.assertFalse(figure['retrieval_eligible'])
        self.assertTrue(figure['review_required'])
        detail=self.bundle.asset_detail(table['asset_id'])
        self.assertIn('<table>',detail['asset']['selected']['content'])
        self.assertTrue(detail['chunks'])
        self.assertEqual(detail['asset']['source_refs'][0]['pdf_page'],1)
        self.assertEqual(len(self.bundle.page_list('d1',query='group',kind='visual')),2)
        self.assertEqual(self.bundle.page_list('d1',query='not in the document'),[])

    def test_render_rotated_source_at_registered_overlay_dimensions(self):
        for page in self.bundle.pages.values():
            with Image.open(io.BytesIO(self.bundle.render(page['page_id']))) as image:
                self.assertEqual(image.size,tuple(page['image_size']))
        page=list(self.bundle.pages.values())[1]
        self.assertEqual(page['original_rotation'],90)
        self.assertTrue(self.bundle.render(page['page_id']).startswith(b'\x89PNG'))

    def test_modified_source_or_artifact_cannot_be_opened(self):
        path=self.run/'extracted_assets.jsonl';original=path.read_bytes();path.write_bytes(original+b'\n')
        with self.assertRaisesRegex(ValueError,'추출 파일 변경'):Bundle(self.run,self.candidate)
        path.write_bytes(original)
        raw=Path(self.bundle.docs['d1']['raw_path']);raw.write_bytes(raw.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'원본 PDF 변경'):Bundle(self.run,self.candidate)

    def test_running_checkpoints_and_mismatched_chunk_sources_are_rejected(self):
        status=self.run/'processing_status.json';data=json.loads(status.read_text(encoding='utf-8'))
        data['lifecycle']='running';status.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'처리 중'):Bundle(self.run,self.candidate)
        data['lifecycle']='finished';status.write_text(json.dumps(data),encoding='utf-8')
        summary_path=self.candidate/'chunks_summary.json';summary=json.loads(summary_path.read_text(encoding='utf-8'))
        summary['input_run']=str(self.root/'different');summary_path.write_text(json.dumps(summary),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'버전이 다릅니다'):Bundle(self.run,self.candidate)


if __name__=='__main__':unittest.main()
