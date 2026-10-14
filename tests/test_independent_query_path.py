from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parent))
from rag import contract_query, selection_candidate as selector
from rag.answer_conditions import missing, explicit_conflicts
from rag.contract_match import match_request
from rag.contract_terms import atoms
from rag.request_contract import ground
from rag.request_coverage import classify
from tests.test_request_contract import term, cond
from tests.test_selection_candidate import evidence


def obligations(question):
    return ground(question, {'requests': [
        {'id': 'result', 'field': term('statistic', 'GMR'),
         'conditions': [cond('endpoint', 'Cmax'), cond('analysis_role', 'primary', '주 분석'),
                        cond('timepoint', 'Week 8')]},
        {'id': 'criterion', 'field': term('attribute', 'margin', '동등성 한계'), 'conditions': []}],
        'non_request_mentions': []})


class IndependentFieldTests(unittest.TestCase):
    def test_analysis_and_visit_apply_only_to_the_requested_result(self):
        question = 'Week 8 Cmax GMR의 주 분석 결과와 동등성 한계는?'
        req = obligations(question)
        source = evidence('Table 4. Cmax results at Week 8\n'
                          '<table><tr><th>Analysis</th><th>Endpoint</th><th>GMR</th></tr>'
                          '<tr><td>Primary Analysis</td><td>Cmax</td><td>1.07</td></tr></table>\n\n'
                          'The equivalence margin was 80 to 125%.')
        u = {'request_contract': req, 'studies': [], 'topic': 'pk_results'}
        candidates = selector.catalog_for_query(source, question, u)
        result = next(c for c in candidates.values() if c['kind'] == 'table_cell' and c['value'] == '1.07')
        margin = next(c for c in candidates.values() if c['kind'] == 'source_paragraph' and c.get('printed_margin_predicate'))
        self.assertNotEqual(result.get('value_eligible'), False)
        self.assertNotEqual(margin.get('value_eligible'), False)
        self.assertIn('result', [c['request_id'] for c in classify(result['independent_request_audit']['checks'])['supported']])
        margin_checks = margin['independent_request_audit']['checks']
        criterion = next(c for c in margin_checks if c['request_id'] == 'criterion')
        self.assertEqual(criterion['status'], 'match')
        self.assertEqual([c['dimension'] for c in criterion['checks']], ['attribute'])
        items = [{'source_selection': {'value_id': c['id']}, 'conditions': [], 'exact': True,
                  'source_label': 'Source', 'value': c['value'], 'quote': c['quote'], 'evidence': ['E1'],
                  'independent_request_audit': c['independent_request_audit']} for c in (result, margin)]
        self.assertEqual(missing(items, question, u), [])
        out = {'items': items, 'status': 'answered', 'answer_ko': '', 'validation': {}}
        selector._finalize_independent_contract(out, candidates, source, question, u)
        self.assertEqual(out['validation']['independent_question_coverage']['missing'], [])
        self.assertEqual(out['validation']['independent_question_coverage']['source_support']['criterion'], [margin['id']])

    def test_wrong_visit_remains_unfulfilled_and_does_not_remove_independent_criterion(self):
        question = 'Week 8 Cmax GMR의 주 분석 결과와 동등성 한계는?'
        req = obligations(question)
        fact = {'relationship_status': 'structural_candidate', 'value_binding_status': 'whole_cell',
                'observations': atoms('Primary Analysis Cmax GMR Week 9'), 'explicit_relations': []}
        check = match_request(req, req['requests'][0], fact)
        self.assertEqual(check['status'], 'mismatch')
        self.assertTrue(any(c['dimension'] == 'timepoint' and c['status'] == 'mismatch' for c in check['checks']))
        criterion_fact = {**fact, 'observations': atoms('Equivalence margin 80 to 125%')}
        self.assertEqual(match_request(req, req['requests'][1], criterion_fact)['status'], 'match')

    def test_contract_for_another_question_is_rejected(self):
        question = 'Week 8 Cmax GMR의 주 분석 결과와 동등성 한계는?'
        with self.assertRaises(ValueError):
            selector.catalog_for_query({}, 'Another question', {'request_contract': obligations(question)})

    def test_unquantified_criterion_or_single_ci_bound_cannot_complete_a_field(self):
        from rag.contract_relations import prose_fact, table_fact
        q='동등성 한계'
        req=ground(q, {'requests':[{'id':'criterion','field':term('attribute','margin',q),'conditions':[]}],
                       'non_request_mentions':[]})
        e=evidence('The equivalence margin will be determined in a later analysis.')
        c=next(c for c in selector.catalog(e,q).values() if c['kind']=='source_paragraph')
        self.assertEqual(match_request(req,req['requests'][0],prose_fact(c,e))['status'],'unknown')
        e=evidence('<table><tr><th>Difference 95% CI</th></tr><tr><td>(-2.7, 4.8)</td></tr><tr><td>-2.7</td></tr></table>')
        c=selector.catalog(e,'95% CI')
        from rag.contract_match import condition
        for value,expected in [('(-2.7, 4.8)','match'),('-2.7','unknown')]:
            cell=next(x for x in c.values() if x['kind']=='table_cell' and x['value']==value)
            self.assertEqual(condition(table_fact(cell,e),'statistic','eq',['CI95'])['status'],expected)
        # A positively identified point estimate is a different statistic,
        # not an unfinished confidence interval merely because it is scalar.
        e=evidence('<table><tr><th>GMR</th></tr><tr><td>1.07</td></tr></table>')
        cell=next(x for x in selector.catalog(e,'GMR').values() if x['kind']=='table_cell' and x['value']=='1.07')
        self.assertEqual(condition(table_fact(cell,e),'statistic','eq',['CI90'])['status'],'mismatch')

    def test_new_vocabulary_does_not_expand_the_legacy_pk_shortcut(self):
        from rag.literal_requests import compile_requests,automatic_roles_complete
        self.assertIsNone(compile_requests('Week 8 PASI 군간 차이, 90% CI와 유사성 한계'))
        self.assertFalse(automatic_roles_complete('Cmax GMR Day 29'))
        self.assertFalse(any(a['dimension']=='analysis_role' for a in atoms('주 분석군')))

    def test_tex_margin_is_a_quantified_predicate_without_changing_source_text(self):
        from rag.margin_scope import predicate
        self.assertTrue(predicate(r'The similarity margin recommended was $ \pm 13\% $.'))

    def test_question_inventory_resolves_study_identity_without_outcome_facts(self):
        from rag.question_anchors import catalog
        entries=catalog('CT-P43 1.1 Part 2 Cmax GMR')
        studies=[a for a in entries.values() if a['dimension']=='study']
        self.assertEqual({a['value'] for a in studies},{'ST_CTP43_1_1_P2'})
        self.assertTrue(all('Cmax' not in a['quote'] for a in studies))

    def test_unknown_literal_field_is_retained_and_cannot_be_answered_as_complete(self):
        q = '새로운 측정값은?'
        req = ground(q, {'requests': [{'id': 'unknown', 'field': term('literal_request', '새로운 측정값'),
                                      'conditions': []}], 'non_request_mentions': []})
        source = evidence('A complete printed observation is 7 units.')
        u = {'request_contract': req, 'studies': [], 'topic': 'other'}
        candidates = selector.catalog_for_query(source, q, u)
        c = next(c for c in candidates.values() if c['kind'] == 'source_paragraph')
        out = {'items': [{'source_selection': {'value_id': c['id']}, 'exact': True, 'conditions': [],
                         'value': c['value'], 'source_label': 'Source', 'evidence': ['E1']}],
               'status': 'answered', 'validation': {}, 'answer_ko': ''}
        selector._finalize_independent_contract(out, candidates, source, q, u)
        self.assertEqual(out['status'], 'partial')
        self.assertFalse(out['items'][0]['exact'])
        self.assertEqual(out['validation']['independent_question_coverage']['missing'], ['unknown'])


class IndependentPipelineTests(unittest.TestCase):
    def setUp(self):
        from test_rag_pipeline import PipelineTests
        self.fixture = PipelineTests('test_original_understanding_retrieval_and_answer_stages_are_used')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_one_query_call_passes_original_contract_through_scope_and_generation(self):
        pipe = self.fixture.pipeline
        pipe.query_strategy = contract_query
        pipe.selection_strategy = selector
        calls = []
        recorded = []
        def client(messages, json_schema=None, provider=None):
            calls.append(json_schema['name'])
            recorded.append(deepcopy(messages))
            if json_schema['name'] == 'query_understanding_with_anchor_ids':
                value = {'understanding': {'intent': 'fact_lookup', 'products': [], 'studies': [],
                         'study_scope': 'unspecified', 'study_mentions': [], 'jurisdictions': [],
                         'jurisdiction_mentions': [], 'doc_groups': [], 'topic': 'other',
                         'english_query': 'What is Group?', 'keywords': ['Group']},
                         'requirements': {'request_groups': [{'id': 'r', 'field_ids': [],
                         'literal_fields': [{'quote': 'Group', 'occurrence': 0}], 'conditions': [],
                         'literal_conditions': [], 'axes': []}], 'non_request_mentions': []}}
            else:
                cid = json_schema['schema']['properties']['items']['items']['properties']['value_id']['enum'][0]
                value = {'status': 'answered', 'premise_source_ids': [], 'items': [{
                    'study': 'none', 'item': 'source', 'source_label': 'Source', 'value_id': cid,
                    'exact': True, 'conditions': []}]}
            return {'text': json.dumps(value), 'model_reported': 'offline-fixture',
                    'prompt_tokens': 10, 'completion_tokens': 5}
        client.source_selection = True
        pipe.client = client
        report = pipe.ask('What is Group?')
        self.assertEqual(calls, ['query_understanding_with_anchor_ids'])
        req = report['understanding']['request_contract']
        self.assertEqual(req['question'], 'What is Group?')
        self.assertEqual(req['requests'][0]['field']['anchor']['quote'], 'Group')
        self.assertEqual(report['results'][0]['status'], 'not_found')
        self.assertIn('independent_question_coverage', report['results'][0]['validation'])
        self.assertNotIn('Evidence:', recorded[0][1]['content'])
        self.assertEqual(report['understanding']['attempts'], 1)

    def test_query_contract_requires_a_compatible_selection_strategy(self):
        from rag.pipeline import RAGPipeline
        with self.assertRaises(ValueError):
            RAGPipeline(bundle=self.fixture.bundle, chat_client=self.fixture.pipeline.client,
                        query_strategy=contract_query)


if __name__ == '__main__':
    unittest.main()
