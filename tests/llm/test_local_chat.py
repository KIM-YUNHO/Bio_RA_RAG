from types import SimpleNamespace
import unittest
from unittest.mock import patch
from rag.llm.local_chat import LocalChat, GenerationFailure


class LocalGeneration(unittest.TestCase):
    def client(self):
        settings=SimpleNamespace(endpoint='http://localhost:11434',timeout_s=5,model='qwen3.5:9b',num_ctx=8192,num_predict=3000,keep_alive='5m')
        return LocalChat(SimpleNamespace(qwen=settings))

    def test_length_error_preserves_usage_finish_reason_and_partial_text(self):
        client=self.client()
        with patch.object(client.adapter,'request',return_value={'done':True,'done_reason':'length',
            'message':{'content':'{"items":['},'prompt_eval_count':4300,'eval_count':3000}):
            with self.assertRaises(GenerationFailure) as caught:client([{'role':'user','content':'short'}])
        t=caught.exception.telemetry
        self.assertEqual((t['prompt_tokens'],t['completion_tokens'],t['finish_reason']),(4300,3000,'length'))
        self.assertEqual(t['raw_text'],'{"items":[')

    def test_input_budget_stops_before_generation(self):
        client=self.client()
        with patch.object(client.adapter,'request') as request:
            with self.assertRaises(GenerationFailure):client([{'role':'user','content':'x '*10000}])
            request.assert_not_called()
        self.assertEqual(client.calls[0]['status'],'input_budget_exceeded')


if __name__=='__main__':unittest.main()
