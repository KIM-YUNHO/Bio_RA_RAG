import contextlib,io,json,tempfile,unittest
from pathlib import Path
import fitz
from preprocess.hybrid import run_hybrid,read_jsonl
from preprocess.hybrid_config import HybridConfig
from preprocess.ingest import from_inputs,stable_hash,sha256
from preprocess.planned_enrichment import execute,bindings
from tests.preprocessing.test_preprocessing_hybrid import Parser, Qwen, BAD

class PlannedEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        source=self.root/'source.pdf';self.source=self.root/'extracted';self.out=self.root/'enriched'
        with fitz.open() as pdf:
            for _ in range(2):
                page=pdf.new_page(width=400,height=400)
                for x,y,text in ((40,70,'Group'),(150,70,'N'),(40,100,'A'),(150,100,'10'),(40,130,'B'),(150,130,'20')):page.insert_text((x,y),text)
            pdf.save(source)
        self.docs=from_inputs([source],self.source);self.config=HybridConfig()
        with contextlib.redirect_stdout(io.StringIO()):run_hybrid(self.docs,self.source,self.config,paddle=Parser(table=BAD),use_qwen=False)
        self.assets=read_jsonl(self.source/'extracted_assets.jsonl');self.tasks=[]
        for a in self.assets:
            if not a['route']['task']:continue
            self.tasks.append({**{k:a[k] for k in ('asset_id','document_id','version_id','pdf_page','bbox_pdf','bbox_input_px','source_refs')},
                'extraction':str(self.source),'task':a['route']['task'],'input_state_sha256':stable_hash([a['base'],a['native_reference']])})
        self.eligible={d.version_id for d in self.docs}
    def tearDown(self):self.tmp.cleanup()
    def test_only_planned_region_calls_model_and_completed_region_is_not_repeated(self):
        qwen=Qwen();before=sha256(self.source/'extracted_assets.jsonl')
        with contextlib.redirect_stdout(io.StringIO()):
            result=execute(self.source,self.out,self.tasks[:1],self.config,self.eligible,set(),adapter=qwen)
            again=execute(self.source,self.out,self.tasks[:1],self.config,self.eligible,set(),adapter=qwen)
        self.assertEqual(result['attempted_calls'],1);self.assertEqual(again['attempted_calls'],0)
        self.assertEqual(qwen.calls,['repair_table'])
        new={a['asset_id']:a for a in read_jsonl(self.out/'extracted_assets.jsonl')}
        unplanned=self.assets[1]
        self.assertEqual(new[unplanned['asset_id']]['enrichment'],unplanned['enrichment'])
        self.assertEqual(sha256(self.source/'extracted_assets.jsonl'),before)
        self.assertFalse(any(a['numeric_evidence_eligible'] for a in new.values()))
    def test_changed_task_and_protected_source_rejected_before_model_call(self):
        qwen=Qwen();task={**self.tasks[0],'pdf_page':99}
        with self.assertRaisesRegex(ValueError,'no longer matches'):execute(self.source,self.out,[task],self.config,self.eligible,set(),adapter=qwen)
        with self.assertRaisesRegex(ValueError,'Out-of-scope'):execute(self.source,self.out,self.tasks[:1],self.config,self.eligible,self.eligible,adapter=qwen)
        self.assertEqual(qwen.calls,[])

    def test_new_contract_executes_and_changed_selection_cannot_reuse_a_frozen_task(self):
        from preprocess.hybrid import save_outputs
        from preprocess.visual_router import decide
        from preprocess.visual_task_inputs import planned_task
        target=self.assets[0]
        target['route']=decide('table',{'issues':['source_numbers_missing']},
            light={'tokens':[]},complexity={'irregular_grid':True})
        summary=json.loads((self.source/'extraction_summary.json').read_text(encoding='utf-8'))
        save_outputs(self.source,self.assets,read_jsonl(self.source/'extraction_pages.jsonl'),self.docs,
            summary,read_jsonl(self.source/'visual_groups.jsonl'))
        task=planned_task(self.source,target,self.assets,self.docs)
        self.assertEqual(len(bindings(self.source,[task],self.eligible,set())),1)
        qwen=Qwen()
        with contextlib.redirect_stdout(io.StringIO()):
            result=execute(self.source,self.out,[task],self.config,self.eligible,set(),adapter=qwen)
        self.assertEqual(result['attempted_calls'],1)
        target['selected']['content']='different selected source'
        save_outputs(self.source,self.assets,read_jsonl(self.source/'extraction_pages.jsonl'),self.docs,
            summary,read_jsonl(self.source/'visual_groups.jsonl'))
        with self.assertRaisesRegex(ValueError,'no longer matches'):
            bindings(self.source,[task],self.eligible,set())

if __name__=='__main__':unittest.main()
