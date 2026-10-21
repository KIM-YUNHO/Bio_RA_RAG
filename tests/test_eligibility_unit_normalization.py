from decimal import Decimal
import unittest

from rag.ra import ra_quantities as q


class EligibilityUnitNormalizationTests(unittest.TestCase):
    def test_pdf_bmi_exponents_and_concatenated_connector(self):
        for text in ('BMI 19.0 - 29.9 kg/m 2 (inclusive)', 'BMI 19.0 to 29.9 kg/m²for adults',
                     'BMI 19.0 to 29.9 kg / m ^{2} for adults'):
            self.assertIn((Decimal('29.9'),'kg/m2'),q.quantities(text))
            self.assertNotIn((Decimal('29.9'),'kg'),q.quantities(text))
        for text in ('29.9 kg/m3','29.9 kg/m 20','29.9 kg/m20'):
            self.assertNotIn((Decimal('29.9'),'kg/m2'),q.quantities(text))

    def test_supplied_eligibility_retains_bmi_without_relaxing_wrong_units(self):
        quote='Weight between 60.0-90.0 kg and BMI between 19.0 - 29.9 kg/m 2 (inclusive).'
        evidence={'E1':{'text':quote,'product_id':'SB17','structure':{}}}
        claim={'text':'체중 60.0~90.0 kg, BMI 19.0~29.9 kg/m²가 선정 기준이다.',
               'products':['SB17'],'sources':[{'evidence':'E1','quote':quote}]}
        self.assertIsNone(q.check(claim,evidence))
        for bad in ('BMI 19.0~29.9 mg', 'BMI 19.0~30.0 kg/m²'):
            claim['text']=bad
            self.assertIsNotNone(q.check(claim,evidence))
        claim['text']='BMI 19.0~29.9 kg/m²'
        claim['sources'][0]['quote']='Weight between 19.0-29.9 kg.'
        self.assertEqual(q.check(claim,evidence),'claim_quantity_value_or_unit_not_in_final_citations')


if __name__=='__main__':unittest.main()
