import unittest
from rag.contract_terms import atoms


class ContractTermAtomsTests(unittest.TestCase):
    def test_spelled_arm_counts_are_source_roles_without_special_document_rules(self):
        for value in ('five-arm','two arm','18-arm','three-arm'):
            self.assertIn(('attribute','arms'),{(a['dimension'],a['value']) for a in atoms(value)})

if __name__ == '__main__':
    unittest.main()
