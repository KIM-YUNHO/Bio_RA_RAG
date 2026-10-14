import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from rag.openai_chat import EvaluationAPIChat
from rag.local_chat import GenerationFailure


class Tokens:
    def one(self,text):return len(text)
    basis='fixture';model_digest='fixture'
    def messages(self,m):return sum(len(x['content']) for x in m)


class API:
    def __init__(self,error=None):
        self.n=0;self.error=error;self.chat=SimpleNamespace(completions=self)
    def create(self,**kwargs):
        self.n+=1;self.payload=kwargs
        if self.error:raise self.error
        return SimpleNamespace(model='gpt-4o-mini-2024-07-18',usage=SimpleNamespace(prompt_tokens=12,completion_tokens=8),
            system_fingerprint='fixture',choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='{"ok":true}'))])


class APIEvaluationTests(unittest.TestCase):
    def setup_client(self,root,api,cap=2):
        c=EvaluationAPIChat(root/'out',cap,client=api,tokenizer=Tokens(),ledger_dir=root/'ledger');c.case='Q001';return c
    def test_real_provider_strict_schema_cache_and_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);api=API();c=self.setup_client(root,api)
            messages=[{'role':'user','content':'Question only'}];schema={'name':'query_understanding','schema':{'type':'object'}}
            first=c(messages,schema);second=c(messages,schema)
            self.assertEqual(api.n,1);self.assertEqual(first['text'],second['text'])
            self.assertTrue(api.payload['response_format']['json_schema']['strict']);self.assertEqual(api.payload['max_tokens'],1000)
            ledger=json.loads(c.ledger_path.read_text());self.assertEqual(len(ledger['attempts']),1)
            self.assertEqual(ledger['attempts'][0]['prompt_tokens'],12);self.assertFalse(c.local_only)
    def test_hard_cap_is_reserved_before_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);api=API();c=self.setup_client(root,api,1)
            c([{'role':'user','content':'first'}])
            with self.assertRaisesRegex(RuntimeError,'cap reached'):c([{'role':'user','content':'second'}])
            self.assertEqual(api.n,1)
    def test_quota_error_is_not_retried_or_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            error=RuntimeError('provider quota');error.status_code=429
            api=API(error);c=self.setup_client(Path(tmp),api)
            with self.assertRaises(GenerationFailure):c([{'role':'user','content':'first'}])
            with self.assertRaises(RuntimeError):c([{'role':'user','content':'second'}])
            self.assertEqual(api.n,1);self.assertEqual(c.halted,'provider_access_or_quota_error')


if __name__=='__main__':unittest.main()
