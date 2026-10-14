"""Completion gate must never promote a failed/unfinished corpus."""
import importlib.util,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tmp'))
import finalize_corpus_update as final

class CompletionGate(unittest.TestCase):
    def test_staged_prompt_matches_required_study_citation_contract(self):
        path=ROOT/'data/runs/corpus_update_2026-10-09/service_patch/answer_conditions.py'
        spec=importlib.util.spec_from_file_location('staged_answer_conditions',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertIn('dimension study',module.QUOTE_RULES)
        self.assertIn('primary results first',module.QUOTE_RULES)
        self.assertEqual(module.missing([{'conditions':[]}],'question',{'studies':['ST_SB17_1001']}),[{'item_index':0,'dimensions':['study']}])
    def kernel(self):
        kernel=MagicMock();kernel.OpenProcess.return_value=0x100000001
        kernel.WaitForSingleObject.return_value=0
        return kernel
    def test_worker_must_finish_the_expected_stage_before_promotion(self):
        kernel=self.kernel()
        with patch.object(final.ctypes,'WinDLL',return_value=kernel),patch.object(final,'save'),patch.object(final,'read',return_value={'stage':'extracting','lifecycle':'running'}):
            with self.assertRaisesRegex(RuntimeError,'before completing'):final.wait_worker(123)
        kernel.CloseHandle.assert_called_once_with(0x100000001)
    def test_completed_worker_handle_is_closed(self):
        kernel=self.kernel()
        with patch.object(final.ctypes,'WinDLL',return_value=kernel),patch.object(final,'save'),patch.object(final,'read',return_value={'stage':'ready_to_compose','lifecycle':'finished'}):
            final.wait_worker(123)
        kernel.CloseHandle.assert_called_once_with(0x100000001)
    def test_missing_coverage_stops_before_composition_or_server_changes(self):
        with patch.object(final,'wait_worker'),patch.object(final,'save'),patch.object(final,'read',return_value={'documents':{}}),patch.object(final,'audit',return_value={
            'protected_versions_processed':[],'documents':[{'status':'extracted','embedding_ready':True,'pages':{'parsed':5},'all_page_coverage':False}]}),patch.object(final.subprocess,'run') as execute:
            with self.assertRaisesRegex(ValueError,'unfinished'):final.main(123)
            execute.assert_not_called()

if __name__=='__main__':unittest.main()
