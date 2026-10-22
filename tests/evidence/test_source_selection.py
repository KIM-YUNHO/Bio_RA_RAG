import unittest
from rag.evidence.source_selection import catalog,resolve,schema,condition_ids,premise_conflicts
from rag.evidence.preprocessed_evidence import grounded_answer
from rag.evidence.answer_conditions import attribution_missing


class SourceSelectionTests(unittest.TestCase):
    def evidence(self):
        return {'E1':{'text':'Study SB17-1001\nThe dose was 45 mg by subcutaneous injection.',
            'product_id':'SB17','structure':{'section_id':'s1','heading_anchors':[]},'source_spans':[]},
            'E2':{'text':'<table><tr><th>Arm</th><th>N</th></tr><tr><td>SB17</td><td>67</td></tr></table>',
            'product_id':'SB17','structure':{'section_id':'s2','heading_anchors':[]},'source_spans':[]}}

    def test_original_table_cell_and_literal_number_are_resolved_by_id(self):
        evidence=self.evidence();candidates=catalog(evidence)
        value=next(c for c in candidates.values() if c['value']=='67')
        selected={'study':'none','item':'count','source_label':'N','value_id':value['id'],'exact':True,'conditions':[]}
        out=resolve({'status':'answered','items':[selected]},candidates,evidence)
        self.assertEqual(out['items'][0]['value'],'67')
        self.assertEqual(out['items'][0]['quote'],'<td>67</td>')
        checked=grounded_answer(out,evidence)
        self.assertEqual(len(checked['items']),1);self.assertFalse(checked['validation']['rejected_items'])
        self.assertTrue(value['context_ids'])
        number=next(c for c in candidates.values() if c['kind']=='literal_number' and c['value']=='45 mg')
        self.assertIn('subcutaneous',number['quote'])

    def test_model_cannot_supply_a_value_or_quotation(self):
        candidates=catalog(self.evidence());item=schema(candidates)['schema']['properties']['items']['items']
        self.assertNotIn('value',item['properties']);self.assertNotIn('quote',item['properties'])
        bad={'study':'none','item':'n','source_label':'N','value_id':'invented','exact':True,'conditions':[]}
        with self.assertRaises(KeyError):resolve({'status':'answered','items':[bad]},candidates,self.evidence())

    def test_study_from_unrelated_section_does_not_ground_a_table_value(self):
        evidence=self.evidence()
        item={'study':'ST_SB17_1001','evidence':['E2'],'conditions':[{'dimension':'study','quote':'SB17-1001','evidence':'E1'}]}
        self.assertEqual(attribution_missing([item],evidence,{'studies':['ST_SB17_1001']},[]),[{'item_index':0,'dimensions':['study']}])
        evidence['E2']['structure']['section_id']='s1'
        self.assertFalse(attribution_missing([item],evidence,{'studies':['ST_SB17_1001']},[]))

    def test_gap_markers_are_excluded_and_merged_headers_keep_source_grid_context(self):
        evidence=self.evidence();evidence['E1']['text']='A [UNSUPPLIED SOURCE GAP] B 55 mg'
        evidence['E2']['text']='<table><tr><th colspan="2">Header</th></tr><tr><td>A</td><td>7</td></tr></table>'
        candidates=catalog(evidence)
        self.assertTrue(all('GAP' not in c['quote'] for c in candidates.values()))
        value=next(c for c in candidates.values() if c['kind']=='table_cell' and c['value']=='7')
        self.assertEqual(value['column'],1)
        self.assertTrue(any(candidates[i]['value']=='Header' for i in value['context_ids']))

    def test_multilevel_dose_and_statistic_headers_follow_logical_colspans(self):
        evidence=self.evidence();evidence['E2']['text']='<table><tr><th>Dose</th><th colspan="2">45 mg</th><th colspan="2">90 mg</th></tr><tr><th>Endpoint</th><th>Median</th><th>Mean</th><th>Median</th><th>Mean</th></tr><tr><td>Cmax</td><td>2.4</td><td>2.7</td><td>5.3</td><td>6.1</td></tr></table>'
        candidates=catalog(evidence);value=next(c for c in candidates.values() if c['kind']=='table_cell' and c['value']=='6.1')
        context=[candidates[i]['value'] for i in value['context_ids']]
        self.assertIn('90 mg',context);self.assertIn('Mean',context);self.assertIn('Cmax',context)
        self.assertNotIn('45 mg',context)

    def test_invalid_span_grid_does_not_assign_header_relationships(self):
        evidence=self.evidence();evidence['E2']['text']='<table><tr><th colspan="9">Bad</th></tr><tr><td>A</td><td>7</td></tr></table>'
        candidates=catalog(evidence)
        self.assertTrue(all(not c.get('context_ids') for c in candidates.values() if c['kind']=='table_cell'))

    def test_gap_splits_paragraph_without_discarding_valid_source_runs(self):
        evidence=self.evidence();evidence['E1']['text']='Adult dose is 45 mg.\n[UNSUPPLIED SOURCE GAP]\nThen every 12 weeks.'
        candidates=catalog(evidence)
        self.assertTrue(any(c['value']=='45 mg' for c in candidates.values()))
        self.assertTrue(any(c['value']=='12 weeks' for c in candidates.values()))
        self.assertTrue(all('GAP' not in c['value'] for c in candidates.values()))
        self.assertTrue(all(not ('Adult' in c['quote'] and 'Then' in c['quote']) for c in candidates.values()))

    def test_literal_number_links_to_parent_cell_without_duplicating_header_context(self):
        evidence=self.evidence();evidence['E2']['text']='<table><tr><th>Endpoint</th><th>Mean</th></tr><tr><td>Cmax</td><td>2.7 (1.2)</td></tr></table>'
        candidates=catalog(evidence)
        number=next(c for c in candidates.values() if c['value']=='2.7')
        self.assertEqual(len(number['context_ids']),1)
        parent=candidates[number['context_ids'][0]]
        self.assertEqual(parent['value'],'2.7 (1.2)')
        self.assertTrue(any(candidates[i]['value']=='Mean' for i in parent['context_ids']))

    def test_study_enum_requires_actual_printed_identifier_and_role_ids_are_constrained(self):
        candidates=catalog(self.evidence());roles=condition_ids(candidates)
        dose=next(c for c in candidates.values() if c['value']=='45 mg')
        self.assertNotIn(dose['id'],roles['study'])
        self.assertIn(dose['id'],roles['dose'])
        selected=schema(candidates)['schema']['properties']['items']['items']
        self.assertEqual(set(selected['properties']['study']['enum']),{'ST_SB17_1001','none'})
        conditions=selected['properties']['conditions']['items']['anyOf']
        study=next(r for r in conditions if r['properties']['dimension']['enum']==['study'])
        self.assertNotIn(dose['id'],study['properties']['source_id']['enum'])

    def test_product_dosage_without_trial_has_only_none_study(self):
        evidence=self.evidence();evidence['E1']['text']='Subcutaneous Adult Dosage: 30 mg every 8 weeks.'
        candidates=catalog(evidence)
        self.assertEqual(schema(candidates)['schema']['properties']['items']['items']['properties']['study']['enum'],['none'])

    def test_unrequested_invented_study_is_still_rejected_by_attribution(self):
        evidence=self.evidence()
        item={'study':'ST_ABP_20190230','evidence':['E2'],
            'conditions':[{'dimension':'study','quote':'Dose','evidence':'E1'}]}
        self.assertEqual(attribution_missing([item],evidence,{'studies':[]},[]),[{'item_index':0,'dimensions':['study']}])

    def test_unrequested_sample_size_is_not_a_mean_value_candidate(self):
        evidence=self.evidence();evidence['E2']['text']='<table><tr><th>Endpoint</th><th>N</th><th>Median</th><th>Mean</th></tr><tr><td>Cmax</td><td>17</td><td>3.1</td><td>3.7</td></tr></table>'
        candidates=catalog(evidence,'Cmax median and mean')
        values=[c['value'] for c in candidates.values()]
        self.assertNotIn('17',values);self.assertIn('3.1',values);self.assertIn('3.7',values)
        self.assertIn('N',values)

    def test_premise_note_requires_connected_printed_trial_and_actual_dose(self):
        evidence=self.evidence();candidates=catalog(evidence)
        dose=next(c for c in candidates.values() if c['value']=='45 mg')
        trial=next(c for c in candidates.values() if c['printed_studies'])
        self.assertTrue(premise_conflicts([dose,trial],evidence,'SB17-1001 90 mg Cmax',{'studies':['ST_SB17_1001'],'english_query':''}))
        self.assertFalse(premise_conflicts([dose],evidence,'SB17-1001 90 mg Cmax',{'studies':['ST_SB17_1001'],'english_query':''}))
