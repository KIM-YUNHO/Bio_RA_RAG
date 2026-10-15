"""Framework model binding and API compatibility; fake transport, no provider calls."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from rag.openai_chat import EvaluationAPIChat, OpenAIPipelineChat
from rag.ra_pipeline import RAPipeline
import test_ra_research


class Tokenizer:
    basis = 'test-counter'
    def one(self, text): return len(text) // 4 + 1
    def messages(self, values): return sum(self.one(m['content']) for m in values)


class Transport:
    def __init__(self):
        self.payloads = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self, **payload):
        self.payloads.append(payload)
        return SimpleNamespace(model=payload['model'], system_fingerprint=None,
            choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='{}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))
    def close(self): pass


class MiniClientTests(unittest.TestCase):
    def test_each_framework_request_has_its_own_three_call_cap_and_mini_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); transport = Transport()
            def factory(folder):
                return EvaluationAPIChat(folder, max_new_calls=3, client=transport,
                    tokenizer=Tokenizer(), ledger_dir=root / 'quota', model='gpt-4o-mini')
            client = OpenAIPipelineChat(root / 'requests', factory, Tokenizer())
            try:
                client.begin_request()
                for n in range(3): client([{'role': 'user', 'content': str(n)}])
                with self.assertRaises(RuntimeError): client([{'role': 'user', 'content': 'fourth'}])
                self.assertEqual(len(transport.payloads), 3)
                self.assertTrue(all(p['model'] == 'gpt-4o-mini' for p in transport.payloads))
                client.release(); client.begin_request()
                client([{'role': 'user', 'content': 'next request'}])
                self.assertEqual(len(transport.payloads), 4)
            finally: client.close()

    def test_wrong_provider_is_rejected_before_request_creation(self):
        client = OpenAIPipelineChat(tokenizer=Tokenizer())
        with self.assertRaises(ValueError): client([], provider='ollama')
        self.assertIsNone(client.session)

    def test_provider_session_cannot_substitute_another_model(self):
        session = SimpleNamespace(model='other', provider='openai', close=lambda: None)
        client = OpenAIPipelineChat(session_factory=lambda folder: session, tokenizer=Tokenizer())
        with self.assertRaises(ValueError): client.begin_request()
        self.assertIsNone(client.session)

    def test_luna_preserves_three_call_cap_and_uses_supported_request_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); transport = Transport()
            def factory(folder):
                return EvaluationAPIChat(folder, max_new_calls=3, client=transport,
                    tokenizer=Tokenizer(), ledger_dir=root / 'quota', model='gpt-5.6-luna')
            client = OpenAIPipelineChat(root / 'requests', factory, Tokenizer(), model='gpt-5.6-luna')
            schema = {'name': 'query_understanding', 'schema': {'type': 'object',
                'properties': {}, 'required': [], 'additionalProperties': False}}
            try:
                client.begin_request()
                for n in range(3):
                    reply = client([{'role': 'user', 'content': str(n)}], json_schema=schema)
                    self.assertEqual(reply['model_reported'], 'gpt-5.6-luna')
                with self.assertRaises(RuntimeError):
                    client([{'role': 'user', 'content': 'fourth'}], json_schema=schema)
                self.assertEqual(len(transport.payloads), 3)
                for payload in transport.payloads:
                    self.assertEqual(payload['model'], 'gpt-5.6-luna')
                    self.assertEqual(payload['reasoning_effort'], 'none')
                    self.assertEqual(payload['max_completion_tokens'], 1600)
                    self.assertTrue(payload['response_format']['json_schema']['strict'])
                    self.assertFalse({'max_tokens', 'temperature', 'seed'} & payload.keys())
                self.assertEqual(client.input_limit, 13128)
            finally: client.close()

    def test_luna_session_cannot_fall_back_to_mini(self):
        session = SimpleNamespace(model='gpt-4o-mini', provider='openai', close=lambda: None)
        client = OpenAIPipelineChat(session_factory=lambda folder: session, tokenizer=Tokenizer(), model='gpt-5.6-luna')
        with self.assertRaisesRegex(ValueError, 'gpt-5.6-luna'): client.begin_request()
        self.assertIsNone(client.session)

    def test_ra_default_is_mini_and_does_not_open_provider_during_initialization(self):
        fixture = test_ra_research.PipelineTests('test_reads_and_synthesizes_instead_of_invoking_numeric_selector')
        fixture.setUp()
        pipeline = None
        try:
            pipeline = RAPipeline(bundle=fixture.bundle, config=fixture.pipeline.config)
            self.assertIsInstance(pipeline.client, OpenAIPipelineChat)
            self.assertEqual(pipeline.summary()['answer_model'], 'gpt-4o-mini')
            self.assertEqual(pipeline.summary()['generation_provider'], 'openai')
            self.assertIsNone(pipeline.client.session)
        finally:
            if pipeline: pipeline.close()
            fixture.tearDown()
