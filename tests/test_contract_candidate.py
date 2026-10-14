import unittest
from rag.contract_candidate import inventory, validate_selection
from rag.request_contract import ground
from tests.test_request_contract import term, cond, proposal


def interpretation(question, requirements, **updates):
    u = {"studies": [], "topic": "pk_results", "english_query": question, "products": ["ABP654"]}
    u.update(updates)
    return {"understanding": u, "requirements": ground(question, requirements)}


class ContractCandidateTests(unittest.TestCase):
    def test_foreign_row_rejected_while_four_requested_values_and_units_survive(self):
        q = "tmax 中央値"  # Request obligations below are anchored only to tmax.
        req = proposal(term("endpoint", "tmax"))
        inter = interpretation(q, req)
        text = '<table><tr><th>Endpoint</th><th>Median</th><th>Mean</th></tr><tr><td>tmax (day)</td><td>13.5</td><td>15.3</td></tr><tr><td>tmax (day)</td><td>7.0</td><td>9.9</td></tr><tr><td>Vz/F (mL/kg)</td><td>160.5</td><td>178.7</td></tr></table>'
        evidence = {"E1": {"text": text, "product_id": "ABP654"}}; unit = {"product": "ABP654"}
        candidates, audits = inventory(q, inter, unit, evidence)
        selected = [c for c in candidates.values() if c["kind"] == "table_cell" and c["value"] in ["13.5", "15.3", "7.0", "9.9", "160.5"]]
        raw = {"status": "answered", "premise_source_ids": [], "items": [{"study": "none", "item": "pk", "source_label": "tmax", "value_id": c["id"], "exact": True, "conditions": []} for c in selected]}
        result = validate_selection(raw, candidates, audits, evidence, inter, q)
        self.assertEqual(len(result["items"]), 4); self.assertEqual(len(result["rejected_items"]), 1)
        self.assertTrue(all(i["printed_units"] == ["day"] for i in result["items"]))

    def test_unknown_programme_assay_is_retained_as_related_evidence(self):
        q = "분석법"; inter = interpretation(q, proposal(term("attribute", "assay", "분석법")),
            studies=["ST_CTP43_1_1_P2"], topic="assay", products=["CTP43"])
        evidence = {"E1": {"text": "Methods applied during clinical development included ADA assays using MSD-ECL.", "product_id": "CTP43"}}
        candidates, audits = inventory(q, inter, {"product": "CTP43"}, evidence)
        c = next(c for c in candidates.values() if c["kind"] == "source_paragraph")
        raw = {"status": "answered", "premise_source_ids": [], "items": [{"study": "none", "item": "assay", "source_label": "분석법", "value_id": c["id"], "exact": True, "conditions": []}]}
        result = validate_selection(raw, candidates, audits, evidence, inter, q)
        self.assertFalse(result["items"]); self.assertEqual(len(result["related_items"]), 1)
        self.assertEqual(result["status"], "partial"); self.assertIn("MSD-ECL", result["answer_ko"])
        self.assertFalse(result["corpus_absence_claimed"])

    def test_requested_part_design_is_not_withheld_for_parent_label(self):
        q = "무작위배정 눈가림"; requirements = proposal(term("attribute", "randomization", "무작위배정"))
        requirements["requests"].append({"id": "r2", "field": term("attribute", "blinding", "눈가림"), "conditions": []})
        inter = interpretation(q, requirements, studies=["ST_CTP43_1_1_P2"], topic="design", products=["CTP43"])
        evidence = {"E1": {"text": "Study CT-P43 1.1 was randomized and double-blind in two parts. In Part 2, subjects received a single dose.", "product_id": "CTP43"}}
        candidates, audits = inventory(q, inter, {"product": "CTP43"}, evidence)
        c = next(c for c in candidates.values() if c["kind"] == "source_paragraph")
        self.assertEqual(audits[c["id"]]["status"], "supported")
        raw = {"status": "answered", "premise_source_ids": [], "items": [{"study": "ST_CTP43_1_1", "item": "design", "source_label": "설계", "value_id": c["id"], "exact": True, "conditions": []}]}
        self.assertEqual(validate_selection(raw, candidates, audits, evidence, inter, q)["status"], "answered")

    def test_assay_scope_uses_request_field_when_coarse_topic_is_immunogenicity(self):
        q = "분석법"; inter = interpretation(q, proposal(term("attribute", "assay", "분석법")),
            studies=["ST_CTP43_1_1_P2"], topic="immunogenicity", products=["CTP43"])
        evidence = {"E1": {"text": "Methods applied during clinical development included ADA assays using MSD-ECL.", "product_id": "CTP43"}}
        candidates, audits = inventory(q, inter, {"product": "CTP43"}, evidence)
        a = next(a for k,a in audits.items() if candidates[k]["kind"] == "source_paragraph")
        self.assertEqual(a["fact"]["scope_audit"]["requested"][0]["status"],"related")
        self.assertEqual(a["status"],"unknown")

    def test_ratio_has_no_endpoint_physical_unit_and_exact_is_computed(self):
        q = "Cmax GMR"; inter = interpretation(q, proposal(term("statistic","GMR"),[{
            "dimension":"endpoint","operator":"eq","values":[{"value":"Cmax","anchor":{"quote":"Cmax","occurrence":0}}]}]))
        evidence = {"E1":{"text":'<table><tr><th>Endpoint</th><th>GMR</th></tr><tr><td>Cmax (ng/mL)</td><td>1.05</td></tr></table>'}}
        candidates,audits=inventory(q,inter,{},evidence)
        c=next(c for c in candidates.values() if c["value"]=="1.05")
        # Explicit GMR also denotes a ratio even if the word Ratio isn't printed.
        raw={"status":"partial","premise_source_ids":[],"items":[{"study":"none","item":"GMR","source_label":"GMR","value_id":c["id"],"exact":False,"conditions":[]}]}
        result=validate_selection(raw,candidates,audits,evidence,inter,q)
        self.assertEqual(result["status"],"answered")
        self.assertEqual(result["items"][0]["printed_units"],[])
        self.assertEqual(result["items"][0]["endpoint_units"],["ng/ml"])

    def test_unverified_question_unit_does_not_reject_correct_source_unit(self):
        q="tmax 단위"; requirements=proposal(term("endpoint","tmax"),[{
            "dimension":"unit","operator":"eq","values":[{"value":"unit","anchor":{"quote":"단위","occurrence":0}}]}])
        inter=interpretation(q,requirements)
        evidence={"E1":{"text":'<table><tr><th>Endpoint</th><th>Mean</th></tr><tr><td>tmax (day)</td><td>12</td></tr></table>'}}
        candidates,audits=inventory(q,inter,{},evidence)
        cid=next(cid for cid,c in candidates.items() if c["value"]=="12")
        self.assertEqual(audits[cid]["status"],"unknown")
        raw={"status":"answered","premise_source_ids":[],"items":[{"study":"none","item":"tmax","source_label":"tmax","value_id":cid,"exact":True,"conditions":[]}]}
        out=validate_selection(raw,candidates,audits,evidence,inter,q)
        self.assertEqual(len(out["related_items"]),1);self.assertFalse(out["rejected_items"])

    def test_partial_hints_preserve_positive_AND_progress_without_certifying_timing(self):
        from rag.contract_candidate import relationship_hints
        audits={"a":{"status":"unknown","requests":[{"request_id":"r","status":"unknown","checks":[
            {"dimension":"statistic","status":"match"},{"dimension":"covariate","status":"match"},
            {"dimension":"analysis_set","status":"match"},{"dimension":"analysis_timing","status":"unknown"}]}]},
            "b":{"status":"unknown","requests":[{"request_id":"r","status":"unknown","checks":[
            {"dimension":"statistic","status":"match"},{"dimension":"covariate","status":"match"},
            {"dimension":"analysis_set","status":"unknown"},{"dimension":"analysis_timing","status":"unknown"}]}]}}
        hints=relationship_hints(audits)
        self.assertEqual([h["status"] for h in hints],["unknown","unknown"])
        self.assertIn("analysis_set",hints[0]["requests"][0]["matched_dimensions"])
        self.assertIn("analysis_set",hints[1]["requests"][0]["unresolved_dimensions"])

    def test_hint_does_not_promote_unverified_field_or_clear_contradiction(self):
        from rag.contract_candidate import relationship_hints
        audits={"a":{"status":"unknown","requests":[{"request_id":"r","status":"unknown","checks":[{"dimension":"statistic","status":"unknown"}]}]},
                "b":{"status":"mismatch","requests":[]}}
        self.assertEqual(relationship_hints(audits),[{"source_id":"b","status":"mismatch","requests":[]}])

    def test_stale_parent_summary_cannot_override_an_unknown_request_condition(self):
        q = "tmax 중앙값"
        inter = interpretation(q, proposal(term("statistic", "median", "중앙값"), [cond("endpoint", "tmax")]))
        evidence = {"E1": {"text": '<table><tr><th>Endpoint</th><th>Median</th></tr><tr><td>tmax (day)</td><td>8.7</td></tr></table>'}}
        candidates, audits = inventory(q, inter, {}, evidence)
        c = next(c for c in candidates.values() if c["value"] == "8.7")
        # Simulate an upstream scope check appended after a stale summary.
        audits[c["id"]]["requests"][0]["checks"].append({"dimension": "scope", "status": "unknown"})
        raw = {"status": "answered", "premise_source_ids": [], "items": [{"study": "none", "item": "value",
               "source_label": "tmax", "value_id": c["id"], "exact": True, "conditions": []}]}
        result = validate_selection(raw, candidates, audits, evidence, inter, q)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["items"])
        self.assertEqual(len(result["related_items"]), 1)
        self.assertFalse(result["related_items"][0]["exact"])
        self.assertEqual(result["request_coverage"]["covered"], [])


if __name__ == "__main__":
    unittest.main()
