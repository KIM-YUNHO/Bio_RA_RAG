import json,tempfile,unittest
from pathlib import Path
from tmp.collect_visual_queue import extraction_folder,collect

class SelectiveQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.workflow=self.root/'workflow';self.extraction=self.workflow/'extraction'
        self.extraction.mkdir(parents=True)
        (self.extraction/'extraction_manifest.json').write_text('{}',encoding='utf-8')
    def tearDown(self):self.tmp.cleanup()
    def test_workflow_and_direct_component_resolve_same_bundle(self):
        self.assertEqual(extraction_folder(self.workflow),self.extraction.resolve())
        self.assertEqual(extraction_folder(self.extraction),self.extraction.resolve())
    def test_workflow_report_selects_final_labels(self):
        labels=self.workflow/'labels';labels.mkdir()
        (labels/'extraction_manifest.json').write_text('{}',encoding='utf-8')
        (self.workflow/'workflow_report.json').write_text(json.dumps({'final_extraction_directory':str(labels)}),encoding='utf-8')
        self.assertEqual(extraction_folder(self.workflow),labels.resolve())
    def test_completed_and_reviewed_assets_do_not_reenter_model_queue(self):
        plan=self.root/'plan.json';plan.write_text(json.dumps({'documents':[{'version_id':'v'}],'protected_exclusions':[]}),encoding='utf-8')
        (self.root/'corpus_progress.json').write_text(json.dumps({'plan_path':str(plan),'components':[[str(self.workflow),'unused']], 'documents':{}}),encoding='utf-8')
        (self.extraction/'extraction_integrity.json').write_text('{}',encoding='utf-8')
        rows=[]
        for i,status in enumerate(('heuristic_checks_passed','needs_review',None)):
            rows.append({'asset_id':str(i),'document_id':'d','version_id':'v','pdf_page':1,
                'bbox_pdf':[0,0,10,10],'bbox_input_px':[0,0,10,10],'source_refs':[],
                'base':{},'native_reference':{},'route':{'task':'repair_table','reasons':[]},
                'enrichment':{'status':status} if status else None})
        (self.extraction/'extracted_assets.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
        report=collect(self.root,self.root/'queue')
        self.assertEqual(report['tasks'],1)
        self.assertEqual(report['preserved_prior_results'],{'heuristic_checks_passed':1,'needs_review':1})

if __name__=='__main__':unittest.main()
