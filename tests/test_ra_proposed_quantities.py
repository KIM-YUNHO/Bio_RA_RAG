from decimal import Decimal
import unittest
from rag.ra_quantities import proposed_quantities, quantities


class ProposedQuantityTests(unittest.TestCase):
    def test_mean_sd_shared_unit(self):
        self.assertEqual(proposed_quantities('Arm A 17±3 ng/mL'),
                         {(Decimal(17), 'ng/mL'), (Decimal(3), 'ng/mL')})

    def test_explicit_conjunction_and_thousands(self):
        self.assertEqual(proposed_quantities('평균과 SD는 1,234 및 567 ng·h/mL'),
                         {(Decimal(1234), 'ng·h/mL'), (Decimal(567), 'ng·h/mL')})

    def test_unit_does_not_spread_across_prose_or_different_units(self):
        for text in ('Arm 17: SD 3 ng/mL', '17, SD 3 ng/mL', '17 mg 및 3 ng/mL'):
            self.assertNotIn((Decimal(17), 'ng/mL'), proposed_quantities(text))

    def test_new_numbers_and_units_remain_absent(self):
        got = proposed_quantities('17±3 ng/mL')
        self.assertNotIn((Decimal(18), 'ng/mL'), got)
        self.assertNotIn((Decimal(17), 'mg'), got)

    def test_source_quantity_parser_is_unchanged(self):
        self.assertEqual(quantities('17±3 ng/mL'), {(Decimal(3), 'ng/mL')})


if __name__ == '__main__':
    unittest.main()
