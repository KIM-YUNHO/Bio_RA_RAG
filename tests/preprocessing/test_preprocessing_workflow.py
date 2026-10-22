"""The complete upload path must work without per-document rules or hidden page selection."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
import numpy as np
from tests.preprocessing.test_preprocessing_hybrid import Parser, Qwen
from tests.index.test_chunking import CharacterCounter
from preprocess.hybrid_config import HybridConfig
from preprocess.workflow import run, read


class Embeddings:
    calls=0
    def __init__(self,*a,**k):self.device='cpu';self.truncated=0;self.revision='fixture'
    def encode_docs(self,chunks):
        type(self).calls+=1
        return np.tile(np.array([1.,0.,0.],dtype=np.float32),(len(chunks),1))
    def encode_queries(self,queries):return np.tile(np.array([1.,0.,0.],dtype=np.float32),(len(queries),1))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.pdf=self.root/'upload.pdf';self.out=self.root/'workflow';self.config=HybridConfig()
        with fitz.open() as pdf:
            for _ in range(3):
                page=pdf.new_page(width=400,height=400)
                for x,y,text in ((40,70,'Group'),(150,70,'N'),(40,100,'A'),(150,100,'10'),(40,130,'B'),(150,130,'20')):
                    page.insert_text((x,y),text)
            pdf.save(self.pdf)
        self.options=dict(use_qwen=False,label_ocr=False,counter=CharacterCounter(),
            index_factory=Embeddings,device='cpu',queries=['Group A'])
    def tearDown(self):self.temp.cleanup()
    def execute(self,**kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run(self.out,self.config,**{**self.options,**kwargs})

    def test_whole_upload_to_real_chunk_index_search_and_report(self):
        parser=Parser();result=self.execute(inputs=[self.pdf],paddle=parser)
        self.assertTrue(result['execution_complete'])
        self.assertEqual(result['documents'][0]['parsed_pages'],3)
        self.assertTrue(result['documents'][0]['all_pages_attempted'])
        self.assertEqual(parser.calls,3)
        self.assertFalse(result['active_index_updated'])
        searches=read(self.out/'candidate/search_examples.json')
        self.assertTrue(searches[0]['results'])
        self.assertTrue((self.out/'workflow_report.md').exists())
        self.assertTrue((self.out/'extraction/originals').exists())

    def test_completed_resume_does_not_call_parser_or_embed_again(self):
        self.execute(inputs=[self.pdf],paddle=Parser());before=Embeddings.calls
        parser=Parser();result=self.execute(resume=True,paddle=parser)
        self.assertEqual(parser.calls,0);self.assertEqual(Embeddings.calls,before)
        self.assertTrue(result['execution_complete'])

    def test_resume_after_index_failure_reuses_extraction_and_chunks(self):
        def failed(*a,**k):raise RuntimeError('embedding unavailable')
        with self.assertRaisesRegex(RuntimeError,'embedding unavailable'):
            self.execute(inputs=[self.pdf],paddle=Parser(),index_factory=failed)
        self.assertEqual(read(self.out/'workflow_manifest.json')['stage'],'index')
        parser=Parser();result=self.execute(resume=True,paddle=parser)
        self.assertEqual(parser.calls,0);self.assertTrue(result['execution_complete'])

    def test_resume_refuses_tampered_candidate_artifact(self):
        self.execute(inputs=[self.pdf],paddle=Parser())
        path=self.out/'candidate/source_units.jsonl';path.write_bytes(path.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'stage changed: chunk'):
            self.execute(resume=True,paddle=Parser())

    def test_qwen_stage_runs_and_release_precedes_index(self):
        qwen=Qwen();parser=Parser(figure=True)
        def factory(*a,**kw):
            self.assertTrue(qwen.unloaded);self.assertTrue(parser.closed)
            return Embeddings(*a,**kw)
        result=self.execute(inputs=[self.pdf],paddle=parser,qwen=qwen,use_qwen=True,index_factory=factory)
        self.assertTrue(qwen.calls);self.assertTrue(result['execution_complete'])

    def test_nonempty_output_and_changed_options_are_rejected(self):
        self.execute(inputs=[self.pdf],paddle=Parser())
        with self.assertRaisesRegex(ValueError,'output must be empty'):
            self.execute(inputs=[self.pdf],paddle=Parser())
        with self.assertRaisesRegex(ValueError,'original configuration'):
            self.execute(resume=True,queries=['different'])

    def test_unreadable_page_is_reported_and_other_pages_continue(self):
        result=self.execute(inputs=[self.pdf],paddle=Parser(fail_once=True))
        self.assertEqual(result['documents'][0]['parsed_pages'],2)
        self.assertTrue(result['documents'][0]['all_pages_attempted'])
        self.assertEqual(result['content_state'],'partial_failure')


if __name__=='__main__':unittest.main()
