"""A parent-only hash must not authorize changed child OCR or context."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from preprocess.ingest import stable_hash
from preprocess.visual_router import decide
from preprocess.visual_task_inputs import input_state,planned_task


class InputBindingTests(unittest.TestCase):
    def setUp(self):
        self.target={'asset_id':'parent','document_id':'document','version_id':'version','pdf_page':1,
            'bbox_pdf':[0,0,20,20],'bbox_input_px':[0,0,200,200],'source_refs':[],
            'base':{},'native_reference':{},'selected':{'origin':'caption','content':'Figure caption'},
            'member_asset_ids':['panel'],
            'route':decide('figure_group',light={'tokens':[]},complexity={'chart_axes':True})}
        self.panel={'asset_id':'panel','base':{},'light_region_ocr':{'raw_sha256':'first',
            'provenance':{'ocr':{'tokens':[{'text':'Time','confidence':.99}]}}}}
        self.caption={'asset_id':'caption','base':{'content':'Original caption'}}
        self.assets=[self.target,self.panel,self.caption]
        self.context={'context_items':[{'asset_id':'caption','text':'Original caption'}]}
        self.docs=[SimpleNamespace(document_id='document',version_id='version')]

    def digest(self):return stable_hash(input_state(self.target,self.assets,self.docs,self.context))

    def test_member_raw_proof_text_and_context_changes_invalidate_parent_binding(self):
        original=self.digest();self.panel['light_region_ocr']['raw_sha256']='changed'
        self.assertNotEqual(self.digest(),original)
        original=self.digest()
        self.panel['light_region_ocr']['provenance']['ocr']['tokens'][0]['text']='Dose'
        self.assertNotEqual(self.digest(),original)
        original=self.digest()
        self.caption['base']['content']='different caption'
        self.assertNotEqual(self.digest(),original)

    def test_state_capture_does_not_mutate_source_or_depend_on_asset_storage_order(self):
        before=deepcopy(self.assets);expected=self.digest();self.assets.reverse()
        self.assertEqual(self.digest(),expected);self.assets.reverse();self.assertEqual(self.assets,before)

    def test_previous_candidate_queue_cannot_be_planned_as_current_complex_work(self):
        self.target['route']={'task':'describe_figure','reasons':['multiple_panels']}
        with self.assertRaisesRegex(ValueError,'Reclassify'):
            planned_task('source',self.target,self.assets,self.docs)

    def test_response_contract_change_invalidates_a_frozen_task_without_model_calls(self):
        from preprocess.qwen_contracts import PROMPT_VERSIONS
        original=self.digest()
        with patch.dict(PROMPT_VERSIONS,{'describe_figure':'changed-contract'}):
            self.assertNotEqual(self.digest(),original)


if __name__=='__main__':unittest.main()
