import unittest
from rag.contract_relations import pairs, ranges, scope, table_fact, relation_condition, attach_comparison
from rag.grounded_table_graph import build
from rag.contract_match import condition


class ContractRelationsTests(unittest.TestCase):
    def test_printed_ratio_direction_is_preserved_and_reverse_is_rejected(self):
        f = {"relationship_status": "structural_candidate", "explicit_relations": pairs("Ratio of SB17 to EU-approved Stelara")}
        self.assertEqual(relation_condition(f, "comparison", "ordered_pair", ["SB17", "EU-Stelara"])["status"], "match")
        self.assertEqual(relation_condition(f, "comparison", "ordered_pair", ["EU-Stelara", "SB17"])["status"], "mismatch")

    def test_vs_is_not_proof_of_numerator_direction(self):
        f = {"relationship_status": "structural_candidate", "explicit_relations": pairs("SB17 vs EU-Stelara")}
        self.assertEqual(relation_condition(f, "comparison", "ordered_pair", ["SB17", "EU-Stelara"])["status"], "unknown")
        self.assertEqual(relation_condition(f, "comparison", "unordered_pair", ["EU-Stelara", "SB17"])["status"], "match")

    def test_unit_slash_is_not_a_treatment_comparison(self):
        self.assertFalse(pairs("mL/kg and mcg/mL"))

    def test_weight_ranges_remain_subgroups_not_covariates(self):
        self.assertFalse(ranges("body weight as a covariate"))
        self.assertEqual(ranges("Body weight 70 to 110 kg")[0]["values"], ["70", "110"])
        self.assertEqual(ranges("체중 70~110 kg")[0]["values"], ["70", "110"])

    def test_weight_interval_mismatch_is_not_partial_overlap(self):
        f = {"relationship_status": "structural_candidate", "explicit_relations": ranges("Body Weight 70 to 110 kg")}
        self.assertEqual(relation_condition(f, "weight", "range", ["70", "110"])["status"], "match")
        self.assertEqual(relation_condition(f, "weight", "range", ["60", "110"])["status"], "mismatch")

    def test_component_design_bridge_does_not_assign_parent_counts(self):
        text = "Study CT-P43 1.1 was randomized and double-blind in two parts. Part 2 received one dose."
        requested = ["ST_CTP43_1_1_P2"]
        self.assertEqual(scope(text, "CTP43", requested, "design")["requested"][0]["status"], "explicit_candidate")
        self.assertEqual(scope(text, "CTP43", requested, "n_subjects")["requested"][0]["status"], "unknown")

    def test_parent_without_printed_part_or_with_gap_is_unresolved(self):
        for text in ["Study CT-P43 1.1 was randomized.", "Study CT-P43 1.1 was randomized. [UNSUPPLIED SOURCE GAP] Part 2 received one dose."]:
            self.assertEqual(scope(text, "CTP43", ["ST_CTP43_1_1_P2"], "design")["requested"][0]["status"], "unknown")

    def test_programme_assay_is_related_not_certified_for_part(self):
        text = "Methods applied during clinical development included ADA and NAb assays using MSD-ECL."
        self.assertEqual(scope(text, "CTP43", ["ST_CTP43_1_1_P2"], "assay")["requested"][0]["status"], "related")

    def test_foreign_trial_does_not_inherit_requested_trial(self):
        self.assertEqual(scope("Study CT-P43 3.1 was double-blind.", "CTP43", ["ST_CTP43_1_1_P2"], "design")["requested"][0]["status"], "mismatch")

    def test_heading_metadata_alone_is_not_scope_proof(self):
        out = scope("The count was 25.", "CTP43", ["ST_CTP43_1_1_P2"], "n_subjects", [{"title": "CT-P43 1.1 Part 2"}])
        self.assertEqual(out["requested"][0]["status"], "unknown")
        self.assertTrue(out["heading_candidates"])

    def test_marked_local_ratio_note_is_linked_but_never_across_gap(self):
        table = '<table><tr><th>Endpoint</th><th>Ratio<sup>1</sup></th></tr><tr><td>Cmax</td><td>103.2</td></tr></table>'
        for separator, expected in [("\n", "match"), ("\n[UNSUPPLIED SOURCE GAP]\n", "unknown")]:
            text = table + separator + "1. Ratio of geometric means: SB17/EU-Stelara"
            e = {"E1": {"text": text, "product_id": "SB17"}}
            g = build(e["E1"]); node = next(n for n in g["tables"][0]["cells"] if n["value"] == "103.2")
            f = table_fact({"evidence": "E1", "start": node["source_range"][0], "end": node["source_range"][1]}, e, graph=g)
            self.assertEqual(relation_condition(f, "comparison", "ordered_pair", ["SB17", "EU-Stelara"])["status"], expected)
            for anchor in f["relation_anchors"]:
                a, b = anchor["source_range"]; self.assertEqual(text[a:b], anchor["quote"])
            self.assertFalse(f["semantic_approval"])

    def test_unmarked_unrelated_note_does_not_supply_comparison(self):
        text = '<table><tr><th>Endpoint</th><th>Mean</th></tr><tr><td>Cmax</td><td>2.4</td></tr></table>\n2. Ratio of SB17/EU-Stelara'
        e = {"E1": {"text": text, "product_id": "SB17"}}; g = build(e["E1"])
        node = next(n for n in g["tables"][0]["cells"] if n["value"] == "2.4")
        f = table_fact({"evidence": "E1", "start": node["source_range"][0], "end": node["source_range"][1]}, e, graph=g)
        self.assertFalse(f["explicit_relations"])

    def test_wrong_footnote_number_is_not_replaced_by_proximity(self):
        text = '<table><tr><th>Endpoint</th><th>Ratio<sup>1</sup></th></tr><tr><td>Cmax</td><td>103.2</td></tr></table>\n2. Ratio of geometric means: SB17/EU-Stelara'
        e = {"E1": {"text": text, "product_id": "SB17"}}; g = build(e["E1"])
        node = next(n for n in g["tables"][0]["cells"] if n["value"] == "103.2")
        f = table_fact({"evidence": "E1", "start": node["source_range"][0], "end": node["source_range"][1]}, e, graph=g)
        self.assertFalse(f["explicit_relations"])

    def fact(self, text, value):
        e = {"E1": {"text": text, "product_id": "SB17"}}; g = build(e["E1"])
        node = next(n for n in g["tables"][0]["cells"] if n["value"] == value)
        return table_fact({"evidence": "E1", "start": node["source_range"][0], "end": node["source_range"][1]}, e, graph=g)

    def test_ci_ratio_group_requires_matching_marker_and_explicit_ci_statement(self):
        for marker, note, status in [("1", "90% CIs for the ratios", "match"),
                                     ("2", "90% CIs for the ratios", "unknown"),
                                     ("1", "90% CIs for the means", "unknown")]:
            text = '<table><tr><th>Endpoint</th><th>Ratio<sup>1</sup></th><th>90% CI<sup>'+marker+'</sup></th></tr><tr><td>Cmax</td><td>1.1</td><td>(0.9, 1.3)</td></tr></table>\nRatios of geometric means (only SB17/EU-Stelara) and '+note
            f = self.fact(text, "(0.9, 1.3)")
            self.assertEqual(relation_condition(f, "comparison", "ordered_pair", ["SB17", "EU-Stelara"])["status"], status)
            self.assertEqual(condition(f, "statistic", "eq", ["CI90"])["status"], "match")
            self.assertFalse(any(a["value"] == "GMR" for a in f["observations"]))

    def test_ratio_legend_direction_and_named_ci_not_row_order(self):
        text = '<table><tr><th>Endpoint</th><th>Geometric LSMean</th><th>Ratio B/A</th><th>90% CI of Ratio</th></tr><tr><td>Cmax (ng/mL)</td><td>20</td><td>0.9</td><td>(0.8, 1.0)</td></tr></table>\nA = SB17; B = EU Stelara'
        for value in ["0.9", "(0.8, 1.0)"]:
            f = self.fact(text, value)
            self.assertEqual(relation_condition(f, "comparison", "ordered_pair", ["EU-Stelara", "SB17"])["status"], "match")
            self.assertEqual(relation_condition(f, "comparison", "ordered_pair", ["SB17", "EU-Stelara"])["status"], "mismatch")
            self.assertTrue(f["ratio_statistic"])
        self.assertEqual(condition(self.fact(text,"0.9"),"statistic","eq",["GMR"])["status"],"match")

    def test_missing_or_conflicting_ratio_legend_never_guessed(self):
        table = '<table><tr><th>Endpoint</th><th>Ratio A/B</th></tr><tr><td>Cmax</td><td>1.2</td></tr></table>'
        for note in ["A = SB17", "A = SB17; A = EU Stelara; B = US Stelara", "[UNSUPPLIED SOURCE GAP]\nA = SB17; B = EU Stelara"]:
            self.assertEqual(relation_condition(self.fact(table+'\n'+note,"1.2"),"comparison","ordered_pair",["SB17","EU-Stelara"])["status"],"unknown")

    def test_generic_reference_binds_only_unique_own_row(self):
        for refs, expected in [("<td>US Stelara</td>","match"), ("<td>US Stelara</td><td>EU Stelara</td>","unknown")]:
            text = '<table><tr><th>Endpoint</th><th>Treatment</th><th>Ratio of SB17/Stelara</th></tr><tr><td>Cmax</td>'+refs+'<td>1.1</td></tr></table>'
            self.assertEqual(relation_condition(self.fact(text,"1.1"),"comparison","ordered_pair",["SB17","US-Stelara"])["status"],expected)

    def test_arithmetic_mean_table_does_not_certify_gmr(self):
        text = '<table><tr><th>Endpoint</th><th>Arithmetic Mean</th><th>Ratio A/B</th></tr><tr><td>Cmax</td><td>20</td><td>1.1</td></tr></table>\nA = SB17; B = EU Stelara'
        self.assertEqual(condition(self.fact(text,"1.1"),"statistic","eq",["GMR"])["status"],"unknown")

    def grouped_ratios(self, marker='1', reference='EU-Stelara', role='Reference', note=None):
        table = '<table><tr><th>Endpoint</th><th>Treatment</th><th>Type</th><th>Geometric LS Mean</th><th>Ratio of Geometric LS Means 1</th><th>90% CI '+marker+'</th></tr>'
        table += '<tr><td rowspan="2">Cmax</td><td>SB17</td><td>Test</td><td>70.5</td><td rowspan="2">1.37</td><td rowspan="2">(1.08, 1.62)</td></tr>'
        table += '<tr><td>'+reference+'</td><td>'+role+'</td><td>51.4</td></tr></table>'
        return table+'\n'+(note or '1 Ratios of geometric LS means (SB17/EU-Stelara, SB17/US-Stelara, EU-Stelara/US-Stelara) and 90% CIs for the ratios.')

    def test_merged_result_matches_only_own_complete_test_reference_group(self):
        for ref in ['EU-Stelara','US-Stelara']:
            for value in ['1.37','(1.08, 1.62)']:
                f=self.fact(self.grouped_ratios(reference=ref),value)
                own=[p for p in f['explicit_relations'] if p['dimension']=='comparison']
                self.assertEqual([p['values'] for p in own],[['SB17',ref]])
                self.assertEqual(len(own[0]['row_group_node_ids']),4)
                self.assertEqual(relation_condition(f,'comparison','ordered_pair',['SB17',ref])['status'],'match')
                self.assertEqual(relation_condition(f,'comparison','ordered_pair',[ref,'SB17'])['status'],'mismatch')

    def test_multi_pair_note_cannot_bind_missing_unknown_or_duplicated_roles(self):
        for ref,role in [('unknown treatment','Reference'),('EU-Stelara','unknown'),('EU-Stelara','Test')]:
            f=self.fact(self.grouped_ratios(reference=ref,role=role),'1.37')
            self.assertFalse(f['explicit_relations'])

    def test_unique_note_contradicting_printed_group_is_not_applicable(self):
        note='1 Ratio of geometric LS means SB17/US-Stelara and 90% CIs for the ratios.'
        for value in ['1.37','(1.08, 1.62)']:
            self.assertFalse(self.fact(self.grouped_ratios(note=note),value)['explicit_relations'])

    def test_numbered_ci_note_requires_matching_marker_and_ratio_ci_statement(self):
        for marker,note in [('2',None),('1','1 Ratio of geometric means SB17/EU-Stelara and 90% CIs for the means.')]:
            self.assertFalse(self.fact(self.grouped_ratios(marker=marker,note=note),'(1.08, 1.62)')['explicit_relations'])

    def test_own_pair_binding_preserves_original_note_and_does_not_display_other_pairs(self):
        text=self.grouped_ratios();e={'E1':{'text':text,'product_id':'SB17'}};g=build(e['E1'])
        n=next(n for n in g['tables'][0]['cells'] if n['value']=='1.37')
        c={'evidence':'E1','start':n['source_range'][0],'end':n['source_range'][1]}
        item={'conditions':[],'source_label':'Wrong AUCinf model label'};attach_comparison(item,c,e,g)
        self.assertEqual(item['own_comparison_binding']['pairs'],[['SB17','EU-Stelara']])
        self.assertIn('SB17/EU-Stelara',[x['quote'] for x in item['conditions']])
        self.assertFalse(any('SB17/US-Stelara' in x['quote'] for x in item['conditions']))
        self.assertTrue(all(x['quote'] in text for x in item['conditions']))
        f=table_fact(c,e,graph=g)
        self.assertIn('SB17/US-Stelara',f['relation_anchors'][0]['quote'])
        self.assertFalse(item['own_comparison_binding']['semantic_approval'])
        self.assertEqual(item['model_source_label'],'Wrong AUCinf model label')
        self.assertIn('Cmax',item['source_label'])
        self.assertTrue(item['own_comparison_binding']['source_node_proof'])
        self.assertFalse(any(x['value'] in ('Test','Reference') for x in item['conditions']))

    def test_ratio_label_and_endpoint_cannot_retain_model_label_from_other_row(self):
        text=self.grouped_ratios();e={'E1':{'text':text,'product_id':'SB17'}};g=build(e['E1'])
        n=next(n for n in g['tables'][0]['cells'] if n['value']=='1.37')
        c={'evidence':'E1','start':n['source_range'][0],'end':n['source_range'][1]}
        item={'conditions':[{'dimension':'endpoint','value':'AUCinf','quote':'foreign header','evidence':'E1'},
            {'dimension':'comparison','value':'SB17/US-Stelara','quote':'foreign pair','evidence':'E1'},
            {'dimension':'statistic','value':'Median','quote':'foreign statistic','evidence':'E1'}],'source_label':'AUCinf US ratio','value':'1.37','item':'AUCinf US median'}
        attach_comparison(item,c,e,g)
        self.assertEqual(item['value'],'1.37')
        self.assertIn('Cmax',item['source_label'])
        self.assertEqual(item['item'],item['source_label'])
        self.assertEqual(item['model_item'],'AUCinf US median')
        self.assertFalse(any('foreign' in x['quote'] for x in item['conditions']))
        self.assertEqual(len(item['removed_unbound_ratio_conditions']),3)
        self.assertTrue(all(x['quote'] in text for x in item['conditions']))

    def compound_ratio_table(self,heading='Ratio of LS Geometric Means (90% CI)',value='1.47 (1.19, 1.83)'):
        return '<table><tr><th>Parameter</th><th>Geometric Mean</th><th>Geometric Mean</th></tr><tr><td>Cmax</td><td>31.8</td><td>32.7</td></tr><tr><td colspan="3">'+heading+'</td></tr><tr><td>Cmax</td><td>'+value+'</td><td>1.28 (1.04, 1.56)</td></tr><tr><td colspan="3">Arithmetic Mean</td></tr><tr><td>Cmax</td><td>72.4</td><td>73.1</td></tr></table>'

    def test_compound_gmr_ci_requires_printed_header_and_whole_cell(self):
        from rag.contract_terms import atoms
        for heading in ['Ratio of LS Geometric Means (90% CI)','Ratio of geometric least squares means (90% CI)']:
            f=self.fact(self.compound_ratio_table(heading=heading),'1.47 (1.19, 1.83)')
            for stat in ['GMR','CI90']:
                self.assertEqual(condition(f,'statistic','eq',[stat])['status'],'match')
            self.assertEqual(condition(f,'statistic','eq',['mean'])['status'],'mismatch')
            self.assertFalse(any(a['value']=='mean' for a in atoms(heading)))
            self.assertFalse(any(a['value']=='mean' for a in atoms('LS Geometric Mean')))
            self.assertFalse(f['compound_statistic_binding']['semantic_approval'])

    def test_bundle_cannot_infer_confidence_level_or_ci_from_mean_sd(self):
        for heading in ['Ratio of Geometric Means (CI)','Mean (SD)','Ratio of Geometric Means (95% CI)']:
            f=self.fact(self.compound_ratio_table(heading=heading),'1.47 (1.19, 1.83)')
            self.assertNotIn('compound_statistic_binding',f)
            self.assertNotEqual(condition(f,'statistic','eq',['CI90'])['status'],'match')

    def test_compound_subcell_and_incomplete_bounds_are_unresolved(self):
        text=self.compound_ratio_table();e={'E1':{'text':text,'product_id':'SB17'}};g=build(e['E1']);a=text.index('1.19')
        f=table_fact({'evidence':'E1','start':a,'end':a+4},e,graph=g)
        self.assertNotIn('compound_statistic_binding',f)
        self.assertEqual(condition(f,'statistic','eq',['GMR'])['status'],'unknown')
        f=self.fact(self.compound_ratio_table(value='1.47 (1.19, missing)'),'1.47 (1.19, missing)')
        self.assertNotIn('compound_statistic_binding',f)

    def test_closed_statistic_block_keeps_endpoint_and_stops_at_next_heading(self):
        f=self.fact(self.compound_ratio_table(),'72.4')
        self.assertEqual(condition(f,'statistic','eq',['mean'])['status'],'match')
        self.assertEqual(condition(f,'statistic','eq',['GMR'])['status'],'mismatch')
        self.assertEqual(condition(f,'endpoint','eq',['Cmax'])['status'],'match')
        f=self.fact(self.compound_ratio_table(),'31.8')
        self.assertNotIn('compound_statistic_binding',f)
        self.assertEqual(condition(f,'statistic','eq',['mean'])['status'],'unknown')

    def test_compound_endpoint_unit_keeps_mass_prefix(self):
        from rag.contract_terms import atoms
        self.assertEqual([a["value"] for a in atoms("AUCinf (ng·h/mL)") if a["dimension"] == "unit"], ["ng·h/ml"])

    def test_ad_hoc_and_post_hoc_timing_are_not_silently_equated(self):
        f = {"relationship_status":"structural_candidate","observations":[{"dimension":"analysis_timing","value":"ad_hoc"}]}
        self.assertEqual(condition(f,"analysis_timing","eq",["post_hoc"])["status"],"unknown")


if __name__ == "__main__":
    unittest.main()
