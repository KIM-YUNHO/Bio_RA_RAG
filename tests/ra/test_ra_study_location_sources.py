import unittest

from rag.ra.ra_source_constraints import check


class StudyLocationTests(unittest.TestCase):
    def claim(self, text, abp, sb='The study was a 3-arm study conducted entirely within the EU in France.'):
        return ({'text': text, 'products': ['ABP654','SB17'], 'sources': [
            {'evidence':'E1','quote':abp}, {'evidence':'E2','quote':sb}]},
            {'E1':{'product_id':'ABP654'},'E2':{'product_id':'SB17'}})

    def test_other_product_arm_count_cannot_prove_this_product_site_count(self):
        c, ev = self.claim('ABP654 시험은 미국 3개 기관에서 수행되었고 SB17 시험은 프랑스에서 수행되었다.',
                          'Each subject received a single 90 mg SC injection of ABP654, ustekinumab (EU), or ustekinumab (US).')
        self.assertEqual(check(c,ev),'study_site_count_not_in_own_product_sources')

    def test_comparator_origin_cannot_prove_study_location(self):
        c, ev = self.claim('ABP654 시험은 미국에서 수행되었고 SB17 시험은 프랑스에서 수행되었다.',
                          'The comparison included ustekinumab (US).')
        self.assertEqual(check(c,ev),'study_location_not_in_own_product_conduct_sources')

    def test_correct_own_site_and_conduct_sources_are_kept(self):
        c, ev = self.claim('ABP654 시험은 미국 3개 기관에서 수행되었고 SB17 시험은 프랑스에서 수행되었다.',
                          'The study was conducted at three centres in the US.')
        self.assertIsNone(check(c,ev))

    def test_comparator_origin_statement_is_not_a_study_location(self):
        c = {'text':'ABP654 시험의 대조약은 미국 제품과 유럽 제품이다.', 'products':['ABP654'],
             'sources':[{'quote':'Comparators were ustekinumab (US) and ustekinumab (EU).'}]}
        self.assertIsNone(check(c,{}))

    def test_comparator_origin_during_conduct_is_not_a_country_assertion(self):
        c = {'text':'ABP654 시험은 미국 제품을 대조약으로 이용해 수행했다.', 'products':['ABP654'],
             'sources':[{'quote':'The comparison included ustekinumab (US).'}]}
        self.assertIsNone(check(c,{}))


if __name__ == '__main__':unittest.main()
