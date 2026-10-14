import unittest
from rag import selection_candidate as selector
from rag.request_contract import ground
from tests.test_request_contract import term,cond
from tests.test_selection_candidate import evidence

class RequestSourceMapTests(unittest.TestCase):
    def fixture(self):
        q='Week 8 Cmax GMR 주 분석과 동등성 한계'
        req=ground(q,{'requests':[
            {'id':'result','field':term('statistic','GMR'),'conditions':[cond('endpoint','Cmax'),cond('timepoint','Week 8'),cond('analysis_role','primary','주 분석')]},
            {'id':'criterion','field':term('attribute','margin','동등성 한계'),'conditions':[]}],'non_request_mentions':[]})
        e=evidence('Table 2. Cmax results at Week 8\n<table><tr><th>Analysis</th><th>Endpoint</th><th>GMR</th></tr><tr><td>Primary Analysis</td><td>Cmax</td><td>1.13</td></tr></table>\n\nThe equivalence margin was 83 to 126%.\n\nCmax GMR was 1.13.')
        u={'request_contract':req,'studies':[],'topic':'pk_results'}
        return q,e,u,selector.catalog_for_query(e,q,u)

    def test_every_request_is_a_required_unique_provider_property(self):
        q,e,u,c=self.fixture();s=selector.schema(c)
        self.assertEqual(s['name'],'source_selection_by_request')
        props=s['schema']['properties']['request_sources']
        self.assertEqual(set(props['required']),{'result','criterion'})
        self.assertFalse(props['additionalProperties'])
        with self.assertRaises(ValueError):selector.resolve({'request_sources':{'result':selector.UNCONFIRMED}},c,e,q,u)

    def test_fully_connected_source_takes_precedence_over_partial_scope(self):
        q,e,u,c=self.fixture();options=selector.request_options(c)
        related=next(x['id'] for x in c.values() if x['kind']=='source_paragraph' and x['value'].strip()=='Cmax GMR was 1.13.')
        self.assertNotIn(related,options['result'])
        self.assertGreater(len(options['result']),1)
        self.assertIn(selector.UNCONFIRMED,options['result'])

    def test_selection_cannot_omit_the_independent_criterion(self):
        q,e,u,c=self.fixture();options=selector.request_options(c)
        selected={'result':next(cid for cid in options['result'] if cid!=selector.UNCONFIRMED),
                  'criterion':next(cid for cid in options['criterion'] if cid!=selector.UNCONFIRMED)}
        out=selector.resolve({'request_sources':selected},c,e,q,u)
        out['validation']={};selector._finalize_independent_contract(out,c,e,q,u)
        self.assertEqual(out['status'],'answered')
        self.assertEqual(out['validation']['independent_question_coverage']['missing'],[])
        self.assertEqual(len(out['items']),2)
        for item in out['items']:
            self.assertEqual(item['value'],c[item['source_selection']['value_id']]['value'])

    def test_one_whole_proposition_can_supply_two_fields_without_duplication(self):
        q='군간 차이와 90% CI'
        req=ground(q,{'requests':[{'id':rid,'field':term('statistic',value,quote),'conditions':[]}
            for rid,value,quote in [('d','difference','군간 차이'),('ci','CI90','90% CI')]],'non_request_mentions':[]})
        e=evidence('The estimated difference was -2.10% (90% CI: -3.40%, -0.80%).')
        u={'request_contract':req,'studies':[],'topic':'efficacy'};c=selector.catalog_for_query(e,q,u)
        options=selector.request_options(c);cid=next(i for i in options['d'] if i!=selector.UNCONFIRMED)
        self.assertIn(cid,options['ci'])
        out=selector.resolve({'request_sources':{'d':cid,'ci':cid}},c,e,q,u)
        self.assertEqual(len(out['items']),1)
        self.assertEqual(out['request_source_assignments']['ci']['source_id'],cid)

    def test_literal_wording_requires_the_requested_physical_scope(self):
        q='체중 42 kg인 환자의 프리필드 펜 사용 원문'
        req=ground(q,{'requests':[{'id':'r','field':term('literal_request','프리필드 펜 사용 원문'),'conditions':[]}],'non_request_mentions':[]})
        e=evidence('The recommended dose is 45 mg every 12 weeks.')
        u={'request_contract':req,'studies':[],'topic':'other'};c=selector.catalog_for_query(e,q,u)
        self.assertEqual(selector.value_ids(c),[])
        self.assertTrue(any(x.get('selection_exclusion')=='requested_asserted_quote_physical_scope_unresolved' for x in c.values()))

if __name__=='__main__':unittest.main()
