import unittest

from rag import ra_review, ra_source_constraints
from test_ra_bound_review import proposal, review, clause, TASK


def sources():
    text = ('If certain clinical and in vivo non-clinical studies are conducted with a non-EEA '
        'authorised comparator, bridging is required. The bridge will always include analytical studies.')
    return {'E1': {'text': text, 'document_id': 'g', 'version_id': 'v', 'product_id': None,
        'jurisdiction': 'EU', 'structure': {}, 'representation': {}, 'document_metadata': {},
        'source_spans': [{'unit_id': 'u', 'representation_start': 0,
                          'representation_end': len(text), 'separator_before': ''}]}}


def part(text, verdict='supported'):
    return {**clause(text=text, ids=('S1',), verdict=verdict), 'products': []}


class ConditionContextTests(unittest.TestCase):
    def test_supported_dependent_condition_retains_reviewed_antecedent(self):
        ev = sources()
        parents = proposal(ev, text='비EEA 대조약을 임상 또는 생체 내 비임상시험에 사용한 경우, 분석 브리징이 필요하다.',
                           ids=('S1',), products=())
        raw = review([part('비EEA 대조약을 임상 또는 생체 내 비임상시험에 사용한 경우, 브리지가 필요하다.'),
                      part('이러한 경우 분석 브리징에는 분석자료가 항상 포함된다.')])
        raw['claims']['C1']['selected_support'] = 'conflicting'
        kept, rejected, _ = ra_review.apply({'claims': raw['claims']}, ev, parents, [TASK], truth_only=True)
        self.assertFalse(rejected)
        self.assertEqual(len(kept), 2)
        self.assertIn('임상 또는 생체 내 비임상시험에 사용한 경우', kept[1]['text'])
        self.assertEqual(kept[1]['resolved_reviewed_condition']['from_claim_id'], 'C1.1')
        self.assertEqual(kept[1]['sources'], kept[0]['sources'])

    def test_unsupported_antecedent_cannot_rescue_anaphoric_clause(self):
        ev = sources()
        parents = proposal(ev, text='브리징을 설명한다.', ids=('S1',), products=())
        raw = review([part('임상시험에 사용한 경우, 브리지가 필요하다.', 'unsupported'),
                      part('이러한 경우 분석 브리징에는 분석자료가 항상 포함된다.')])
        kept, rejected, _ = ra_review.apply({'claims': raw['claims']}, ev, parents, [TASK], truth_only=True)
        self.assertFalse(kept)
        self.assertEqual(len(rejected), 2)

    def test_document_active_substance_title_is_not_efficacy_assertion(self):
        c = {'text': '사용된 문서는 「바이오테크놀로지 유래 단백질을 유효성분으로 함유하는 유사생물의약품 지침: 품질 쟁점」이다.',
             'products': [], 'sources': [{'quote': 'Guideline on similar biological medicinal products containing biotechnology-derived proteins as active substance: quality issues'}]}
        self.assertIsNone(ra_source_constraints.check(c, {}))
        c['text'] = '이 지침은 유효성 입증 요건에 적용된다.'
        self.assertEqual(ra_source_constraints.check(c, {}), 'claim_quality_safety_efficacy_predicate_not_in_selected_sources')

    def test_rejected_intervening_clause_cannot_supply_condition(self):
        ev = sources()
        parents = proposal(ev, text='브리징을 설명한다.', ids=('S1',), products=())
        raw = review([part('비EEA 대조약을 임상 또는 생체 내 비임상시험에 사용한 경우, 브리지가 필요하다.'),
                      part('분석시험에 사용한 경우, 브리지가 필요하다.', 'unsupported'),
                      part('이러한 경우 분석 브리징에는 분석자료가 항상 포함된다.')])
        kept, rejected, _ = ra_review.apply({'claims': raw['claims']}, ev, parents, [TASK], truth_only=True)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(rejected), 2)


if __name__ == '__main__': unittest.main()
