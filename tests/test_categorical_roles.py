import unittest

class CategoricalRoleTests(unittest.TestCase):
    def test_equivalent_comparators_share_coverage_without_losing_inclusivity(self):
        from rag.categorical_roles import weight_category_key
        self.assertEqual(weight_category_key('greater than 60 kg to 90 kg'),weight_category_key('more than 60 kg to 90 kg'))
        self.assertEqual(weight_category_key('>90 kg'),weight_category_key('over 90 kg'))
        self.assertNotEqual(weight_category_key('>90 kg'),weight_category_key('90 kg or more'))
        self.assertNotEqual(weight_category_key('less than 60 kg'),weight_category_key('60 kg or less'))