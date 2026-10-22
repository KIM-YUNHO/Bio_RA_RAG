import unittest

class SourceEventTests(unittest.TestCase):
    def test_retrieval_cues_prefer_printed_predicates_over_contents(self):
        from rag.evidence.source_event_scope import cues
        self.assertFalse(cues('금기로 명시한 대상',{'text':'CONTENTS\n4 CONTRAINDICATIONS\n5 WARNINGS'}))
        self.assertTrue(cues('금기로 명시한 대상',{'text':'The product is contraindicated in patients with hypersensitivity.'}))
        self.assertFalse(cues('희석액의 화학적 안정성 보관 온도',{'text':'Individual pre-filled syringes may be stored at room temperature.'}))
