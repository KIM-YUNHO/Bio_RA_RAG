"""A new frozen run must reuse only intact, complete, permitted prior documents."""
import contextlib,io,json,tempfile,unittest
from pathlib import Path
import fitz
from preprocess.workflow import run
from preprocess.hybrid_config import HybridConfig
from preprocess.selective_reuse import collect
from test_preprocessing_hybrid import Parser
from test_chunking import CharacterCounter


class SelectiveReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        source=self.root/'source.pdf';self.prior=self.root/'prior';self.new=self.root/'new'
        with fitz.open() as pdf:
            page=pdf.new_page(width=400,height=400);page.insert_text((40,70),'Group A 10 and Group B 20');pdf.save(source)
        self.run=self.prior/'documents/fixture'
        with contextlib.redirect_stdout(io.StringIO()):
            run(self.run,HybridConfig(),inputs=[source],paddle=Parser(),use_qwen=False,label_ocr=False,embedding=False,counter=CharacterCounter())
        doc=json.loads((self.run/'workflow_manifest.json').read_text(encoding='utf-8'))['documents'][0]
        self.key=doc['version_id'];self.plan={'documents':[{'version_id':self.key,'document_id':doc['document_id'],'product_id':'SB17'}],'protected_exclusions':[]}
    def tearDown(self):self.tmp.cleanup()
    def test_full_document_reuses_a_separate_identical_candidate(self):
        result=collect(self.prior,self.plan,self.new)[self.key]
        self.assertEqual(result['pages'],1)
        self.assertNotEqual(Path(result['candidate']),self.run/'candidate')
        self.assertEqual((Path(result['candidate'])/'chunks.jsonl').read_bytes(),(self.run/'candidate/chunks.jsonl').read_bytes())
    def test_modified_candidate_cannot_be_reused(self):
        path=self.run/'candidate/source_units.jsonl';path.write_bytes(path.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'candidate changed'):collect(self.prior,self.plan,self.new)
    def test_incomplete_workflow_and_protected_document_are_rejected(self):
        self.plan['protected_exclusions']=[{'version_id':self.key}]
        with self.assertRaisesRegex(ValueError,'Protected'):collect(self.prior,self.plan,self.new)
        self.plan['protected_exclusions']=[]
        path=self.run/'workflow_manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'));manifest['lifecycle']='running'
        path.write_text(json.dumps(manifest),encoding='utf-8')
        self.assertEqual(collect(self.prior,self.plan,self.new),{})


if __name__=='__main__':unittest.main()
