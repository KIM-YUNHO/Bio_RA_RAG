import unittest
from rag import document_structure as ds
from rag.source_selection import catalog,resolve,NUMBER
from rag.source_context import fact_role
from rag.dosing_coverage import slots,enforce
from rag.answer_conditions import label,missing


class Finalization(unittest.TestCase):
    def test_unidentified_phase_heading_stops_prior_trial_ancestor(self):
        tracker=ds.Tracker('doc','v','SB17',version=ds.VERSION)
        texts=['2.6.2 Clinical pharmacology','Clinical Phase I study (SB17-1001)','Results','Phase III study','Results']
        refs=[{'block_id':str(i),'line_start':i,'line_end':i} for i in range(len(texts))]
        states=tracker.scan(texts,refs,1)[3]
        self.assertEqual(states[2]['heading_studies'],['ST_SB17_1001'])
        self.assertEqual(states[4]['heading_studies'],[])
        self.assertNotIn('Clinical Phase I study (SB17-1001)',states[4]['path'])
        self.assertIn('Phase III study',states[4]['path'])

    def test_unregistered_study_title_is_a_boundary_without_an_invented_identifier(self):
        heading=ds.identify('Phase II/III trial (TRIAL-900)','SB17',version=ds.VERSION)
        self.assertEqual(heading['kind'],'study_boundary');self.assertFalse(heading['study_keys'])
        self.assertIsNone(ds.identify('The Phase III study showed a benefit.','SB17',version=ds.VERSION))
        self.assertIsNone(ds.identify('Phase III study','SB17',version='sections-6'))
        self.assertIsNotNone(ds.identify('Subcutaneous Adult Dosage Regimen','SB17',version='sections-6'))

    def fixture(self):
        text='Subcutaneous Adult Dosage Regimen\nFor patients weighing 80 kg or less, give 35 mg initially and 6 weeks later, then every 10 weeks.\nFor patients weighing more than 80 kg, give 70 mg initially and 6 weeks later, then every 10 weeks.'
        evidence={'E1':{'text':text,'product_id':'unseen','document_id':'d','version_id':'v',
            'structure':{'section_id':'s','heading_anchors':[]},'source_spans':[]}}
        question='80 kg 이하 및 초과의 용량, 투여 경로, 초기 및 유지 투여 간격은?'
        return evidence,question,catalog(evidence,question)

    def test_same_interval_in_other_weight_paragraph_does_not_cover_requested_group(self):
        evidence,question,candidates=self.fixture();required=slots(candidates,evidence,question)
        self.assertEqual(len(required),6)
        selected=[{'source_selection':{'value_id':s['value_ids'][0]},'source_fact_role':fact_role(candidates[s['value_ids'][0]]),'conditions':[]}
                  for s in required if s['band']=='lower']
        out={'status':'answered','items':selected,'validation':{},'answer_ko':'Original'}
        enforce(out,candidates,evidence,question)
        self.assertEqual(out['status'],'partial')
        self.assertEqual(sum(m['population'].startswith('patients weighing more') for m in out['validation']['requested_dosing_coverage']['missing']),3)
        self.assertEqual(len(out['items']),3)

    def test_all_weight_specific_locations_and_route_cover_complete_request(self):
        evidence,question,candidates=self.fixture();required=slots(candidates,evidence,question)
        ids=[s['value_ids'][0] for s in required]+[next(c['id'] for c in candidates.values() if c['kind']=='literal_term')]
        selections=[{'study':'none','item':'fact','source_label':'generated','value_id':cid,'exact':True,'conditions':[]} for cid in ids]
        out=resolve({'status':'answered','items':selections},candidates,evidence,question,{'studies':[]})
        out['validation']={};enforce(out,candidates,evidence,question)
        self.assertEqual(out['status'],'answered');self.assertFalse(out['validation']['requested_dosing_coverage']['missing'])
        self.assertFalse(missing(out['items'],question,{'studies':[]}))

    def test_nonlocal_weight_condition_removed_and_route_does_not_inherit_dose(self):
        evidence,question,candidates=self.fixture()
        value=next(c for c in candidates.values() if c['value']=='35 mg')
        paragraph=next(c for c in candidates.values() if c['kind']=='source_paragraph')
        selection={'study':'none','item':'dose','source_label':'generated','value_id':value['id'],'exact':True,
            'conditions':[{'dimension':'population','source_id':paragraph['id']}]}
        item=resolve({'status':'answered','items':[selection]},candidates,evidence,question)['items'][0]
        self.assertTrue(item['source_context']['removed_nonlocal_conditions'])
        self.assertFalse(any('more than 80' in c['value'] for c in item['conditions']))
        self.assertIn('patients weighing 80 kg or less',label(item))
        self.assertNotIn('For patients',label(item))
        route=next(c for c in candidates.values() if c['kind']=='literal_term')
        selection.update(value_id=route['id'],conditions=[])
        route_item=resolve({'status':'answered','items':[selection]},candidates,evidence,question)['items'][0]
        self.assertFalse(any(c['dimension'] in ('dose','population','dosing_interval') for c in route_item['conditions']))

    def test_treatment_column_is_not_displayed_as_row_header(self):
        evidence,question,_=self.fixture()
        evidence['E1']['text']='<table><tr><td>Treatment</td><td>Test</td><td>EU Reference</td></tr><tr><td>Pharmacokinetic Analysis Set (PKS)</td><td>37</td><td>38</td></tr></table>'
        candidates=catalog(evidence,'군별 PK set 인원은?');value=next(c for c in candidates.values() if c['value']=='38')
        item=resolve({'status':'answered','items':[{'study':'none','item':'count','source_label':'count','value_id':value['id'],
            'exact':True,'conditions':[]}]},candidates,evidence,'군별 PK set 인원은?')['items'][0]
        self.assertFalse(any(c['dimension']=='row_header' for c in item['conditions']))
        self.assertTrue(any(c['dimension']=='arm' and c['value']=='EU Reference' for c in item['conditions']))

    def test_regulatory_days_are_not_a_dosing_interval_and_units_stay_compound(self):
        self.assertIsNone(fact_role({'kind':'literal_number','value':'30 days'},'Submit within 30 days of first commercial marketing.'))
        self.assertEqual([m[0] for m in NUMBER.finditer('2°C 0.75 mg/kg 226.9 mcg·day/mL')],['2°C','0.75 mg/kg','226.9 mcg·day/mL'])

    def test_source_body_and_required_heading_reserve_budget_together(self):
        from rag.evidence_budget import select
        table={'chunk_id':'table','document_id':'d','version_id':'v','pdf_page':2}
        filler={'chunk_id':'filler','document_id':'d','version_id':'v','pdf_page':3}
        heading={'chunk_id':'heading','document_id':'d','version_id':'v','pdf_page':1}
        queue=[(table,'retrieved'),(filler,'retrieved'),(heading,'source_heading')]
        kept,omitted=select(queue,lambda proposed:len(proposed)<=2,{'table':['heading']})
        self.assertEqual([c['chunk_id'] for c,_ in kept],['table','heading'])
        self.assertEqual([c['chunk_id'] for c in omitted],['filler'])
        kept,omitted=select(queue,lambda proposed:len(proposed)<=1,{'table':['heading']})
        self.assertNotIn('table',[c['chunk_id'] for c,_ in kept])

    def test_qualifying_pk_study_title_is_literal_and_old_rules_remain_supported(self):
        title='PK similarity study in healthy adult subjects (20190230)'
        self.assertEqual(ds.identify(title,'ABP654',version=ds.VERSION)['study_keys'],['ST_ABP_20190230'])
        self.assertIsNone(ds.identify(title,'ABP654',version='sections-7'))
        self.assertIsNone(ds.identify('The study in healthy subjects was reported (20190230)','ABP654',version=ds.VERSION))
        self.assertIsNotNone(ds.identify('Phase III study','SB17',version='sections-7'))

    def test_long_native_paragraph_retains_all_source_ranges_and_analysis_role(self):
        from rag.source_selection import prose_ranges,condition_ids
        text=('A background statement.\n'*150)+'The approved indication is in adults and pediatric patients 8 years and older.\nFull analysis set (FAS).'
        ranges=list(prose_ranges(text,0,len(text)))
        self.assertEqual(''.join(text[a:b] for a,b in ranges),text)
        evidence={'E1':{'text':text,'product_id':'unseen'}}
        candidates=catalog(evidence)
        self.assertTrue(any('8 years' in c['value'] for c in candidates.values()))
        self.assertTrue(any('FAS' in candidates[k]['value'] for k in condition_ids(candidates)['analysis']))

    def test_pk_statistic_question_outranks_dosing_even_when_topic_is_dosing(self):
        from rag.clinical_retrieval import cues
        query='single subcutaneous dose 45 mg and 90 mg tmax mean median'
        table={'text':'<table><tr><td>tmax (day)</td><td>Mean</td><td>Median</td></tr></table>','structure':{'kind':'table_candidate'},'product_id':'unseen'}
        dose={'text':'Adults receive 45 mg initially by subcutaneous injection.','structure':{'section_path':['Recommended dosage']},'product_id':'unseen'}
        self.assertIn('printed_endpoint_and_requested_statistics',cues(query,table,topic='dosing'))
        self.assertNotIn('printed_dose_and_administration',cues(query,dose,topic='dosing'))

    def test_label_retains_maintenance_interval_and_short_route(self):
        text='For patients weighing more than 80 kg, give 70 mg initially and 6 weeks later, followed by 70 mg every 10 weeks.'
        item={'source_label':'maintenance','source_fact_role':'dosing_interval','value':'10 weeks','conditions':[{'dimension':'dosing_interval','value':text}]}
        self.assertIn('every 10 weeks',label(item))
        item.update(source_fact_role='route',conditions=[{'dimension':'route','value':'Subcutaneous Adult Dosage Regimen\n'+text}])
        self.assertNotIn('For patients',label(item))

    def test_requested_indication_and_dose_are_ranked_by_their_printed_facts(self):
        from rag.clinical_retrieval import cues
        indication={'text':'The product is indicated in pediatric patients 8 years and older with active arthritis.','structure':{},'product_id':'unseen'}
        assessment={'text':'Required pediatric assessments: no studies are required under PREA.','structure':{},'product_id':'unseen'}
        self.assertIn('printed_requested_indications',cues('pediatric indication minimum age',indication))
        self.assertNotIn('printed_requested_indications',cues('pediatric indication minimum age',assessment))
        dose={'text':'Each subject received a single 75 mg SC injection.','structure':{},'product_id':'unseen'}
        self.assertIn('printed_dose_and_administration',cues('single dose and administration route',dose,topic='dosing'))

    def test_product_identifier_is_not_a_numeric_dose(self):
        evidence={'E1':{'text':'ABP 654: each subject received a single 90 mg SC injection.','product_id':'ABP654'}}
        candidates=catalog(evidence,'단회 투여 용량과 경로는?')
        self.assertFalse(next(c['value_eligible'] for c in candidates.values() if c['value']=='654'))
        self.assertTrue(next(c.get('value_eligible',True) for c in candidates.values() if c['value']=='90 mg'))

    def test_wrong_printed_trial_cannot_be_overridden_by_a_heading_hint(self):
        from rag.answer_conditions import attribution_missing
        evidence={'E1':{'text':'Study 20190232: value 654','product_id':'ABP654','structure':{'section_id':'s'},'source_spans':[]},
            'E2':{'text':'Single dose PK study 20190230','product_id':'ABP654','structure':{'section_id':'s'},'source_spans':[]}}
        item={'study':'ST_ABP_20190230','evidence':['E1'],'conditions':[{'dimension':'study','quote':evidence['E2']['text'],'evidence':'E2'}]}
        self.assertEqual(attribution_missing([item],evidence,{'studies':['ST_ABP_20190230']},[])[0]['dimensions'],['study'])

    def test_primary_analysis_qualification_is_bound_to_same_source_block(self):
        evidence={'E1':{'text':'Study CT-P43 3.1\nPrimary efficacy endpoint was PASI at Week 12.\nThe primary efficacy analysis was conducted on the FAS using an ANCOVA model.','product_id':'CTP43','document_id':'d','version_id':'v','structure':{'section_id':'s','heading_anchors':[]},'source_spans':[]}}
        candidates=catalog(evidence);value=next(c for c in candidates.values() if c['kind']=='source_line' and 'endpoint was' in c['value'])
        selected={'study':'ST_CTP43_3_1','item':'endpoint','source_label':'endpoint','value_id':value['id'],'exact':True,'conditions':[]}
        item=resolve({'status':'answered','items':[selected]},candidates,evidence,'주 분석 집단과 평가변수는?',{'studies':['ST_CTP43_3_1']})['items'][0]
        self.assertTrue(any(c['dimension']=='analysis' and 'FAS' in c['quote'] for c in item['conditions']))

    def test_pen_storage_is_prioritized_even_with_a_dosing_topic(self):
        from rag.clinical_retrieval import cues
        chunk={'text':'Pre-filled pens may be stored at room temperature up to 27°C for 25 days, then returned to the refrigerator once.','structure':{},'product_id':'unseen'}
        found=cues('pre-filled pen room temperature storage and refrigerator conditions',chunk,topic='dosing')
        self.assertIn('printed_requested_pen_storage',found)
        self.assertNotIn('printed_dose_and_administration',found)

    def test_notification_deadline_requires_the_requested_event_not_a_letter_date(self):
        evidence={'E1':{'text':'Submit labeling within 14 days of this letter.\nSubmit correspondence informing the Agency of the date of first commercial marketing within 30 days of that date.','product_id':'unseen'}}
        candidates=catalog(evidence,'첫 상업적 시판 후 통지 기한은?')
        self.assertFalse(next(c['value_eligible'] for c in candidates.values() if c['value']=='14 days'))
        self.assertTrue(next(c.get('value_eligible',True) for c in candidates.values() if c['value']=='30 days'))

    def test_pediatric_indications_do_not_use_adult_or_pediatric_assessment_clauses(self):
        text='Indication:\nA medicine indicated for the treatment of:\nAdult patients with:\n• adult-only disease.\nPediatric patients 8 years and older with:\n• pediatric disease.'
        evidence={'E1':{'text':text,'product_id':'unseen'},'E2':{'text':'REQUIRED PEDIATRIC ASSESSMENTS\nNo pediatric studies are required in patients 0 to 17 years of age.','product_id':'unseen'}}
        candidates=catalog(evidence,'소아 적응증과 최저 연령은?')
        self.assertTrue(any(c.get('value_eligible',True) and 'pediatric disease' in c['value'] for c in candidates.values()))
        self.assertFalse(any(c.get('value_eligible',True) and 'adult-only disease' in c['value'] for c in candidates.values()))
        self.assertFalse(next(c['value_eligible'] for c in candidates.values() if c['value']=='0 to 17 years'))

    def test_explicit_dose_premise_probe_still_supplies_source_administration(self):
        from rag.clinical_retrieval import cues
        source={'text':'Each subject received 45 mg as a single SC injection.','structure':{},'product_id':'unseen'}
        self.assertIn('printed_dose_and_administration',cues('90 mg Cmax GMR',source,topic='dosing'))
        self.assertNotIn('printed_dose_and_administration',cues('90 mg Cmax GMR',source,topic='pk_results'))

    def test_marketing_launch_query_preserves_the_actual_notification_clause(self):
        from rag.regulatory_scope import requests_marketing_notification
        from rag.evidence_slices import score
        query='notification deadline and content after first commercial launch'
        self.assertTrue(requests_marketing_notification(query))
        action='Submit correspondence informing the Agency of the first commercial marketing within 30 days.'
        unrelated='First commercial marketing triggers one year of exclusivity.'
        self.assertGreater(score(action,query),score(unrelated,query))

    def test_new_indications_in_prea_text_are_not_an_approved_indication_cue(self):
        from rag.clinical_retrieval import cues
        source={'text':'REQUIRED PEDIATRIC ASSESSMENTS. All applications for new indications must contain an assessment for the claimed indication in pediatric patients.','product_id':'unseen','structure':{}}
        self.assertNotIn('printed_requested_indications',cues('pediatric indications minimum age',source))

    def test_direct_quotation_path_blocks_wrong_event_and_preserves_source_value(self):
        from rag.regulatory_scope import enforce
        text='Submit labeling within 14 days of this letter.\nSubmit correspondence informing the Agency of the first commercial marketing within 40 days.'
        evidence={'E1':{'text':text}}
        bad={'value':'14 days','quote':text.splitlines()[0],'evidence':['E1'],'source_label':'deadline','conditions':[]}
        good={'value':'40 days','quote':text.splitlines()[1],'evidence':['E1'],'source_label':'deadline','conditions':[]}
        out={'status':'answered','items':[bad,good],'validation':{}}
        enforce(out,evidence,'첫 상업적 시판 후 통지 기한은?')
        self.assertEqual(out['status'],'partial');self.assertEqual(out['items'],[good])
        self.assertEqual(out['items'][0]['value'],'40 days')

    def test_direct_quotation_path_does_not_use_adult_indication_for_child(self):
        from rag.regulatory_scope import literal_item_eligible
        text='Indication:\nDrug indicated for treatment of:\nAdult patients with:\n• adult-only illness.\nPediatric patients 8 years and older with:\n• childhood illness.'
        evidence={'E1':{'text':text}}
        item={'value':'adult-only illness','quote':text,'evidence':['E1']}
        self.assertFalse(literal_item_eligible(item,evidence,'소아 적응증은?'))
        item['value']='childhood illness'
        self.assertTrue(literal_item_eligible(item,evidence,'소아 적응증은?'))

    def test_approval_request_does_not_use_a_preapproval_correspondence(self):
        from rag.regulatory_scope import literal_item_eligible
        text='Indication: Reference medicine is indicated for pediatric patients 8 years and older.'
        evidence={'E1':{'text':text,'document_type':'Administrative Correspondence','document_metadata':{'family':'correspondence'}}}
        item={'value':'8 years','quote':text,'evidence':['E1']}
        self.assertFalse(literal_item_eligible(item,evidence,'허가 서신에서 소아 적응증 최저 연령은?'))
        evidence['E1'].update(document_type='Approval Letter',document_metadata={'family':'decision_letter'})
        self.assertTrue(literal_item_eligible(item,evidence,'허가 서신에서 소아 적응증 최저 연령은?'))

    def test_unfinished_action_line_is_not_a_complete_fact(self):
        from rag.regulatory_scope import unfinished_value
        self.assertTrue(unfinished_value('For each approved product, submit a '))
        self.assertTrue(unfinished_value('The drug is indicated for patients with '))
        self.assertFalse(unfinished_value('Treatment A'))
        self.assertFalse(unfinished_value('Submit the date of first commercial marketing.'))


if __name__=='__main__':unittest.main()
