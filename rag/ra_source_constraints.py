"""Narrow observable qualifiers; not a general RA semantic certifier."""
import re
from rag.ra_requirements import ROUTES

VERSION = 'ra-selected-source-qualifiers-3-study-type'


def _check(text, own):
    if (re.search(r'eligib(?:ility|le)|inclusion criteria|sex criterion|at screening', own, re.I)
            and re.search(r'\d.{0,15}(?:kg|kg/m|kg/m²)', text, re.I)
            and re.search(r'실제\s*대상군|대상자는|여성의\s*체중|관찰.{0,10}(?:체중|BMI)', text)
            and not re.search(r'선정\s*기준|선별\s*(?:시|기준)|허용|포함\s*기준|eligib|inclusion criteria', text, re.I)
            and not re.search(r'(?:observed|actual|ranged|mean|median).{0,80}(?:weight|BMI)|(?:weight|BMI).{0,60}(?:ranged|mean|median)', own, re.I|re.S)):
        return 'eligibility_criteria_not_observed_population_range'
    negated_data_use = bool(re.search(r'자료|데이터',text) and re.search(
        r'병용\s*투여한다는\s*의미가\s*아니|병용\s*투여를\s*뜻하는\s*것이\s*아니',text))
    if (re.search(r'Quality Target Product Profile|\bQTPP\b', own, re.I)
            and re.search(r'병용|동시\s*투여|함께\s*투여', text) and not negated_data_use):
        return 'comparator_data_use_not_patient_coadministration'
    if (re.search(r'비\s*EEA',text,re.I) and re.search(r'유사한?\s*과학적?.{0,12}규제적?\s*기준',text)
            and not re.search(r'similar scientific and regulatory standards',own,re.I)):
        return 'comparator_authority_standards_not_in_selected_sources'
    if (re.search(r'clinical\s+PK\s+and/or\s+PD\s+bridging',own,re.I)
            and re.search(r'If certain clinical and in vivo non-clinical studies',own,re.I)
            and re.search(r'임상.{0,25}(?:PK|PD|약동학|약력학)',text,re.I)
            and not (re.search(r'비\s*EEA',text,re.I) and re.search(r'경우|사용한|사용하는',text))):
        return 'clinical_bridging_applicability_not_in_final_clause'
    # These checks cover explicit words with material effects on source meaning.
    # They deliberately do not infer regulatory obligations from source scores.
    sites = re.findall(r'(\d+)\s*(?:개(?:의)?\s*)?(?:시험\s*)?(?:기관|센터)', text)
    if sites:
        from rag.ra_quantities import numbers
        printed_sites = re.findall(r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+'
            r'(?:study\s+)?(?:centres?|centers?|sites?)\b', own, re.I)
        supported_counts = set().union(*(numbers(part) for part in printed_sites)) if printed_sites else set()
        if not set(sites) <= supported_counts:
            return 'study_site_count_not_in_own_product_sources'
    if re.search(r'(?:시험|연구).{0,90}(?:수행|실시|진행)', text):
        # Comparator origin does not establish where a study was conducted.
        conducted = ' '.join(re.findall(r'[^.\n]*(?:conducted|carried out|performed|took place)[^.\n]*', own, re.I))
        location_suffix = r'(?:의)?\s*(?:(?:\d+\s*개\s*)?(?:시험\s*)?(?:기관|센터)\s*)?에서'
        for assertion, source in [('미국'+location_suffix, r'\bUS\b|United States'),
                                  ('프랑스'+location_suffix, r'France'), ('일본'+location_suffix, r'Japan'),
                                  (r'EU\s*내|유럽연합'+location_suffix, r'\bEU\b|European Union')]:
            if re.search(assertion, text, re.I) and not re.search(source, conducted, re.I):
                return 'study_location_not_in_own_product_conduct_sources'
    if (re.search(r'(?:임상|clinical).{0,20}(?:PK|PD)',text,re.I)
            and re.search(r'선택\s*사항|선택적|optional',text,re.I)
            and re.search(r'may also\s+include.{0,120}clinical|case.by.case',own,re.I|re.S)
            and not re.search(r'수\s*있|사례별|제품.{0,6}유형|필요|조건|경우|may|case.by.case',text,re.I)):
        return 'case_specific_clinical_bridge_not_unconditional_option'
    if (re.search(r'clinical\s+PK\s+and/or\s+PD\s+bridging',own,re.I)
            and re.search(r'may\s+also\s+include\s+data\s+from\s+clinical',own,re.I)
            and re.search(r'(?:임상|clinical).{0,20}(?:PK|PD)',text,re.I)
            and re.search(r'필수|의무|포함해야|포함하여야|필요하다|required|must',text,re.I)
            and not re.search(r'수\s*있|가능|경우|조건|사례별|may|optional|case.by.case',text,re.I)):
        return 'optional_clinical_bridging_strengthened_to_requirement'
    if (re.search(r'약리학적\s*(?:동등|유사)', text)
            and re.search(r'pharmacokinetic|\bPK\s+similarity', own, re.I)
            and not re.search(r'pharmacolog(?:ical|ic)', own, re.I)):
        return 'pk_similarity_not_pharmacological_equivalence'
    if (re.search(r'(?<![A-Za-z])analytes?(?![A-Za-z])|분석\s*대상|측정\s*대상', text, re.I)
            and not re.search(r'\banalytes?\b|concentration|\bADA\b|\bNAb\b|anti[- ]drug|neutralizing|neutralising|assay|분석\s*대상|측정\s*대상', own, re.I)):
        return 'assay_target_not_in_selected_sources'
    if ((re.search(r'\d+\s*명.{0,35}(?:모집|등록|참여)', text)
            or re.search(r'무작위\s*배정(?:되었|하였다)', text))
            and re.search(r'planned to be conducted|subjects were to be enrolled', own, re.I)
            and not re.search(r'계획|예정|목표', text)):
        # A planned population is not an observed enrolled population. An
        # explicitly quoted actual-enrollment sentence can support the latter.
        from rag.ra_quantities import numbers
        actual = re.findall(r'[^.\n]*(?<!be )\b(?:were enrolled|were recruited|were randomised|were randomized)\b[^.\n]*', own, re.I)
        asserted = re.findall(r'(\d+)\s*명', text)
        if not asserted and not actual:
            return 'planned_design_not_observed_conduct'
        if any(value not in numbers(' '.join(actual)) for value in asserted):
            return 'planned_population_not_actual_enrollment'
    for route, pattern in ROUTES.items():
        if re.search(pattern, text, re.I) and not re.search(pattern, own, re.I):
            return 'claim_route_not_in_selected_sources'
    if re.search(r'건강(?:한|인)|healthy', text, re.I) and not re.search(r'healthy|건강(?:한|인)', own, re.I):
        return 'claim_population_not_in_selected_sources'
    if (re.search(r'(?:\bPK\b|약동학(?:적)?|pharmacokinetic)\s*(?:시험|연구|stud(?:y|ies))', text, re.I)
            and not re.search(r'\bPK\b|pharmacokinetic|약동학', own, re.I)):
        return 'claim_pk_study_not_in_selected_sources'
    if re.search(r'(?<![A-Za-z])(?:non[- ]?)?EEA(?![A-Za-z])', text, re.I) and not re.search(r'\bEEA\b', own, re.I):
        return 'claim_comparator_region_not_in_selected_sources'
    if (re.search(r'유사성|유사(?:해야|하여야)|similarity', text, re.I) and re.search(r'representative', own, re.I)
            and not re.search(r'\bsimilarity\b|\bsimilar\s+(?:biological|to|medicinal)|유사', own, re.I)):
        return 'representativeness_not_biological_similarity'
    if re.search(r'입증|유사|비교|검증|분석|요구|필수|평가|적용', text):
        # 유효성분 (active substance) in a document title is not an efficacy
        # assertion. Keep checking actual 유효성/효능 predicates.
        for asserted, printed in [(r'품질', r'quality'), (r'안전성', r'safety'), (r'효능|유효성(?!분)', r'efficacy|effectiveness')]:
            if re.search(asserted, text, re.I) and not re.search(printed+'|'+asserted, own, re.I):
                return 'claim_quality_safety_efficacy_predicate_not_in_selected_sources'
    if (re.search(r'PK\s*분석.{0,25}(?:동안|진행)', text, re.I)
            and re.search(r'PK sampling period', own, re.I)
            and not re.search(r'샘플|채혈|혈액|관찰|추적|평가', text)):
        return 'sampling_period_not_analysis_duration'
    if (re.search(r'(?:시험|연구).{0,40}동안.{0,35}(?:진행|수행)',text)
            and re.search(r'\bfollowed\b',own,re.I)
            and not re.search(r'추적|관찰|투여\s*후|주사\s*후|follow',text,re.I)):
        return 'follow_up_period_not_total_study_duration'
    if (re.search(r'과학적\s*지식', text) and re.search(r'provided that', own, re.I)
            and not re.search(r'유사|입증|조건|경우', text)):
        return 'selected_scientific_knowledge_condition_not_preserved'
    if (re.search(r'브리징|연관성|연결|bridge|relevance', text, re.I)
            and re.search(r'If certain clinical and in vivo non-clinical studies', own, re.I)
            and not re.search(r'임상|비임상|clinical|in vivo|PK|PD', text, re.I)):
        return 'selected_clinical_bridge_condition_not_preserved'
    # "한 번" is a dose qualifier only next to an administration word; "최소 한 번 양성"
    # or "한 번 이상" counts test results, not doses.
    if re.search(r'단일\s*(?:용량|투여|주사|SC)|단회|한\s*번(?:만)?\s*(?:의\s*)?(?:투여|주사|주입|접종|용량)'
                 r'|(?:투여|주사|주입|접종)\S*\s*한\s*번(?!\s*(?:이상|도))|single[- ]dose', text, re.I):
        if not re.search(r'\bsingle(?:[- ]dose|\s+(?:dose|injection|SC|(?:subcutaneous\s+|intravenous(?:\s*\(IV\))?\s+(?:tiered\s+)?)?(?:dose|infusion)))|단회|단일\s*(?:용량|투여)', own, re.I):
            return 'single_dose_qualifier_not_in_selected_sources'
    if re.search(r'대조약|comparator', text, re.I) and re.search(r'승인|허가|라이선스|라이센스|authori[sz]ed|approved|licensed', text, re.I):
        if not re.search(r'authori[sz]ed|approved|licensed|승인|허가', own, re.I):
            return 'comparator_approval_not_in_selected_sources'
    if (re.search(r'대조약|comparator',text,re.I) and re.search(r'ustekinumab|Stelara|스텔라라',text,re.I)
            and not re.search(r'ustekinumab|Stelara|스텔라라',own,re.I)):
        return 'comparator_identity_not_in_selected_sources'
    if (re.search(r'함께|병용|병합|혼합|결합|combined', text, re.I) and re.search(r'사용|허용|acceptable|use', text, re.I)
            and re.search(r'Quality Target Product Profile|\bQTPP\b', own, re.I)
            and not re.search(r'Quality Target Product Profile|\bQTPP\b|품질\s*목표|목표\s*품질', text, re.I)):
        return 'selected_permission_scope_not_preserved'
    return None


def product_name_without_presentation(text, products):
    """'Wezlana I.V.' / 'Steqeyma I.V.' name a product presentation, not a route assertion."""
    from rag import study_registry
    for product in products:
        for name in [product, *study_registry.products().get(product, {}).get('names', [])]:
            base = re.sub(r'\s*\(.*\)$', '', name).strip()
            text = re.sub(r'(?<![A-Za-z0-9])(' + re.escape(base) + r')\s+I\.\s?V\.', r'\1', text, flags=re.I)
    return text


def check(claim, evidence):
    text = product_name_without_presentation(claim['text'], claim.get('products', []))
    own='\n'.join(s['quote'] for s in claim['sources'])
    if (re.search(r'독성\s*평가',text) and re.search(r'약동학|\bPK\b',text,re.I) and re.search(r'면역원성',text)
            and re.search(r'In April 2015.*?guidance.*?recommended',own,re.I|re.S)
            and not re.search(r'2015|당시|과거|이전\s*지침',text)):
        return 'historical_guidance_summary_not_current_draft_recommendation'
    if (re.search(r'최종화|최종본|when final',text,re.I) and re.search(r'견해|생각|thinking',text,re.I)
            and re.search(r'draft guidance for industry\s+Clinical Immunogenicity Considerations for Biosimilar and Interchangeable Insulin Products.*?When final',own,re.I|re.S)
            and any(evidence[s['evidence']].get('document_id')=='GL:FDA-CES-UPDATED-2025' for s in claim['sources'])
            and not re.search(r'insulin|인슐린',text,re.I)):
        return 'foreign_guidance_footnote_not_current_document_status'
    if re.search(r'판상\s*건선|plaque psoriasis',text,re.I) and re.search(r'용량|mg|투여',text,re.I) and not re.search(r'건선성\s*관절염|psoriatic arthritis',text,re.I):
        for source in claim['sources']:
            block=evidence[source['evidence']]
            if block.get('doc_group')=='label' and re.search(r'Psoriatic Arthritis',
                    ' '.join(block.get('structure',{}).get('section_path',[])),re.I):
                return 'psoriatic_arthritis_regimen_not_standalone_plaque_psoriasis_regimen'
    if len(claim['products']) < 2:
        return _check(text, '\n'.join(s['quote'] for s in claim['sources']))
    from rag import study_registry, ra_quantities
    mentions = set()
    for product in claim['products']:
        aliases = [product, *study_registry.products().get(product, {}).get('names', [])]
        for alias in aliases:
            for match in re.finditer(r'(?<![A-Za-z0-9])' + re.escape(alias) + r'(?![A-Za-z0-9])', text, re.I):
                mentions.add((match.start(), match.end(), product))
    if {p for _, _, p in mentions} != set(claim['products']):
        return 'comparison_products_not_explicit_in_final_clause'
    shared = bool(re.search(r'모두|둘\s*다|both|each', text, re.I))
    ordered = sorted(mentions)
    for n, (start, end, product) in enumerate(ordered):
        stop = next((a for a, _, p in ordered[n + 1:] if p != product), len(text))
        subject = text if shared else text[start:stop]
        own = '\n'.join(s['quote'] for s in claim['sources'] if evidence[s['evidence']].get('product_id') == product)
        if shared and not ra_quantities.quantities(subject) <= ra_quantities.quantities(own):
            return 'shared_comparison_quantity_not_in_each_product_source'
        reason = _check(subject, own)
        if reason:
            return reason
    return None
