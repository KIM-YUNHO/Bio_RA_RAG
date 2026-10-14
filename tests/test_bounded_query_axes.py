import unittest
from rag.question_anchors import schema,catalog
from rag.understand_query import SCHEMA
from rag.request_contract import ground
from rag import selection_candidate as selector
from tests.test_request_contract import term
from tests.test_selection_candidate import evidence

class BoundedAxesTests(unittest.TestCase):
    def test_single_conditions_cannot_become_a_heterogeneous_axis(self):
        q='Week 8 Cmax GMR 주 분석과 동등성 한계'
        groups=schema(q,SCHEMA['schema'])['schema']['properties']['requirements']['properties']['request_groups']['items']['anyOf']
        for group in groups:
            p=group['properties']
            self.assertEqual(p['axes']['maxItems'],0)
            self.assertEqual(p['conditions']['maxItems'],3)
            self.assertLessEqual(p['field_ids']['maxItems'],1)

    def test_each_provider_axis_has_one_dimension_and_bounded_option_count(self):
        q='CT-P43 EU·US Stelara 대비 AUCinf, Cmax GMR';anchors=catalog(q)
        groups=schema(q,SCHEMA['schema'])['schema']['properties']['requirements']['properties']['request_groups']['items']['anyOf']
        for group in groups:
            for axis in group['properties']['axes']['items']['anyOf']:
                options=axis['properties']['options'];ids=options['items']['properties']['anchor_ids']['items']['enum']
                self.assertEqual(len({anchors[i]['dimension'] for i in ids}),1)
                self.assertLessEqual(options['maxItems'],3)

    def test_single_unresolved_request_still_rejects_a_different_weight_category(self):
        q='체중 42 kg인 환자의 프리필드 펜 사용 원문'
        req=ground(q,{'requests':[{'id':'r','field':term('literal_request','프리필드 펜 사용 원문'),'conditions':[]}],
                      'non_request_mentions':[]})
        e=evidence('<table><tr><th>Body Weight</th><th>Dosage Form</th></tr><tr><td>&lt; 60 kg</td><td>vial</td></tr><tr><td>≥ 60 kg to ≤ 100 kg</td><td>pre-filled pen</td></tr></table>')
        u={'request_contract':req,'studies':[],'topic':'other'}
        cs=selector.catalog_for_query(e,q,u)
        wrong=next(c for c in cs.values() if c['kind']=='table_cell' and c['value']=='pre-filled pen')
        self.assertFalse(wrong['value_eligible'])
        checks=wrong['independent_request_audit']['checks'][0]['checks']
        self.assertTrue(any(c['dimension']=='weight_point' and c['status']=='mismatch' for c in checks))

    def test_negative_physical_guard_is_not_broadcast_to_an_independent_margin(self):
        q='체중 42 kg인 환자의 프리필드 펜 사용 원문과 동등성 한계'
        req=ground(q,{'requests':[{'id':'r','field':term('literal_request','프리필드 펜 사용 원문'),'conditions':[]},
                      {'id':'m','field':term('attribute','margin','동등성 한계'),'conditions':[]}],'non_request_mentions':[]})
        e=evidence('The equivalence margin was 81 to 124%.')
        u={'request_contract':req,'studies':[],'topic':'other'}
        cs=selector.catalog_for_query(e,q,u)
        c=next(c for c in cs.values() if c['kind']=='source_paragraph' and c.get('printed_margin_predicate'))
        check=next(r for r in c['independent_request_audit']['checks'] if r['request_id']=='m')
        self.assertEqual(check['status'],'match')
        self.assertFalse(any(x['dimension'] in ('formulation','weight_point') for x in check['checks']))

if __name__=='__main__':unittest.main()
