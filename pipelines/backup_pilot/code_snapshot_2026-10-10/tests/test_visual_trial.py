import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from preprocess.ingest import sha256
from preprocess.visual_trial import verify_trial, execute


class TrialTests(unittest.TestCase):
    def fixture(self,root):
        specdir=root/'spec';specdir.mkdir();dataset=root/'dataset';dataset.mkdir()
        (dataset/'seal.json').write_text('{}')
        (specdir/'catalog.jsonl').write_text('{}\n')
        code=root/'preprocess'/'example.py';code.parent.mkdir();code.write_text('version=1')
        snap=specdir/'code_snapshot'/'preprocess'/'example.py';snap.parent.mkdir(parents=True);snap.write_bytes(code.read_bytes())
        spec={'dataset':str(dataset),'root':str(root),'dataset_seal_sha256':sha256(dataset/'seal.json'),
              'catalog_sha256':sha256(specdir/'catalog.jsonl'),'code_sha256':{'preprocess/example.py':sha256(code)}}
        (specdir/'trial.json').write_text(json.dumps(spec))
        (specdir/'seal.json').write_text(json.dumps({'trial_sha256':sha256(specdir/'trial.json')}))
        return specdir,code

    def test_code_tampering_denied_before_inference(self):
        with tempfile.TemporaryDirectory() as tmp,patch('preprocess.visual_trial.verify'):
            folder,code=self.fixture(Path(tmp));verify_trial(folder)
            code.write_text('version=2')
            with self.assertRaisesRegex(ValueError,'implementation changed'):verify_trial(folder)

    def test_source_annotations_must_be_sealed_before_model_or_output(self):
        with tempfile.TemporaryDirectory() as tmp,patch('preprocess.visual_trial.verify'),patch('preprocess.visual_trial.run_hybrid') as model:
            root=Path(tmp);folder,_=self.fixture(root)
            with self.assertRaisesRegex(ValueError,'source-first annotations'):execute(folder,root/'out')
            self.assertFalse((root/'out').exists());model.assert_not_called()
            (folder/'source_annotations.json').write_text('{}')
            (folder/'annotation_seal.json').write_text(json.dumps({'sha256':'wrong'}))
            with self.assertRaisesRegex(ValueError,'Annotations changed'):execute(folder,root/'out')
            model.assert_not_called()


if __name__=='__main__':unittest.main()
