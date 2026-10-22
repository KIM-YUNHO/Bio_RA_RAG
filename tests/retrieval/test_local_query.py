import unittest
from types import SimpleNamespace
import json
from rag.retrieval.local_query import rewrite,validate_rewrite

class Fake:
    settings=SimpleNamespace(model='local-model',num_ctx=8192)
    def __init__(self,replies):self.replies=iter(replies);self.requests=[]
    def fingerprint(self):return {'name':'local-model','digest':'fixed'}
    def request(self,path,body):
        self.requests.append((path,body))
        return {'done':True,'done_reason':'stop','message':{'content':json.dumps({'english_query':next(self.replies)})},
            'prompt_eval_count':20,'eval_count':10}

class LocalQueryTests(unittest.TestCase):
    def test_numbers_identifiers_and_units_must_survive_with_boundaries(self):
        q='ABP654 12주 60kg 결과는?'
        self.assertEqual(validate_rewrite(q,{'english_query':'What are ABP654 outcomes at Week 12 for 60 kg?'}),[])
        self.assertEqual(validate_rewrite(q,{'english_query':'What are ABP654 outcomes at Week 12 for 60kg?'}),[])
        for value in ('ABP654 outcomes at Week 120 for 60 kg','ABP654 outcomes at Week 12 for 60 pounds',
                      'ABP6542 outcomes at Week 12 for 60 kg','ABP654 outcomes at Week 12 for 60 kg and 90 mg'):
            self.assertTrue(validate_rewrite(q,{'english_query':value}))

    def test_valid_translation_is_local_text_only_and_counts_tokens(self):
        backend=Fake(['Where is the YESINTEK 12 week table?'])
        out=rewrite('YESINTEK 12주 표는?',adapter=backend)
        self.assertTrue(out['used_rewrite'])
        self.assertEqual((out['input_tokens'],out['output_tokens']),(20,10))
        path,body=backend.requests[0]
        self.assertEqual(path,'/api/chat');self.assertFalse(body['think'])
        self.assertFalse(any('images' in m for m in body['messages']))

    def test_failed_validation_retries_then_returns_the_original(self):
        backend=Fake(['YESINTEK outcomes at Week 120?','YESINTEK outcomes at Week 90?'])
        question='YESINTEK 12주 결과는?'
        out=rewrite(question,adapter=backend)
        self.assertEqual(out['query'],question)
        self.assertFalse(out['used_rewrite']);self.assertEqual(out['attempted_calls'],2)

    def test_english_query_does_not_contact_model(self):
        out=rewrite('Find the CADMUS Week 12 results',adapter=Fake([]))
        self.assertFalse(out['used_rewrite']);self.assertEqual(out['attempted_calls'],0)

    def test_invalid_schema_and_korean_output_are_rejected(self):
        self.assertTrue(validate_rewrite('표를 찾아줘',None))
        self.assertTrue(validate_rewrite('표를 찾아줘',{'english_query':'표를 찾아줘'}))

if __name__=='__main__':unittest.main()
