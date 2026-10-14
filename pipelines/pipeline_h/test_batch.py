"""Prevent source/result cross-wiring when multiple crops share one call."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from pipelines.pipeline_h.vlm_batch import predict

class BatchIdentityTests(unittest.TestCase):
    def setUp(self):
        self.jobs=[{'task':'table','image':str(Path(name).resolve())} for name in ('source-a.png','source-b.png')]
        self.settings={'max_new_tokens':4096}

    def model(self,paths):
        outputs=[SimpleNamespace(json={'res':{'input_path':path,'parsing_res_list':[
            {'block_content':'<table><tr><td>37 (46.8%)</td></tr></table>'}]}}) for path in paths]
        return SimpleNamespace(predict=lambda **kwargs:outputs)

    @patch('pipelines.pipeline_h.vlm_batch.configure')
    def test_swapped_results_are_rejected(self,configure):
        with self.assertRaisesRegex(ValueError,'source identity'):
            predict(self.model([j['image'] for j in reversed(self.jobs)]),self.jobs,self.settings,2)

    @patch('pipelines.pipeline_h.vlm_batch.configure')
    def test_missing_result_is_rejected(self,configure):
        with self.assertRaisesRegex(ValueError,'count mismatch'):
            predict(self.model([self.jobs[0]['image']]),self.jobs,self.settings,2)

    @patch('pipelines.pipeline_h.vlm_batch.configure')
    def test_own_source_and_unmodified_content_retained(self,configure):
        reports,elapsed=predict(self.model([j['image'] for j in self.jobs]),self.jobs,self.settings,2)
        self.assertEqual([r['job']['image'] for r in reports],[j['image'] for j in self.jobs])
        self.assertTrue(all('37 (46.8%)' in r['content'] for r in reports))
        self.assertAlmostEqual(sum(r['seconds'] for r in reports),elapsed)

if __name__=='__main__':unittest.main()
