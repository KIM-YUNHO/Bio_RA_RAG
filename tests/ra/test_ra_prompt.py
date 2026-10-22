import os
import unittest
from unittest.mock import patch

from rag.ra import ra_answer, ra_prompt


def evidence(text='Paragraph.', product='P1', jurisdiction='EU'):
    return {'E1': {'text': text, 'version_id': 'v', 'document_id': 'd', 'product_id': product,
                   'jurisdiction': jurisdiction, 'chunk_id': 'c', 'pdf_page': 1}}


ENUM = {'products': ['A', 'B', 'C'], 'jurisdictions': ['EU', 'US', 'CA'],
        'tasks': [{'id': 'T1', 'request': 'r', 'source_roles': ['assessment'], 'intent': {'intent': 'fact_lookup'}}]}
SINGLE = {'products': ['A'], 'jurisdictions': ['EU'],
          'tasks': [{'id': 'T1', 'request': 'r', 'source_roles': ['assessment'], 'intent': {'intent': 'fact_lookup'}}]}


class PromptVariantTests(unittest.TestCase):
    def test_default_is_modular_and_current_is_a_rollback(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('RA_PROMPT_VARIANT', None)
            system = ra_answer.messages('질문?', SINGLE, evidence(), 'spans')[0]['content']
        self.assertTrue(system.startswith(ra_prompt.CORE[:40]))
        with patch.dict(os.environ, {'RA_PROMPT_VARIANT': 'current'}):
            current = ra_answer.messages('질문?', SINGLE, evidence(), 'spans')[0]['content']
        self.assertIn('Return at most 8 concise', current)
        with patch.dict(os.environ, {'RA_PROMPT_VARIANT': 'facts'}):
            with self.assertRaises(ValueError):
                ra_answer.messages('질문?', SINGLE, evidence(), 'spans')

    def test_modular_selects_modules_and_puts_enumeration_last(self):
        q = '모든 바이오시밀러의 허가 날짜를 정리하고 제출부터 허가까지 걸린 기간은?'
        with patch.dict(os.environ, {'RA_PROMPT_VARIANT': 'modular'}):
            system = ra_answer.messages(q, ENUM, evidence(), 'spans')[0]['content']
        self.assertTrue(system.startswith(ra_prompt.CORE[:40]))
        self.assertIn(ra_prompt.DERIVED[:30], system)
        self.assertTrue(system.rstrip().endswith('Do not stop after a few units.'))
        self.assertNotIn(ra_prompt.TABLE[:30], system)
        self.assertNotIn(ra_prompt.GUIDELINE[:30], system)
        self.assertNotIn('Return at most', system)

    def test_modular_table_question_gets_table_module(self):
        q = 'PK 요약표에서 Cmax 평균과 표준편차를 알려줘.'
        with patch.dict(os.environ, {'RA_PROMPT_VARIANT': 'modular'}):
            system = ra_answer.messages(q, SINGLE, evidence('<table><tr><td>Mean</td></tr></table>'), 'spans')[0]['content']
        self.assertIn(ra_prompt.TABLE[:30], system)
        self.assertNotIn(ra_prompt.ENUMERATION[:30], system)


class RequirementPathVariantTests(unittest.TestCase):
    def setUp(self):
        # Requirement slots are the rollback mode (RA_REQUIREMENT_MODE=slots).
        from unittest.mock import patch
        import os
        patcher = patch.dict(os.environ, {'RA_REQUIREMENT_MODE': 'slots'})
        patcher.start(); self.addCleanup(patcher.stop)

    def test_modular_replaces_requirement_prompt_with_core_modules_and_contract_last(self):
        from rag.ra.ra_obligations import build
        q = 'EMA SB17-1001 건강인 PK 시험에서 실제 사용한 용량과 투여 경로, 대상군과 비교약을 정리해줘.'
        if not build(q):
            self.skipTest('question no longer requirement-decomposed')
        with patch.dict(os.environ, {'RA_PROMPT_VARIANT': 'modular'}):
            system = ra_answer.messages(q, SINGLE, evidence(), 'spans')[0]['content']
        self.assertTrue(system.startswith(ra_prompt.CORE[:40]))
        self.assertTrue(system.rstrip().endswith('Do not fill a requirement\nwith background.'))
        self.assertIn(ra_prompt.STUDY[:30], system)
        with patch.dict(os.environ, {'RA_PROMPT_VARIANT': 'current'}):
            current = ra_answer.messages(q, SINGLE, evidence(), 'spans')[0]['content']
        self.assertTrue(current.startswith('Write concise KOREAN claims'))
