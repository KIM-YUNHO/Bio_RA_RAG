import json
from types import SimpleNamespace
import unittest
from rag.answer import answer_unit
from rag.source_selection import catalog,comparison_choices,coverage_correction


class CoverageCorrectionTests(unittest.TestCase):
    question='NovelDrug 시험에서 ANCOVA의 US Reference 대비, EU Reference와 US Reference 간 AUCinf, Cmax 기하평균비와 90% CI는?'
    def fixture(self,mode='improve'):
        text='Table 4. ANCOVA with Weight as a Covariate (Ad-hoc Analysis)\n<table><tr><th rowspan="2">Parameter</th><th rowspan="2">Treatment</th><th colspan="2">Ratio of NovelDrug/Reference</th><th colspan="2">Ratio of EU Reference /US Reference</th></tr><tr><th>Estimate</th><th>90% CI</th><th>Estimate</th><th>90% CI</th></tr><tr><td>AUCinf</td><td>US Reference</td><td>1.13</td><td>1.01, 1.20</td><td>0.95</td><td>0.88, 1.02</td></tr><tr><td>Cmax</td><td>US Reference</td><td>1.11</td><td>1.00, 1.22</td><td>0.97</td><td>0.90, 1.04</td></tr></table>'
        evidence={'E1':{'text':text,'product_id':'NovelDrug','document_id':'doc','version_id':'v','pdf_page':3,
            'title':'Test source','document_type':'Assessment','structure':{},'source_spans':[]}}
        candidates=catalog(evidence,self.question)
        choices=comparison_choices(candidates,self.question,'NovelDrug')
        self.assertEqual(len(choices),8)
        def output(rows):
            items=[]
            for _,_,_,cid in rows:
                headers=[candidates[k] for k in candidates[cid]['context_ids']]
                ratio=next(c for c in headers if c['value'].startswith('Ratio'))
                items.append({'study':'none','item':'result','source_label':'원문 결과','value_id':cid,'exact':True,
                    'conditions':[{'dimension':'comparison','source_id':ratio['id']}]})
            return {'status':'answered','premise_source_ids':[],'items':items}
        class Client:
            source_selection=True
            count=0
            messages=[]
            def __call__(self,messages,**kwargs):
                self.count+=1;self.messages.append(messages)
                rows=choices if mode=='complete' or self.count==2 and mode=='improve' else [r for r in choices if r[0].startswith('eu')]
                if self.count==2 and mode in ('missing_only','partial_missing'):
                    rows=[r for r in choices if not r[0].startswith('eu')]
                    if mode=='partial_missing':rows=rows[:-1]
                return {'text':json.dumps(output(rows)),'model_reported':'fake','prompt_tokens':100,'completion_tokens':50}
        class Adapter:
            last_omitted=[]
            count=0
            def from_pages(self,*args):self.count+=1;return evidence
            def attach_quote_links(self,*args):pass
        client=Client();adapter=Adapter()
        return evidence,candidates,client,adapter
    def run_answer(self,mode):
        evidence,candidates,client,adapter=self.fixture(mode)
        out=answer_unit(self.question,{'english_query':self.question,'studies':[],'topic':'gmr'},
            {'product':'NovelDrug','jurisdiction':'EU','studies':[]},[],SimpleNamespace(evidence_adapter=adapter),chat_client=client)
        return out,client,adapter,evidence,candidates
    def test_missing_supported_comparison_is_corrected_once_using_same_evidence(self):
        out,client,adapter,_,candidates=self.run_answer('improve')
        self.assertEqual(client.count,2);self.assertEqual(adapter.count,1)
        self.assertEqual(out['status'],'answered');self.assertEqual(len(out['items']),8)
        self.assertTrue(out['validation']['coverage_repair']['accepted'])
        self.assertFalse(out['validation']['requested_comparison_coverage']['missing'])
        self.assertIn('printed value ID',client.messages[1][-1]['content'])
        self.assertTrue(all(i['value']==candidates[i['source_selection']['value_id']]['value'] for i in out['items']))
    def test_failed_correction_preserves_original_partial_and_stops(self):
        out,client,adapter,_,_=self.run_answer('repeat')
        self.assertEqual(client.count,2);self.assertEqual(adapter.count,1)
        self.assertEqual(out['status'],'partial');self.assertEqual(len(out['items']),4)
        self.assertFalse(out['validation']['coverage_repair']['accepted'])
    def test_complete_selection_makes_no_correction_request(self):
        out,client,adapter,_,_=self.run_answer('complete')
        self.assertEqual(client.count,1);self.assertEqual(adapter.count,1)
        self.assertEqual(out['status'],'answered');self.assertNotIn('coverage_repair',out['validation'])
    def test_unsupported_missing_slot_does_not_trigger_model_call(self):
        evidence,candidates,_,_=self.fixture()
        out={'items':[],'validation':{'requested_comparison_coverage':{'missing':[{'comparison':'unknown/us reference','endpoint':'Cmax','statistic':'GMR'}]}}}
        self.assertIsNone(coverage_correction(out,candidates,self.question,'NovelDrug'))

    def test_missing_only_correction_keeps_initial_facts_and_completes_grid(self):
        out,client,adapter,_,_=self.run_answer('missing_only')
        self.assertEqual(client.count,2);self.assertEqual(adapter.count,1)
        self.assertEqual(len(out['items']),8);self.assertEqual(out['status'],'answered')
        self.assertEqual(out['validation']['coverage_repair']['retry_coverage']['covered_count'],4)
        self.assertEqual(out['validation']['coverage_repair']['merged_coverage']['covered_count'],8)

    def test_still_incomplete_merge_remains_partial(self):
        out,client,_,_,_=self.run_answer('partial_missing')
        self.assertEqual(client.count,2);self.assertEqual(len(out['items']),7)
        self.assertEqual(out['status'],'partial')
        self.assertEqual(len(out['validation']['requested_comparison_coverage']['missing']),1)


if __name__=='__main__':unittest.main()
