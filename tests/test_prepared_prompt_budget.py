from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import json
import unittest
from rag import preprocessed_prompt,selection_candidate


class Counter:
    basis='synthetic character counter'
    def messages(self,messages):return sum(len(m['content']) for m in messages)
    def one(self,text):return len(text)


class PreparedPromptBudgetTests(unittest.TestCase):
    def client(self):return SimpleNamespace(tokenizer=Counter(),input_limit=50000,output_tokens=3000,config=SimpleNamespace(num_ctx=53000))

    def test_same_catalog_builds_identical_messages_and_schema(self):
        e={'E1':{'text':'<table><tr><th>Parameter</th><th>Arm A</th></tr><tr><td>Tmax (h) Mean (SD)</td><td>9.7 (1.2)</td></tr></table>',
             'document_id':'new','version_id':'v','product_id':'SB17'}}
        q='군별 Tmax 평균과 SD 및 단위는?';u={'studies':[]};unit={}
        with patch('rag.source_selection.prompt',return_value=[{'role':'system','content':'rules'},{'role':'user','content':'sources'}]):
            expected_messages=selection_candidate.prompt(q,u,unit,e)
            expected_schema=selection_candidate.schema(selection_candidate.catalog(e,q))
            original=selection_candidate.catalog
            with patch('rag.selection_candidate.catalog',wraps=original) as calls:
                messages,schema=selection_candidate.prepare_prompt(q,u,unit,e)
                self.assertEqual(1,calls.call_count)
            self.assertEqual(expected_messages,messages);self.assertEqual(expected_schema,schema)

    def test_cache_invalidates_on_changed_source_or_provenance_and_is_query_local(self):
        calls=[]
        def prepare(q,u,unit,blocks):
            calls.append((q,deepcopy(blocks)))
            return [{'role':'user','content':q+json.dumps(blocks,sort_keys=True)}],{'name':'source_ids','schema':{}}
        strategy=SimpleNamespace(prepare_prompt=prepare,cues=lambda *a:[],schema=lambda c:{},catalog=lambda *a:{})
        adapter=SimpleNamespace()
        make=preprocessed_prompt.configure_budget(adapter,self.client(),'Q1',{'search_terms':'q'}, {}, [],'',builder=lambda *a:[],selection_strategy=strategy)
        b={'E1':{'text':'9.7','structure':{'section_id':'first'}}}
        first=make(b);adapter.budget['fits'](b);make(deepcopy(b))
        self.assertEqual(2,len(calls)) # empty base + one identical supplied source
        b['E1']['text']='8.1';self.assertNotEqual(first,make(b))
        b['E1']['structure']['section_id']='other';make(b)
        self.assertEqual(4,len(calls))
        other=preprocessed_prompt.configure_budget(SimpleNamespace(),self.client(),'Q2',{'search_terms':'q'}, {}, [],'',builder=lambda *a:[],selection_strategy=strategy)
        self.assertNotEqual(make(b),other(b))
        self.assertEqual(6,len(calls))

    def test_oversized_enum_still_fails_budget_preflight(self):
        strategy=SimpleNamespace(cues=lambda *a:[],
            prepare_prompt=lambda *a:([{'role':'user','content':'x'}],{'name':'ids','schema':{'type':'string','enum':[str(i) for i in range(1001)]}}),
            schema=lambda c:{},catalog=lambda *a:{})
        adapter=SimpleNamespace()
        preprocessed_prompt.configure_budget(adapter,self.client(),'Q',{'search_terms':'q'}, {}, [],'',builder=lambda *a:[],selection_strategy=strategy)
        self.assertFalse(adapter.budget['fits']({'E1':{'text':'source'}}))

if __name__=='__main__':unittest.main()
