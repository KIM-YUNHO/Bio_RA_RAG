from copy import deepcopy
import unittest
from unittest.mock import patch
from preprocess import queue_finalize as qf


class QueueFinalizeTests(unittest.TestCase):
    def setUp(self):
        self.asset={'base':{'content':'literal'},'native_reference':{'reliable':True,'text':'literal'},
            'light_region_ocr':{'raw_sha256':'ocr'},'route':{'task':'repair_table','next_stage':'qwen','reasons':['irregular_grid']}}
        self.gate={'geometry':None,'complexity_confirmed':False,'decision':'structural_complexity_unconfirmed_review'}

    def test_no_positive_structure_becomes_review_and_keeps_original_route(self):
        original=deepcopy(self.asset['route'])
        with patch.object(qf,'apply_asset',return_value='source_grid_unconfirmed'),patch.object(qf,'table_gate',return_value=self.gate):
            self.assertEqual(qf.finish_asset(self.asset,None),'explicit_review_tables')
            qf.validate_review(self.asset,None)
            self.assertEqual(self.asset['superseded_unconfirmed_route'],original)
            self.assertIsNone(self.asset['route']['task'])
            self.asset['route']['next_stage']='qwen'
            with self.assertRaisesRegex(ValueError,'model queue'):qf.validate_review(self.asset,None)

    def test_review_fingerprint_cannot_hide_changed_ocr_or_geometry(self):
        with patch.object(qf,'apply_asset',return_value='source_grid_unconfirmed'),patch.object(qf,'table_gate',return_value=self.gate):
            qf.finish_asset(self.asset,None);self.asset['light_region_ocr']['raw_sha256']='changed'
            with self.assertRaisesRegex(ValueError,'source geometry/OCR'):qf.validate_review(self.asset,None)

    def test_genuinely_unresolved_complex_cells_cannot_be_marked_complete(self):
        self.gate.update(geometry={'merged_cells':True},complexity_confirmed=True)
        self.asset['source_grid_refinement']={'plan':{'issues':['cell_text_requires_refinement'],'unresolved_cells':['R0C0']}}
        with patch.object(qf,'apply_asset',return_value='source_cells_require_review'),patch.object(qf,'table_gate',return_value=self.gate):
            with self.assertRaisesRegex(ValueError,'bounded transcription'):qf.finish_asset(self.asset,None)
        self.assertNotIn('source_grid_gate_review',self.asset)

    def test_recovered_table_does_not_get_a_review_receipt(self):
        with patch.object(qf,'apply_asset',return_value='recovered_tables'):
            self.assertEqual(qf.finish_asset(self.asset,None),'recovered_tables')
        self.assertNotIn('source_grid_gate_review',self.asset)

    def test_native_packet_cache_requires_exact_source_version(self):
        from preprocess.hybrid_validate import _source_packet,_NATIVE_PACKETS
        from unittest.mock import Mock
        _NATIVE_PACKETS.clear();build=Mock(return_value={'text':'original'})
        self.assertEqual(_source_packet(('file','sha1',1,100,100),build),{'text':'original'})
        _source_packet(('file','sha1',1,100,100),build);self.assertEqual(build.call_count,1)
        _source_packet(('file','sha2',1,100,100),build);self.assertEqual(build.call_count,2)
        _NATIVE_PACKETS.clear()


if __name__=='__main__':unittest.main()
