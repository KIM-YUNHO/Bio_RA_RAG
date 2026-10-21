import unittest
from rag.evidence.source_selection import catalog,resolve
from rag.evidence.dosing_coverage import enforce
from rag.retrieval.regulatory_scope import literal_item_eligible
from rag.retrieval.clinical_retrieval import cues,requested_priority_cue
from rag.evidence.evidence_slices import score,view

class ContextRegressions(unittest.TestCase):
    def evidence(self,text):
        return {'E1':{'text':text,'product_id':'unseen','document_id':'doc','version_id':'v',
            'document_type':'Approval Letter','structure':{},'source_spans':[]}}
    def selected(self,evidence,question,candidates,ids):
        out=resolve({'status':'answered','items':[{'study':'none','item':'fact','source_label':'fact',
            'value_id':cid,'exact':True,'conditions':[]} for cid in ids]},candidates,evidence,question)
        out['validation']={};return out
    def test_displayed_full_regimens_cover_both_intervals_without_inventing_items(self):
        evidence=self.evidence('Subcutaneous adult dosage\nFor patients weighing 80 kg or less, give 35 mg initially and 6 weeks later, then every 10 weeks.\nFor patients weighing more than 80 kg, give 70 mg initially and 6 weeks later, then every 10 weeks.')
        question='80 kg 이하 및 초과의 용량과 초기 및 유지 투여 간격은?';candidates=catalog(evidence,question)
        ids=[next(c['id'] for c in candidates.values() if c['value']==v) for v in ('35 mg','70 mg')]
        out=self.selected(evidence,question,candidates,ids);enforce(out,candidates,evidence,question)
        self.assertEqual(out['status'],'answered');self.assertFalse(out['validation']['requested_dosing_coverage']['missing'])
        self.assertEqual([i['value'] for i in out['items']],['35 mg','70 mg'])
    def test_complete_lower_band_sentence_cannot_cover_upper_band(self):
        evidence=self.evidence('For patients weighing 80 kg or less, give 35 mg initially and 6 weeks later, then every 10 weeks.\nFor patients weighing more than 80 kg, give 70 mg initially and 6 weeks later, then every 10 weeks.')
        question='80 kg 이하 및 초과의 용량과 초기 및 유지 투여 간격은?';candidates=catalog(evidence,question)
        cid=next(c['id'] for c in candidates.values() if c['value']=='35 mg')
        out=self.selected(evidence,question,candidates,[cid]);enforce(out,candidates,evidence,question)
        self.assertEqual(out['status'],'partial')
        self.assertEqual(len(out['validation']['requested_dosing_coverage']['missing']),3)
    def test_duplicate_adult_bullet_does_not_reject_selected_pediatric_occurrence(self):
        evidence=self.evidence('Indication:\nMedicine is indicated for treatment of:\nAdult patients with:\n• active joint disease\nPediatric patients 8 years and older with:\n• active joint disease\nMANUFACTURING LOCATIONS\nSite 55.')
        question='허가 서신의 소아 적응증은?';candidates=catalog(evidence,question)
        bullet=next(c for c in candidates.values() if c.get('source_sentence') and c['value'].startswith('• active') and c.get('value_eligible',True))
        item=self.selected(evidence,question,candidates,[bullet['id']])['items'][0]
        self.assertTrue(literal_item_eligible(item,evidence,question))
        adult=evidence['E1']['text'].find(item['value'])
        item['source_selection']['source_range']=[adult,adult+len(item['value'])]
        self.assertFalse(literal_item_eligible(item,evidence,question))
    def test_adjusted_result_table_precedes_discussion_with_same_analysis_words(self):
        text='Ad-hoc ANCOVA with body weight covariate: geometric ratio for AUCinf and Cmax.'
        query='post hoc ANCOVA GMR AUCinf Cmax'
        table={'text':text,'structure':{'kind':'table_candidate'}}
        prose={'text':text,'structure':{'kind':'text'}}
        self.assertGreater(len(cues(query,table)),len(cues(query,prose)))
        self.assertNotIn('printed_adjusted_result_table',cues(query,{'text':'Primary ANOVA geometric ratio AUCinf','structure':{'kind':'table_candidate'}}))
    def test_direct_quote_checks_repeated_occurrences_within_requested_scope(self):
        evidence=self.evidence('Indication:\nMedicine is indicated for treatment of:\nAdult patients with:\n• active joint disease\nPediatric patients 8 years and older with:\n• active joint disease')
        item={'value':'• active joint disease','quote':'• active joint disease','evidence':['E1']}
        self.assertTrue(literal_item_eligible(item,evidence,'허가 서신의 소아 적응증은?'))
        evidence['E1']['text']='Indication:\nMedicine is indicated for treatment of:\nAdult patients with:\n• active joint disease'
        self.assertFalse(literal_item_eligible(item,evidence,'허가 서신의 소아 적응증은?'))
    def test_minimum_infusion_time_survives_separate_route_sentence(self):
        text='Medicine is for intravenous use only. It should be administered over at least two hours. Unrelated background.'
        unit={'text':text,'kind':'text','source_refs':[]}
        chunk={'chunk_id':'c','text':text,'provenance':{'source_spans':[{'unit_id':'u','representation_start':0,'representation_end':len(text),'separator_before':'','role':'content'}]},'representation':{'origins':{'u':'native_text_layer'}}}
        counter=type('Counter',(),{'one':lambda self,x:len(x)})()
        got=view(chunk,{'u':unit},'minimum intravenous infusion time',65,counter)
        self.assertIn('at least two hours',got['text'])
        self.assertGreater(score('It should be administered over at least two hours.','minimum intravenous infusion time'),score('Medicine is for intravenous use only.','minimum intravenous infusion time'))
    def test_approval_cover_date_is_a_source_cue_without_product_rules(self):
        query='허가 서신의 최초 승인 날짜는?'
        chunk={'text':'Approval Date:\n123456Orig1 – January 12, 2021\n123456Orig2 – May 18, 2022','document_type':'Approval Letter','structure':{}}
        self.assertIn('printed_approval_dates',cues(query,chunk))
    def test_original_question_preserves_letter_scope_omitted_in_translation(self):
        english='What are the initial approval dates in FDA correspondence?'
        original='FDA 허가 서신에서 각각의 최초 승인 날짜는?'
        self.assertEqual(requested_priority_cue(english+' '+original),'printed_approval_dates')
        self.assertIsNone(requested_priority_cue('What is the approval package application number?'))
    def test_infusion_duration_priority_requires_a_time_request(self):
        self.assertEqual(requested_priority_cue('크론병 정맥 유도치료의 최소 주입 시간은?'),'printed_infusion_duration')
        self.assertIsNone(requested_priority_cue('정맥 투여의 용량은?'))
    def test_same_source_fact_with_reordered_conditions_is_deduplicated(self):
        evidence=self.evidence('<table><tr><th>Dose</th><th>25 mg</th><th>50 mg</th></tr><tr><th>Parameter</th><th>Mean</th><th>Mean</th></tr><tr><td>tmax (day)</td><td>8.2</td><td>9.3</td></tr></table>')
        candidates=catalog(evidence,'tmax mean 25 mg');value=next(c for c in candidates.values() if c['value']=='8.2')
        headers=[candidates[k] for k in value['context_ids']]
        conditions=[{'dimension':'statistic' if c['value']=='Mean' else 'endpoint','source_id':c['id']} for c in headers if c['value']=='Mean' or 'tmax' in c['value']]
        item={'study':'none','item':'fact','source_label':'fact','value_id':value['id'],'exact':True,'conditions':conditions}
        other={**item,'conditions':list(reversed(conditions))}
        out=resolve({'status':'answered','items':[item,other]},candidates,evidence,'tmax mean 25 mg')
        self.assertEqual(len(out['items']),1)
    def test_notification_fact_does_not_inherit_unrelated_injection_route(self):
        evidence=self.evidence('The medicine is administered subcutaneously by subcutaneous injection.\nSubmit correspondence notifying the date of first commercial marketing within 40 days.')
        question='첫 상업적 시판 후 통지 기한은?';candidates=catalog(evidence,question)
        cid=next(c['id'] for c in candidates.values() if c['value']=='40 days')
        out=self.selected(evidence,question,candidates,[cid])
        self.assertFalse(any(c['dimension']=='route' for c in out['items'][0]['conditions']))

    def test_concentration_is_not_a_comparison_source(self):
        from rag.evidence.source_selection import condition_ids
        evidence=self.evidence('Serum concentration depends on weight.\nMedicine is for intravenous use. It should be administered over at least two hours.')
        candidates=catalog(evidence,'최소 주입 시간은?')
        self.assertNotIn('comparison',condition_ids(candidates))

    def test_infusion_time_does_not_keep_comparison_from_other_sentence(self):
        evidence=self.evidence('A ratio of exposure was measured. It should be administered over at least two hours.')
        candidates=catalog(evidence,'최소 주입 시간은?')
        value=next(c for c in candidates.values() if c.get('source_sentence') and 'at least two hours' in c['value'] and 'ratio' not in c['value'])
        other=next(c for c in candidates.values() if c.get('source_sentence') and c['value'].startswith('A ratio'))
        item={'study':'none','item':'infusion','source_label':'최소 주입 시간','value_id':value['id'],'exact':True,'conditions':[{'dimension':'comparison','source_id':other['id']}]}
        out=resolve({'status':'answered','items':[item]},candidates,evidence,'최소 주입 시간은?')
        self.assertFalse(out['items'][0]['conditions'])
        self.assertTrue(out['items'][0]['source_context']['removed_unconnected_conditions'])

    def test_full_source_paragraph_is_not_a_unit_condition(self):
        evidence=self.evidence('Store at room temperature up to 27°C for 25 days. The infusion lasts 9 hours.')
        candidates=catalog(evidence,'실온 보관 온도와 기간은?')
        number=next(c for c in candidates.values() if c['value']=='27°C')
        parent=next(c for c in candidates.values() if c['kind']=='source_paragraph' and not c.get('source_sentence'))
        item={'study':'none','item':'temperature','source_label':'실온 최대 온도','value_id':number['id'],'exact':True,'conditions':[{'dimension':'unit','source_id':parent['id']}]}
        out=resolve({'status':'answered','items':[item]},candidates,evidence,'실온 보관 온도와 기간은?')
        self.assertFalse(out['items'][0]['conditions'])

    def test_explicit_product_information_excludes_plain_summary(self):
        evidence=self.evidence('Infuse over at least two hours.')
        evidence['E1']['document_type']='overview';evidence['E1']['document_metadata']={'family':'plain_summary'}
        candidates=catalog(evidence,'제품정보의 최소 주입 시간은?')
        self.assertFalse(any(c.get('value_eligible',True) for c in candidates.values()))
        item={'value':'two hours','quote':evidence['E1']['text'],'evidence':['E1']}
        self.assertFalse(literal_item_eligible(item,evidence,'제품정보의 최소 주입 시간은?'))
        evidence['E1']['document_metadata']['family']='product_information'
        self.assertTrue(literal_item_eligible(item,evidence,'제품정보의 최소 주입 시간은?'))

if __name__=='__main__':unittest.main()
