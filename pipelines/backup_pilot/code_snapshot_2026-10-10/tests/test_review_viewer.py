"""Actual checkpoint -> visual inspection APIs; no OCR/VLM or external services."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import fitz
from PIL import Image
from test_preprocessing_hybrid import Parser
from test_chunking import CharacterCounter
from preprocess.hybrid import run_hybrid
from preprocess.hybrid_config import HybridConfig
from preprocess.ingest import from_inputs
from rag.preprocessed_chunks import write
from review_viewer.data import Bundle
from review_viewer.__main__ import handler


class ViewerTests(unittest.TestCase):
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
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.bundle))
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def get(self,path,headers=None):
        return self.opener.open(urllib.request.Request(self.url+path,headers=headers or {}))

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
        with self.get('/page-image/'+page['page_id']) as reply:
            self.assertEqual(reply.headers['Content-Type'],'image/png')
            self.assertTrue(reply.read().startswith(b'\x89PNG'))

    def test_http_contracts_static_headers_and_registered_pdf_range(self):
        with self.get('/api/summary') as reply:
            self.assertEqual(json.load(reply)['pages'],2)
            self.assertEqual(reply.headers['Cache-Control'],'no-store')
        with self.get('/') as reply:
            self.assertIn("script-src 'self'",reply.headers['Content-Security-Policy'])
            self.assertIn('원본 페이지',reply.read().decode())
        with self.get('/source/d1.pdf',{'Range':'bytes=0-4'}) as reply:
            self.assertEqual(reply.status,206);self.assertEqual(reply.read(),b'%PDF-')
        with self.get('/source/d1.pdf',{'Range':'bytes=-8'}) as reply:
            self.assertEqual(reply.status,206);self.assertEqual(len(reply.read()),8)

    def test_unknown_paths_traversal_and_foreign_hosts_cannot_read_files(self):
        for path,headers,expected in [('/../source.pdf',None,404),('/source/../source.pdf',None,404),
            ('/api/asset/../../source.pdf',None,404),('/image/../../source.pdf',None,404),
            ('/api/summary',{'Host':'other.example'},403),('/source/d1.pdf',{'Range':'bytes=99999999-'},416)]:
            with self.assertRaises(urllib.error.HTTPError) as exc:self.get(path,headers)
            self.assertEqual(exc.exception.code,expected)

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
