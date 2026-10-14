import unittest
from rag import selection_candidate as s
from tests.test_selection_candidate import evidence

class SourceEventTests(unittest.TestCase):
    def test_diluted_storage_does_not_select_other_formulation(self):
        q='희석 주입액의 실온 온도와 보관 시간 및 주입 완료 기한은?'
        e=evidence('Individual pre-filled syringes may be stored at 25 C for 11 days.\n\nThe diluted infusion solution may be stored at 28 C for 2 hours. The infusion should be completed within 3 hours.')
        c=s.catalog(e,q)
        self.assertFalse(any(x.get('value_eligible',True) for x in c.values() if '11 days' in x['value']))
        self.assertTrue(any(x.get('value_eligible',True) and 'diluted infusion solution' in x['value'] for x in c.values()))
        self.assertFalse(any(x.get('value_eligible',True) and x['kind']=='literal_number' for x in c.values()))

    def test_stability_request_keeps_its_native_complete_statement(self):
        q='희석 용액의 화학적 물리적 안정성 온도와 기간은?'
        e=evidence('Chemical and physical in-use stability of the diluted infusion solution has been demonstrated for 12 days at 2 C followed by 18 hours at 27 C.')
        c=s.catalog(e,q)
        self.assertTrue(any(x.get('value_eligible',True) and '12 days' in x['value'] and '18 hours' in x['value'] for x in c.values()))

    def test_contraindication_is_not_its_warning_cross_reference(self):
        q='제품설명서에서 금기로 명시한 대상은?'
        e=evidence('The product is contraindicated in patients with hypersensitivity.\n\nSerious hypersensitivity reactions have occurred. See Contraindications (4).')
        for block in e.values():block['document_metadata']={'family':'product_information'}
        c=s.catalog(e,q)
        self.assertTrue(any(x.get('value_eligible',True) and 'contraindicated' in x['value'] for x in c.values()))
        self.assertFalse(any(x.get('value_eligible',True) and 'have occurred' in x['value'] for x in c.values()))

    def test_gap_or_missing_formulation_never_becomes_confirmed(self):
        from rag.source_event_scope import audit,requested
        q='희석액의 주입 완료 기한은?';e=evidence('The solution should be administered within 3 hours.')
        c=s.catalog(e,q)
        self.assertTrue(all(x['requested_source_event']['status']=='unknown' for x in c.values()))
        e=evidence('The diluted infusion solution.\n[UNSUPPLIED SOURCE GAP]\nStore for 3 hours.')
        c=s.catalog(e,q)
        self.assertFalse(any(x.get('value_eligible',True) and '3 hours' in x['value'] and x['requested_source_event']['status']=='match' for x in c.values()))

    def test_diluted_deadline_is_a_separate_obligation_from_storage(self):
        from rag.requested_facts import slots
        q='희석 주입액의 실온 최대 온도, 보관 시간과 주입 완료 기한은?'
        e=evidence('The diluted infusion solution may be stored at room temperature up to 28 °C for 2 hours. The infusion should be completed within 3 hours.')
        c=s.catalog(e,q);required=slots(c,e,q)
        self.assertEqual({'storage_temperature','storage_period','storage_infusion_deadline'},{r['name'] for r in required})
        self.assertTrue(all(r['value_ids'] for r in required))

    def test_retrieval_cues_prefer_printed_predicates_over_contents(self):
        from rag.source_event_scope import cues
        self.assertFalse(cues('금기로 명시한 대상',{'text':'CONTENTS\n4 CONTRAINDICATIONS\n5 WARNINGS'}))
        self.assertTrue(cues('금기로 명시한 대상',{'text':'The product is contraindicated in patients with hypersensitivity.'}))
        self.assertFalse(cues('희석액의 화학적 안정성 보관 온도',{'text':'Individual pre-filled syringes may be stored at room temperature.'}))
