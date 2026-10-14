from copy import deepcopy
import unittest
from rag import printed_pk,selection_candidate


def evidence(html):return {'E1':{'text':html,'document_id':'synthetic','version_id':'one','product_id':'SB17'}}
TABLE='<table><tr><th>Parameter</th><th>Arm A</th><th>Arm B</th></tr><tr><td>Tmax (h)</td><td></td><td></td></tr><tr><td>Median</td><td>171</td><td>171</td></tr><tr><td>Min</td><td>11</td><td>22</td></tr><tr><td>Max</td><td>700</td><td>800</td></tr><tr><td>CL/F (L/day)</td><td></td><td></td></tr><tr><td>Mean (SD)</td><td>0.61 (0.13)</td><td>0.72 (0.17)</td></tr></table>'


class PrintedPKTests(unittest.TestCase):
    def test_new_values_closed_groups_all_arms_equal_values_are_distinct(self):
        e=evidence(TABLE);q='군별 Tmax 중앙값, 최소값, 최대값과 단위는?'
        c=selection_candidate.catalog(e,q)
        result=printed_pk.unique_selections(c,e,q,{})
        self.assertEqual(6,len(result['items']))
        values=[c[i['value_id']]['value'] for i in result['items']]
        self.assertCountEqual(['171','171','11','22','700','800'],values)
        self.assertEqual(6,result['_proof']['slots'])
        self.assertNotIn('0.61 (0.13)',values)

    def test_whole_mean_sd_cell_and_endpoint_boundary(self):
        e=evidence(TABLE);q='군별 CL/F 평균과 SD 및 단위는?';c=selection_candidate.catalog(e,q)
        r=printed_pk.unique_selections(c,e,q,{})
        self.assertEqual(2,len(r['items']))
        self.assertCountEqual(['0.61 (0.13)','0.72 (0.17)'],[c[i['value_id']]['value'] for i in r['items']])
        self.assertFalse(any(c[i]['value']=='0.61' for i in selection_candidate.value_ids(c)))

    def test_model_label_and_another_dose_cannot_override_own_cell(self):
        html='<table><tr><th>Parameter</th><th>37 mg</th><th>74 mg</th></tr><tr><td>CL/F (L/day)</td><td></td><td></td></tr><tr><td>Median (Range)</td><td>6.1 (1.1, 9.2)</td><td>4.2 (1.7, 8.1)</td></tr></table>'
        e=evidence(html);q='37 mg 및 74 mg CL/F 중앙값과 범위 및 단위는?';c=selection_candidate.catalog(e,q)
        cid=next(i for i,v in c.items() if v['value']=='4.2 (1.7, 8.1)' and v['kind']=='table_cell')
        dose=next(i for i,v in c.items() if v['value']=='37 mg')
        r=selection_candidate.resolve({'status':'answered','items':[{'study':'none','item':'minimum','source_label':'37 mg 최솟값','value_id':cid,'exact':True,'conditions':[{'dimension':'dose','source_id':dose}]}]},c,e,q,{})
        i=r['items'][0]
        self.assertIn('74 mg',i['source_label']);self.assertNotIn('37 mg',i['source_label'])
        self.assertEqual(['74 mg'],[a['value'] for a in i['conditions'] if a['dimension']=='dose'])
        self.assertIn('범위',i['source_label'])

    def test_conflicting_duplicate_results_and_missing_sd_never_complete(self):
        e=evidence(TABLE+TABLE.replace('<td>11</td>','<td>12</td>'));q='군별 Tmax 최소값과 단위는?';c=selection_candidate.catalog(e,q)
        self.assertIsNone(printed_pk.unique_selections(c,e,q,{}))
        e=evidence(TABLE);q='군별 Tmax 평균과 SD 및 단위는?';c=selection_candidate.catalog(e,q)
        self.assertIsNone(printed_pk.unique_selections(c,e,q,{}))

    def test_compound_endpoint_sentence_only_and_no_stray_list_number(self):
        e=evidence('In a population pharmacokinetic analysis, the apparent clearance (CL/F) and volume (V/F) were 0.617 L/day and 16.2 L, respectively, in patients with psoriasis.\n\n1. Calculate dose.')
        q='건선 환자 CL/F 및 V/F 값과 단위는?';c=selection_candidate.catalog(e,q)
        values=[c[i]['value'] for i in selection_candidate.value_ids(c)]
        self.assertTrue(any('0.617 L/day and 16.2 L' in v for v in values))
        self.assertFalse(any('Calculate' in v or v=='1.' for v in values))
        self.assertIsNone(printed_pk.unique_selections(c,e,q,{}))

    def test_ambiguous_questions_keep_other_contract_path(self):
        for q in ['Tmax 평균; CL/F 중앙값','체중 60~90 kg Cmax 평균','Tmax 평균 또는 중앙값','EU Stelara 대비 Cmax GMR']:
            self.assertIsNone(printed_pk.request(q))

    def test_named_reference_arms_are_not_ratios(self):
        r=printed_pk.request('ABP654, US-Stelara, EU-Stelara 군의 Tmax 중앙값과 범위 및 단위는?')
        self.assertEqual(['ABP654','US-Stelara','EU-Stelara'],r['arms'])
        self.assertEqual(['median','range'],r['statistics'])
    def test_retrieval_roles_ignore_wrong_parameter_and_next_block(self):
        r=printed_pk.request('군별 CL/F 평균과 SD 및 단위는?')
        self.assertEqual(2,printed_pk.retrieval_score(r,{'text':TABLE}))
        self.assertEqual(0,printed_pk.retrieval_score(r,{'text':TABLE.split('<tr><td>CL/F')[0]+'</table>'}))
    def test_reference_proper_name_alias_uses_catalog_and_printed_region(self):
        from unittest.mock import patch
        with patch('rag.treatment_identity.references',return_value={'NEW':{'name':'ReferenceBrand','proper_name':'molecule-x'}}):
            self.assertEqual('US-ReferenceBrand',printed_pk._printed_arm('molecule-x (US)','NEW'))
            self.assertIsNone(printed_pk._printed_arm('molecule-x','NEW'))
            self.assertIsNone(printed_pk._printed_arm('different-molecule (US)','NEW'))
            self.assertIsNone(printed_pk._printed_arm('molecule-x (US)','OTHER'))
    def test_complete_quantitative_sentence_does_not_inherit_next_population(self):
        e=evidence('CL/F and V/F were 0.617 L/day and 16.2 L, respectively, in patients with psoriasis. Antibody-positive patients had higher clearance.')
        c=selection_candidate.catalog(e,'건선 환자 CL/F와 V/F 값과 단위는?')
        values=[c[i]['value'] for i in selection_candidate.value_ids(c)]
        self.assertTrue(values);self.assertFalse(any('Antibody-positive' in v for v in values))

    def test_endpoint_subscripts_do_not_become_a_maximum_statistic(self):
        for header in ['$ T_{max} $ (day)',r'C_{\mathrm{max}} (ng/mL)','최대 혈중 농도 (ng/mL)']:
            self.assertEqual(set(),printed_pk.statistics(header))
            self.assertEqual({'mean','SD'},printed_pk.statistics(header+' Mean (SD)'))
            self.assertEqual({'maximum'},printed_pk.statistics(header+' Max'))
        html=TABLE.replace('CL/F (L/day)','$ T_{max} $ (day)').replace('<td>Tmax (h)</td>','<td>Cmax (ng/mL)</td>')
        e=evidence(html);q='군별 Tmax 평균과 SD 및 단위는?';c=selection_candidate.catalog(e,q)
        raw=printed_pk.unique_selections(c,e,q,{})
        raw.pop('_proof')
        r=selection_candidate.resolve(raw,c,e,q,{})
        self.assertTrue(r['items'])
        for item in r['items']:
            self.assertIn('평균/SD',item['source_label']);self.assertNotIn('최대',item['source_label'])

    def test_whole_pk_sentence_does_not_accept_nearby_dose_as_statistic(self):
        sentence='CL/F and V/F were 0.617 L/day and 16.2 L, respectively, in patients with psoriasis.'
        e=evidence('Subcutaneous doses ranged from 31 mg to 241 mg.\n\n'+sentence)
        q='건선 환자 CL/F와 V/F 값과 단위는?';c=selection_candidate.catalog(e,q)
        cid=next(i for i,v in c.items() if v.get('value_eligible') is not False and '0.617 L/day' in v['value'])
        dose=next(i for i,v in c.items() if v['kind']=='source_paragraph' and '31 mg' in v['value'])
        raw={'status':'answered','items':[{'study':'none','item':'clearance','source_label':'CL/F 평균','value_id':cid,'exact':True,
            'conditions':[{'dimension':'statistic','source_id':dose}]}]}
        r=selection_candidate.resolve(raw,c,e,q,{})['items'][0]
        self.assertEqual(sentence,r['value'].strip())
        self.assertEqual([],r['conditions'])
        self.assertIn('V/F',r['source_label']);self.assertNotIn('평균',r['source_label'])
        self.assertTrue(r['removed_unbound_pk_qualifiers'])
        self.assertFalse(r['printed_pk_role_binding']['individual_number_correspondence_certified'])

if __name__=='__main__':unittest.main()
