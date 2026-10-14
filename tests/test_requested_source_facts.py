import unittest
from rag.source_selection import catalog,resolve,value_ids
from rag.prose import ranges
from rag.requested_facts import slots,enrich,enforce
from rag.regulatory_scope import literal_item_eligible

class RequestedSourceFacts(unittest.TestCase):
    def evidence(self,text,**extra):
        return {'E1':{'text':text,'product_id':'unseen','document_id':'doc','version_id':'v','structure':{},'source_spans':[],**extra}}
    def selected(self,candidates,evidence,ids,question):
        out=resolve({'status':'answered','items':[{'study':'none','item':'fact','source_label':'원문 사실','value_id':cid,'exact':True,'conditions':[]} for cid in ids]},candidates,evidence,question)
        out['validation']={};enrich(out,candidates,evidence,question);enforce(out,candidates,evidence,question)
        return out
    def child_fixture(self):
        text='Indication:\nMedicine is indicated for treatment of:\nAdult patients with:\n• adult disease\nPediatric patients 8 years and older with:\n• moderate disease in candidates for light\nor systemic therapy\n• active joint disease\nMANUFACTURING LOCATIONS\nManufacture the product at site 44.\nDATING PERIOD\nThe product lasts 42 months.'
        question='허가 서신에 기재된 소아 적응증 최저 연령과 적응증은?'
        evidence=self.evidence(text,document_type='Approval Letter',document_metadata={'family':'decision_letter'})
        return evidence,question,catalog(evidence,question)
    def test_heading_partition_preserves_all_source_and_ends_child_scope(self):
        evidence,_,_=self.child_fixture();text=evidence['E1']['text'];parts=[text[a:b] for a,b in ranges(text,0,len(text))]
        self.assertEqual(''.join(parts),text)
        child=next(p for p in parts if p.startswith('Pediatric'))
        self.assertIn('systemic therapy',child);self.assertNotIn('MANUFACTURING',child)
    def test_manufacturing_and_adult_values_are_not_child_candidates(self):
        _,_,candidates=self.child_fixture()
        for c in candidates.values():
            if c['value'] in ('44','42 months') or 'adult disease' in c['value']:
                self.assertNotIn(c['id'],value_ids(candidates))
    def test_wrapped_indication_bullet_has_complete_literal_candidate(self):
        evidence,question,candidates=self.child_fixture()
        c=next(c for c in candidates.values() if c.get('source_sentence') and 'light\nor systemic therapy' in c['value'])
        self.assertEqual(evidence['E1']['text'][c['start']:c['end']],c['value'])
        self.assertIn(c['id'],value_ids(candidates))
        self.assertNotIn(next(c['id'] for c in candidates.values() if c['kind']=='source_line' and c['value']=='or systemic therapy'),value_ids(candidates))
    def test_age_alone_does_not_cover_indications_and_one_bullet_does_not_cover_two(self):
        evidence,question,candidates=self.child_fixture()
        age=next(c['id'] for c in candidates.values() if c['value']=='8 years')
        bullet=next(c['id'] for c in candidates.values() if c.get('source_sentence') and 'light\nor systemic' in c['value'])
        out=self.selected(candidates,evidence,[age,bullet],question)
        self.assertEqual(out['status'],'partial')
        self.assertEqual(len(out['validation']['requested_fact_coverage']['missing']),1)
    def test_complete_child_region_covers_age_and_each_source_indication(self):
        evidence,question,candidates=self.child_fixture()
        cid=next(c['id'] for c in candidates.values() if c['kind']=='source_paragraph' and c['value'].startswith('Pediatric') and 'active joint disease' in c['value'])
        out=self.selected(candidates,evidence,[cid],question)
        self.assertEqual(out['status'],'answered');self.assertEqual(out['validation']['requested_fact_coverage']['required_count'],3)
    def test_wrapped_notification_content_and_deadline_are_separate_obligations(self):
        text='A prior submission takes 9 days.\nFor each approved product, submit a\ngeneral correspondence informing the Agency of the date of the\nfirst commercial marketing within 40 days of that date.\nOTHER REQUIREMENTS\nSubmit another form within 11 days.'
        question='첫 상업적 시판 후 통지 기한과 통지 내용은?';evidence=self.evidence(text);candidates=catalog(evidence,question)
        deadline=next(c['id'] for c in candidates.values() if c['value']=='40 days')
        out=self.selected(candidates,evidence,[deadline],question)
        self.assertEqual(out['status'],'partial');self.assertEqual(out['validation']['requested_fact_coverage']['missing'][0]['name'],'notification_content')
        sentence=next(c['id'] for c in candidates.values() if c.get('source_sentence') and c['value'].lstrip().startswith('For each'))
        out=self.selected(candidates,evidence,[sentence],question)
        self.assertEqual(out['status'],'answered');self.assertFalse(out['validation']['requested_fact_coverage']['missing'])
    def test_short_notification_line_and_other_event_are_not_selectable(self):
        question='first commercial marketing notification deadline and content'
        evidence=self.evidence('Submit labeling within 9 days.\nFor each approved product, submit a\ncorrespondence informing the Agency of the date of first commercial marketing within 40 days.')
        candidates=catalog(evidence,question)
        for c in candidates.values():
            if c['value']=='9 days' or c['value'].endswith('submit a'):self.assertNotIn(c['id'],value_ids(candidates))
    def test_storage_return_inherits_complete_printed_timing_and_expiry(self):
        text='Storing the pre-filled pen\n•\nThe pen may be stored at room temperature up to 27°C for up to 25 days.\n•\nAt any time before the end of that period, the product can be\nreturned to the refrigerator once, up to the original expiry date.'
        question='프리필드 펜의 실온 보관 최대 온도와 기간, 냉장고에 다시 넣을 수 있는 조건은?'
        evidence=self.evidence(text);candidates=catalog(evidence,question)
        ids=[next(c['id'] for c in candidates.values() if c['value']==v) for v in ('27°C','25 days')]
        ids.append(next(c['id'] for c in candidates.values() if c['kind']=='source_line' and c['value'].startswith('returned to')))
        out=self.selected(candidates,evidence,ids,question)
        condition=next(c for c in out['items'][-1]['conditions'] if c['dimension']=='eligibility')
        self.assertIn('before the end',condition['value']);self.assertIn('original expiry',condition['value'])
        self.assertEqual(out['status'],'answered');self.assertFalse(out['validation']['requested_fact_coverage']['missing'])
    def test_refrigerated_temperature_is_not_room_temperature_limit(self):
        evidence=self.evidence('• Store the pen in a refrigerator between 3°C and 7°C.\n• Store at room temperature up to 27°C for 25 days.')
        candidates=catalog(evidence,'실온 보관 최대 온도와 기간은?');required=slots(candidates,evidence,'실온 보관 최대 온도와 기간은?')
        temperatures=next(s['value_ids'] for s in required if s['name']=='storage_temperature')
        self.assertNotIn(next(c['id'] for c in candidates.values() if c['value']=='3°C'),temperatures)
        self.assertIn(next(c['id'] for c in candidates.values() if c['value']=='27°C'),temperatures)

    def test_secondary_cold_storage_duration_does_not_cover_room_temperature_period(self):
        question='실온 보관 온도와 기간, 냉장고에 다시 넣을 수 있는 조건은?'
        text='Store at room temperature up to 27°C for 25 days. At the end of this period the product may be placed back to the refrigerator for up to 50 days at 3-7°C.'
        evidence=self.evidence(text);candidates=catalog(evidence,question);required=slots(candidates,evidence,question)
        period=next(s for s in required if s['name']=='storage_period')
        self.assertNotIn(next(c['id'] for c in candidates.values() if c['value']=='50 days'),period['value_ids'])
        self.assertTrue(next(s for s in required if s['name']=='storage_return')['value_ids'])

    def test_following_expiry_sentence_is_retained_without_combining_quotes(self):
        text='Store at room temperature up to 27°C for 25 days. At the end of this period the product may be placed back to the refrigerator for up to 50 days at 3-7°C. Discard the product after this period or the original expiry date, whichever is earlier.'
        question='실온 보관 온도와 기간, 냉장고에 다시 넣는 조건은?';evidence=self.evidence(text);candidates=catalog(evidence,question)
        cid=next(c['id'] for c in candidates.values() if c.get('source_sentence') and c['value'].startswith('At the end'))
        out=self.selected(candidates,evidence,[cid],question)
        conditions=out['items'][0]['conditions']
        self.assertTrue(any(c['value'].startswith('Discard') for c in conditions))
        self.assertTrue(all(c['quote'] in text for c in conditions))

    def secondary_fixture(self):
        text='Serum concentrations were affected by weight.\n11 STORAGE, STABILITY AND DISPOSAL\nStore at room temperature up to 27°C for 25 days. At the end of this period the product may be placed back to the refrigerator for up to 50 days at 3-7°C. Discard the product after 25 days at room temperature and 50 days back in the refrigerator or the original expiry date, whichever is earlier.'
        question='실온 보관 온도와 기간, 냉장고에 다시 넣은 이후 보관 기간과 폐기 기준은?'
        evidence=self.evidence(text)
        return evidence,question,catalog(evidence,question)

    def test_numbered_heading_delimits_storage_without_losing_source(self):
        evidence,_,_=self.secondary_fixture();text=evidence['E1']['text']
        parts=[text[a:b] for a,b in ranges(text,0,len(text))]
        self.assertEqual(''.join(parts),text)
        storage=next(p for p in parts if p.startswith('11 STORAGE'))
        self.assertNotIn('Serum concentrations',storage)

    def test_secondary_numeric_fact_preserves_full_return_and_disposal(self):
        evidence,question,candidates=self.secondary_fixture()
        ids=[next(c['id'] for c in candidates.values() if c['value']==v) for v in ('27°C','25 days','50 days')]
        out=self.selected(candidates,evidence,ids,question)
        self.assertEqual(out['status'],'answered')
        self.assertEqual(out['validation']['requested_fact_coverage']['required_count'],4)
        self.assertEqual([i['value'] for i in out['items']],['27°C','25 days','50 days'])
        conditions=out['items'][-1]['conditions']
        self.assertTrue(any('At the end' in c['value'] and '3-7°C' in c['value'] for c in conditions))
        self.assertTrue(any(c['value'].startswith('Discard') and 'whichever is earlier' in c['value'] for c in conditions))

    def test_room_storage_alone_cannot_cover_return_or_disposal(self):
        evidence,question,candidates=self.secondary_fixture()
        ids=[next(c['id'] for c in candidates.values() if c['value']==v) for v in ('27°C','25 days')]
        out=self.selected(candidates,evidence,ids,question)
        self.assertEqual(out['status'],'partial')
        self.assertEqual({m['name'] for m in out['validation']['requested_fact_coverage']['missing']},{'storage_return','storage_disposal'})

    def test_disposal_sentence_with_return_words_does_not_cover_return_permission(self):
        evidence,question,candidates=self.secondary_fixture()
        cid=next(c['id'] for c in candidates.values() if c.get('source_sentence') and c['value'].startswith('Discard'))
        out=self.selected(candidates,evidence,[cid],question)
        self.assertIn('storage_return',{m['name'] for m in out['validation']['requested_fact_coverage']['missing']})

    def test_disposal_from_another_section_is_not_attached(self):
        text='Store at room temperature up to 27°C for 25 days. At the end of this period the product may be placed back to the refrigerator for up to 50 days.\n12 INFUSION DISPOSAL\nDiscard the diluted infusion by the expiry date.'
        question='실온 보관과 냉장고에 다시 넣는 조건, 폐기 기준은?';evidence=self.evidence(text);candidates=catalog(evidence,question)
        cid=next(c['id'] for c in candidates.values() if c['value']=='50 days')
        out=self.selected(candidates,evidence,[cid],question)
        self.assertFalse(any(c['value'].startswith('Discard') for c in out['items'][0]['conditions']))
        self.assertIn('storage_disposal',{m['name'] for m in out['validation']['requested_fact_coverage']['missing']})

    def test_storage_schema_excludes_unrelated_renal_value(self):
        evidence=self.evidence('Renal impairment was not studied.\n11 STORAGE AND DISPOSAL\nStore at room temperature up to 27°C for 25 days.')
        candidates=catalog(evidence,'실온 보관 온도와 기간은?')
        self.assertTrue(value_ids(candidates))
        self.assertTrue(all('Renal' not in candidates[cid]['value'] for cid in value_ids(candidates)))

    def test_direct_quote_path_rejects_unrelated_storage_answer(self):
        from rag.requested_facts import restrict
        evidence=self.evidence('Renal impairment was not studied.\n11 STORAGE AND DISPOSAL\nStore at room temperature up to 27°C for 25 days.')
        question='실온 보관 온도와 기간은?';candidates=catalog(evidence,question)
        item={'value':'Renal impairment was not studied.','quote':'Renal impairment was not studied.','evidence':['E1'],'conditions':[]}
        out={'status':'answered','items':[item],'answer_ko':'wrong','validation':{}}
        restrict(out,candidates,evidence,question)
        self.assertEqual(out['status'],'not_found');self.assertFalse(out['items'])
        self.assertEqual(len(out['validation']['requested_fact_scope_rejections']),1)

    def test_both_doses_with_korean_postpositions_have_statistic_slots(self):
        text='<table><tr><td>Parameter</td><td>42 mg</td><td>84 mg</td></tr><tr><td>Statistic</td><td>Mean</td><td>Mean</td></tr><tr><td>tmax (day)</td><td>11</td><td>12</td></tr></table>'
        evidence=self.evidence(text);question='42 mg과 84 mg의 tmax 평균과 중앙값 및 단위는?';candidates=catalog(evidence,question)
        required=slots(candidates,evidence,question)
        self.assertEqual(len([s for s in required if s['name'].startswith('pk_') and s['name']!='pk_unit']),4)
        self.assertTrue(next(s for s in required if s['name']=='pk_unit')['value_ids'])
    def test_approval_source_requirement_applies_without_pediatric_keywords(self):
        evidence=self.evidence('Submit the original application.',document_type='Administrative Correspondence')
        item={'value':'original application','quote':evidence['E1']['text'],'evidence':['E1']}
        self.assertFalse(literal_item_eligible(item,evidence,'허가 서신에 명시된 제출 내용은?'))
    def test_gap_does_not_make_a_sentence_cross_missing_source(self):
        from rag.source_context import containing_sentence
        text='First fact.\n[UNSUPPLIED SOURCE GAP]\nSecond fact is 27 days.';evidence=self.evidence(text)
        start=text.index('27');candidate={'evidence':'E1','start':start,'end':start+7}
        self.assertEqual(containing_sentence(candidate,evidence),'Second fact is 27 days.')

if __name__=='__main__':unittest.main()
