import unittest
from rag import ra_quantities, ra_source_constraints, ra_citation_completion
from test_ra_citation_completion import block, claim


class TodayInvariants(unittest.TestCase):
    def test_hyphenated_duration_and_explicit_roman_phase_have_same_meaning(self):
        evidence={'E1':block('Phase I study. Total duration approximately 18 weeks including a 4-week screening period.')}
        a=claim(evidence,'1상 시험의 기간은 약 18주이며 4주 선별기간을 포함했다.')
        self.assertIsNone(ra_quantities.check(a,evidence))
        a['text']='2상 시험의 기간은 18주이며 5주 선별기간을 포함했다.'
        self.assertIsNotNone(ra_quantities.check(a,evidence))
        self.assertNotIn('1',ra_quantities.numbers('I consider IV administration appropriate.'))
        self.assertNotIn((1,'mg'),ra_quantities.quantities('Phase I study'))

    def test_product_name_inside_study_id_is_not_removed_into_a_negative_number(self):
        quote=('PK similarity study in healthy adult subjects (BAT-2206-001-CR). The ADA samples were collected at '
               'pre - dose, and at 10, 15, 29, 57, 85, and 113 days after drug administration.')
        evidence={'E1':block(quote,trial='BAT-2206-001-CR',product='BAT2206')}
        a=claim(evidence,'BAT-2206-001-CR에서 ADA 시료는 투여 전과 투여 후 10일, 15일, 29일, 57일, 85일 및 113일에 채취했다.')
        a['products']=['BAT2206']
        self.assertNotIn('-1',ra_quantities.numbers(ra_quantities.substantive_text(a,evidence)))
        self.assertIsNone(ra_quantities.check(a,evidence))
        a['text']='BAT-2206에서 ADA 시료는 투여 후 10일, 15일, 29일, 57일, 85일 및 120일에 채취했다.'
        self.assertEqual(ra_quantities.check(a,evidence),'claim_number_not_in_final_citations')

    def test_at_least_once_positive_is_not_a_single_dose_qualifier(self):
        evidence={'E1':block('At Least One Positive ADA After Administration 16.2% (18/111)')}
        for text in ['투여 후 ADA 양성(투여 후 최소 한 번 양성)은 16.2%였다.',
                     '투여 후 ADA 양성은 기저시점 이후 ADA 양성이 한 번 이상인 경우로 정의되었다.']:
            a=claim(evidence,text)
            self.assertNotEqual(ra_source_constraints.check(a,evidence),'single_dose_qualifier_not_in_selected_sources',text)
        evidence={'E1':block('Subjects received 45 mg SC injection.')}
        for text in ['대상자는 45 mg을 한 번 투여받았다.','대상자는 45 mg SC를 한 번만 주사받았다.']:
            a=claim(evidence,text)
            self.assertEqual(ra_source_constraints.check(a,evidence),'single_dose_qualifier_not_in_selected_sources',text)

    def test_eligibility_does_not_establish_observed_female_weight_or_bmi(self):
        evidence={'E1':block('The sex criterion was extended to include female subjects with body weight of 60.0-90.0 kg and BMI 19.0-29.9 kg/m2. Eligibility criteria were acceptable.')}
        a=claim(evidence,'실제 대상군에 여성도 포함되었고 여성의 체중은 60.0~90.0 kg, BMI는 19.0~29.9 kg/m²였다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'eligibility_criteria_not_observed_population_range')
        a['text']='선정 기준은 여성도 허용했으며 허용 체중은 60.0~90.0 kg, BMI는 19.0~29.9 kg/m²였다.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))

    def test_qtpp_use_does_not_mean_patient_coadministration(self):
        evidence={'E1':block('Combined use of non-EEA and EEA comparators is acceptable for Quality Target Product Profile development.')}
        a=claim(evidence,'품질 목표제품특성 개발에서 비교약의 병용이 허용된다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'comparator_data_use_not_patient_coadministration')
        a['text']='품질 목표제품특성 개발에서는 비교약을 함께 활용할 수 있다.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))
        a['text']='QTPP 개발에서 비교약 자료를 함께 활용할 수 있으며 병용 투여한다는 의미가 아니다.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))

    def test_label_single_iv_infusion_and_prea_effectiveness_are_not_false_rejections(self):
        evidence={'E1':block('The recommended induction dosage is a single intravenous infusion using the weight-based regimen.')}
        a=claim(evidence,'성인 유도요법은 체중 기반 용량 요법에 따른 단회 정맥주입이다.')
        self.assertIsNone(ra_source_constraints.check(a,evidence))
        evidence['E1']=block('The recommended induction dosage is an intravenous infusion.')
        a=claim(evidence,'성인 유도요법은 체중 기반 용량 요법에 따른 단회 정맥주입이다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'single_dose_qualifier_not_in_selected_sources')
        evidence['E1']=block('An assessment of safety and effectiveness in pediatric patients is required unless waived, deferred, or inapplicable.')
        a=claim(evidence,'소아 환자의 안전성·유효성 평가가 필요하지만 면제·연기·미적용은 예외이다.')
        self.assertIsNone(ra_source_constraints.check(a,evidence))
        evidence['E1']=block('An assessment of safety in pediatric patients is required.')
        a=claim(evidence,'소아 환자의 안전성·유효성 평가가 필요하다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'claim_quality_safety_efficacy_predicate_not_in_selected_sources')

    def test_single_sc_and_tiered_iv_dose_retain_explicit_qualifier(self):
        for own in ['Subjects received a single subcutaneous dose of 90 mg.',
                    'Induction is a single intravenous (IV) tiered dose based on weight.']:
            evidence={'E1':block(own)};a=claim(evidence,'단회 투여이다.')
            self.assertIsNone(ra_source_constraints.check(a,evidence))
        evidence={'E1':block('A single treatment group received multiple subcutaneous doses.')}
        a=claim(evidence,'단회 투여이다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'single_dose_qualifier_not_in_selected_sources')
        from rag.ra_requirements import conditions
        self.assertEqual(conditions('Steqeyma I.V.')['routes'],['IV'])

    def test_month_year_date_does_not_create_a_dose_or_support_wrong_month(self):
        evidence={'E1':block('DRAFT GUIDANCE. October 2025.')}
        a=claim(evidence,'2025년 10월 초안이다.')
        self.assertIsNone(ra_quantities.check(a,evidence))
        a['text']='2025년 11월 초안이다.'
        self.assertIsNotNone(ra_quantities.check(a,evidence))
        self.assertEqual(ra_quantities.quantities('October 2025'),set())
        self.assertNotIn('10',ra_quantities.numbers('October meeting'))

    def test_historical_2015_guidance_and_foreign_footnote_are_not_new_ces_rules(self):
        evidence={'E1':block('In April 2015, the agency published guidance which recommended an assessment of toxicity, human PK and clinical immunogenicity.')}
        a=claim(evidence,'지침은 독성 평가, 약동학 연구 및 임상 면역원성 평가를 고려한다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'historical_guidance_summary_not_current_draft_recommendation')
        a['text']='2015년 당시 지침은 독성 평가, 약동학 연구 및 임상 면역원성 평가를 고려했다.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))
        evidence['E1']=block('See the draft guidance for industry Clinical Immunogenicity Considerations for Biosimilar and Interchangeable Insulin Products (November 2019). When final, this guidance will represent current thinking.')
        evidence['E1']['document_id']='GL:FDA-CES-UPDATED-2025'
        a=claim(evidence,'해당 지침은 최종화될 경우 FDA의 현재 견해를 나타낸다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'foreign_guidance_footnote_not_current_document_status')

    def test_narrow_known_study_dose_request_is_checkable_but_extra_meaning_is_not(self):
        from rag.ra_requirements import fully_checkable_request
        text='EMA SB17-1001 건강인 PK 시험에서 실제 사용한 용량과 투여 경로를 원문으로 확인해줘.'
        self.assertTrue(fully_checkable_request(text))
        for extra in [' 장기 안전성도 확인해줘.',' 추적 기간을 알려줘.',' BMI 선정 기준도 알려줘.']:
            self.assertFalse(fully_checkable_request(text+extra))

    def test_bridge_authority_qualifiers_require_own_words(self):
        evidence={'E1':block('If certain clinical and in vivo non-clinical studies use the non-EEA comparator, data may also include clinical PK and/or PD bridging studies.')}
        a=claim(evidence,'임상 PK 또는 PD 브리징 자료가 추가로 포함될 수도 있다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'clinical_bridging_applicability_not_in_final_clause')
        a['text']='비EEA 비교약을 사용한 경우 임상 PK 또는 PD 브리징 자료가 추가로 포함될 수도 있다.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))
        a['text']='비EEA 비교약은 EMA와 유사한 과학적·규제적 기준을 적용하는 기관의 허가 제품이어야 한다.'
        self.assertEqual(ra_source_constraints.check(a,evidence),'comparator_authority_standards_not_in_selected_sources')

    def test_coexistent_psoriasis_in_arthritis_label_does_not_change_indication(self):
        evidence={'E1':block('For patients with co-existent moderate-to-severe plaque psoriasis weighing more than 100 kg, the recommended dosage is 90 mg.')}
        evidence['E1']['doc_group']='label';evidence['E1']['structure']['section_path']=['2.2 Recommended Dosage in Psoriatic Arthritis']
        a=claim(evidence,'판상 건선에서 체중 100 kg 초과인 환자는 90 mg을 투여한다.')
        self.assertEqual(ra_source_constraints.check(a,evidence),'psoriatic_arthritis_regimen_not_standalone_plaque_psoriasis_regimen')
        a['text']='건선성 관절염에서 중등도·중증 판상 건선을 동반하고 체중 100 kg 초과인 환자는 90 mg을 투여한다.'
        self.assertIsNone(ra_source_constraints.check(a,evidence))

    def test_comparison_name_completion_stays_in_each_own_verified_trial(self):
        evidence={'E1':block('EU Stelara and US Stelara, 45 mg SC.'),
            'E2':block('ABP 654, stekinumab (EU), stekinumab (US); 90 mg SC.',product='ABP654',trial='20190230'),
            'E3':block('Each subject received 90 mg SC of ABP 654, ustekinumab (EU), or ustekinumab (US).',product='ABP654',trial='20190230')}
        a=claim(evidence,'SB17의 대조약은 EU·US Stelara, ABP654의 대조약은 EU·US ustekinumab이다.',('S1','S2'))
        a['products']=['SB17','ABP654'];a['claim_basis']='comparison_of_sources'
        ready,changes=ra_citation_completion.complete([a],evidence)
        self.assertTrue(changes);self.assertIsNone(ra_source_constraints.check(ready[0],evidence))
        evidence['E3']=block(evidence['E3']['text'],product='ABP654',trial='20190232')
        self.assertFalse(ra_citation_completion.complete([a],evidence)[1])


if __name__=='__main__':unittest.main()
