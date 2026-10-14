"""The main service delegates to the original RAG stages with source-safe evidence."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import numpy as np

import test_review_viewer
from preprocess.hybrid_config import HybridConfig
from rag import answer
from rag.pipeline import RAGPipeline
from rag.plan_query import plan
from rag.understand_query import resolve


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_review_viewer.ViewerTests('test_pages_assets_and_chunks_preserve_same_source_and_review_state')
        self.fixture.setUp();self.bundle=self.fixture.bundle
        self.chunks=[json.loads(line) for line in (self.bundle.candidate/'chunks.jsonl').read_text(encoding='utf-8').splitlines()]
        for chunk in self.chunks:chunk.update(source='EMA',jurisdiction='EU')
        self.meta={'dim':2,'n_chunks':len(self.chunks),'truncated_inputs':0,'chunks_sha256':self.bundle.chunks_sha256,'model':'BAAI/bge-m3'}
        self.index=(self.chunks,np.tile([1.,0.],(len(self.chunks),1)),self.meta)
        self.calls=[]
        self.schemas=[]
        def client(messages,json_schema=None,provider=None):
            self.calls.append(json_schema['name'])
            self.schemas.append(deepcopy(json_schema))
            if json_schema['name']=='query_understanding':
                value={'intent':'fact_lookup','products':[],'studies':[],'study_scope':'unspecified','study_mentions':[],
                    'jurisdictions':[],'jurisdiction_mentions':[],'doc_groups':[],'topic':'other','english_query':'What is Group?', 'keywords':['Group']}
            else:
                value={'status':'answered','items':[{'study':'none','item':'heading','source_label':'표 제목','value':'Group',
                    'quote':'Group','exact':True,'evidence':['E1']}],'answer_ko':'Uncited 999 subjects.'}
            return {'text':json.dumps(value),'model_reported':'fake-local','prompt_tokens':10,'completion_tokens':5}
        class Encoder:
            def encode_queries(self,queries):return np.tile([1.,0.],(len(queries),1))
        self.patches=[patch('rag.pipeline.load_index',return_value=self.index),patch('rag.answer.load_index',return_value=self.index)]
        for item in self.patches:item.start()
        self.pipeline=RAGPipeline(bundle=self.bundle,config=HybridConfig(),chat_client=client,encoder_factory=Encoder)

    def tearDown(self):
        for item in reversed(self.patches):item.stop()
        self.fixture.tearDown()

    def test_original_understanding_retrieval_and_answer_stages_are_used(self):
        with patch('rag.answer.retrieve',wraps=answer.retrieve) as retrieve,patch('rag.answer.answer_unit',wraps=answer.answer_unit) as generate:
            report=self.pipeline.ask('What is Group?')
        self.assertEqual(self.calls,['query_understanding','unit_answer'])
        retrieve.assert_called_once();generate.assert_called_once()
        self.assertEqual(retrieve.call_args.kwargs['k'], 5)
        self.assertTrue(report['results'][0]['evidence'])
        self.assertIn('Group',report['results'][0]['answer_ko'])
        self.assertNotIn('999',report['results'][0]['answer_ko'])
        self.assertEqual(report['index']['chunks_sha256'],self.bundle.chunks_sha256)
        self.assertFalse(report['numeric_accuracy_certified'])
        self.assertEqual(self.schemas[-1]['schema']['properties']['items']['items']['properties']['evidence']['items']['enum'],list(report['results'][0]['evidence']))

    def test_injected_api_provider_is_reported_in_summary_and_answer(self):
        self.pipeline.client.provider='openai';self.pipeline.client.model='gpt-4o-mini';self.pipeline.client.local_only=False
        self.assertEqual(self.pipeline.summary()['answer_model'],'gpt-4o-mini')
        report=self.pipeline.ask('What is Group?')
        self.assertEqual(report['generation_provider'],'openai');self.assertFalse(report['local_only'])

    def test_progress_exposes_real_stages_and_plan(self):
        events=[];report=self.pipeline.ask('What is Group?',progress=lambda **event:events.append(event))
        self.assertEqual([event['stage'] for event in events],['understanding','retrieving','answering'])
        self.assertEqual(len(report['plan']),1)

    def test_japan_and_sponsor_sources_are_unavailable_even_when_nondefault_is_enabled(self):
        self.assertEqual(self.pipeline.agencies,['EU'])
        for agency in ('JP','sponsor'):
            with self.assertRaises(ValueError):self.pipeline.ask('Group?',jurisdictions=[agency],include_nondefault=True)
        self.assertTrue(all(d['jurisdiction']=='EU' for d in self.pipeline.documents))

    def test_generation_failure_preserves_retrieval_evidence_and_failed_call_usage(self):
        from rag.local_chat import GenerationFailure
        original=self.pipeline.client
        class Client:
            def __init__(self):self.calls=[]
            def __call__(self,messages,json_schema=None,provider=None):
                if json_schema['name']=='query_understanding':return original(messages,json_schema,provider)
                telemetry={'prompt_tokens':4000,'completion_tokens':3000,'finish_reason':'length','raw_text':'partial JSON'}
                self.calls.append(telemetry)
                raise GenerationFailure('length',telemetry)
        self.pipeline.client=Client()
        report=self.pipeline.ask('What is Group?')
        result=report['results'][0]
        self.assertEqual(result['status'],'error')
        self.assertTrue(result['evidence'])
        self.assertTrue(result['retrieved_pages'])
        self.assertEqual(result['generation_telemetry'][0]['finish_reason'],'length')
        self.assertEqual(report['usage'],{'input_tokens':4010,'output_tokens':3005})

    def test_retrieval_budget_is_bounded_and_reported(self):
        for value in (0, 11, True, 3.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RAGPipeline(bundle=self.bundle, retrieval_pages=value)
        self.pipeline.retrieval_pages = 3
        with patch('rag.answer.retrieve', wraps=answer.retrieve) as retrieve:
            report = self.pipeline.ask('What is Group?')
        self.assertEqual(retrieve.call_args.kwargs['k'], 3)
        self.assertEqual(report['retrieval_pages'], 3)
        self.assertEqual(self.pipeline.summary()['retrieval_pages'], 3)

    def test_one_literal_repair_reuses_evidence_and_accounts_for_usage(self):
        original = self.pipeline.client
        answer_calls = []
        def client(messages, json_schema=None, provider=None):
            response = original(messages, json_schema, provider)
            if json_schema['name'] == 'unit_answer':
                answer_calls.append(deepcopy(messages))
                if len(answer_calls) == 1:
                    raw = json.loads(response['text'])
                    raw['items'][0]['value'] = '999'
                    response['text'] = json.dumps(raw)
            return response
        self.pipeline.client = client
        with patch('rag.answer.retrieve', wraps=answer.retrieve) as retrieve:
            report = self.pipeline.ask('What is Group?')
        result = report['results'][0]
        self.assertEqual(len(answer_calls), 2)
        retrieve.assert_called_once()
        self.assertEqual(answer_calls[1][:len(answer_calls[0])], answer_calls[0])
        self.assertEqual(result['items'][0]['value'], 'Group')
        self.assertNotIn('999', result['answer_ko'])
        self.assertTrue(result['validation']['repair']['accepted'])
        self.assertEqual(result['prompt_tokens'], 20)
        self.assertEqual(result['completion_tokens'], 10)

    def test_repeated_invalid_values_stop_after_one_repair(self):
        original = self.pipeline.client
        def client(messages, json_schema=None, provider=None):
            response = original(messages, json_schema, provider)
            if json_schema['name'] == 'unit_answer':
                raw = json.loads(response['text']); raw['items'][0]['value'] = '999'
                response['text'] = json.dumps(raw)
            return response
        self.pipeline.client = client
        report = self.pipeline.ask('What is Group?')
        result = report['results'][0]
        self.assertEqual(self.calls.count('unit_answer'), 2)
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(result['items'], [])
        self.assertFalse(result['validation']['repair']['accepted'])
        self.assertNotIn('999', result['answer_ko'])

    def test_invalid_condition_is_repaired_even_when_the_value_is_literal(self):
        original=self.pipeline.client
        answers=[]
        def client(messages,json_schema=None,provider=None):
            response=original(messages,json_schema,provider)
            if json_schema['name']=='unit_answer':
                answers.append(messages)
                raw=json.loads(response['text'])
                raw['items'][0]['conditions']=([{'dimension':'study','quote':'ST_INTERNAL_KEY','evidence':'E1'}]
                    if len(answers)==1 else [])
                response['text']=json.dumps(raw)
            return response
        self.pipeline.client=client
        result=self.pipeline.ask('What is Group?')['results'][0]
        self.assertEqual(len(answers),2)
        self.assertTrue(result['validation']['repair']['accepted'])
        self.assertNotIn('ST_INTERNAL_KEY',result['answer_ko'])

    def test_api_queries_reuse_encoder_but_local_queries_release_it(self):
        created=[]
        original=self.pipeline.encoder_factory
        def factory():
            created.append(1)
            return original()
        self.pipeline.encoder_factory=factory
        self.pipeline.client.provider='openai'
        self.pipeline.ask('What is Group?');self.pipeline.ask('What is Group?')
        self.assertEqual(len(created),1)
        self.pipeline.close()
        self.assertIsNone(self.pipeline._shared_encoder)
        self.pipeline.client.provider='ollama'
        self.pipeline.ask('What is Group?');self.pipeline.ask('What is Group?')
        self.assertEqual(len(created),3)

    def test_default_exclusion_can_be_explicitly_included_but_evaluation_cannot(self):
        for chunk in self.chunks:chunk.setdefault('document_metadata',{})['default_search']=False
        report=self.pipeline.ask('What is Group?')
        self.assertFalse(report['results']);self.assertEqual(self.calls,['query_understanding'])
        self.calls.clear()
        report=self.pipeline.ask('What is Group?',include_nondefault=True)
        self.assertTrue(report['results'][0]['items'])
        for chunk in self.chunks:chunk['document_metadata']['evaluation_cohort']='final_test'
        self.calls.clear()
        report=self.pipeline.ask('What is Group?',include_nondefault=True)
        self.assertFalse(report['results']);self.assertEqual(self.calls,['query_understanding'])

    def test_invalid_scope_fails_before_any_model_call(self):
        for payload in [dict(question=''),dict(question='q',documents=['d99']),dict(question='q',products='SB17'),dict(question='q',include_nondefault='yes'),
                        dict(question='PMDA 심사보고서에서는?',include_nondefault=True),
                        dict(question='일본 RA 자료에서는?'),dict(question='q',jurisdictions=['JP']),
                        dict(question='q',jurisdictions=['sponsor'])]:
            with self.assertRaises(ValueError):self.pipeline.ask(**payload)
        self.assertEqual(self.calls,[])

    def test_agency_scope_supports_pmda_and_sponsor_without_country_inference(self):
        for question,jurisdiction in [('PMDA 문서에서는?','JP'),('ClinicalTrials.gov 프로토콜에서는?','sponsor')]:
            raw={'intent':'fact_lookup','products':['SB17'],'studies':[],'study_scope':'unspecified','study_mentions':[],
                'jurisdictions':[jurisdiction],'jurisdiction_mentions':[{'jurisdiction':jurisdiction,'role':'regulator','quote':question}],
                'doc_groups':['assessment'],'topic':'design'}
            understood=resolve(question,raw)
            self.assertEqual(plan(understood)[0]['jurisdiction'],jurisdiction)

    def test_dynamic_unspecified_scope_covers_available_products_and_sources(self):
        u={'intent':'fact_lookup','products':[],'studies':[],'jurisdictions':[],'doc_groups':[],'topic':'other'}
        units=plan(u,agencies=['EU','US','JP','CA','sponsor'],products=['SB17'])
        self.assertEqual(len(units),5)
        self.assertEqual({unit['jurisdiction'] for unit in units},{'EU','US','JP','CA','sponsor'})


if __name__=='__main__':unittest.main()
