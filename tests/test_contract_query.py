from copy import deepcopy
import json
import unittest
from rag.contract_query import interpret, resolve, expand_groups, condition_schema
from tests.test_request_contract import proposal, term, cond


class ContractQueryTests(unittest.TestCase):
    def response(self):
        return {"understanding": {"intent": "fact_lookup", "products": [], "studies": [], "study_scope": "unspecified",
            "study_mentions": [], "jurisdictions": [], "jurisdiction_mentions": [], "doc_groups": [],
            "topic": "pk_results", "english_query": "What is AUCinf?", "keywords": ["AUCinf"]},
            "requirements": {"requests": [proposal(term("endpoint", "AUCinf"))["requests"][0],
                {"id": "r2", "field": term("endpoint", "Cmax"), "conditions": []}], "non_request_mentions": []}}

    def test_original_question_contract_and_search_preserve_missing_cmax(self):
        r = resolve("AUCinf와 Cmax", self.response())
        self.assertEqual(r["original_question"], "AUCinf와 Cmax")
        self.assertEqual(r["requirements"]["status"], "anchored_candidate")
        self.assertIn("Cmax", r["search_terms"])
        self.assertTrue(r["requires_review"])

    def test_single_injected_call_and_no_default_model(self):
        calls = []
        def fake(messages, json_schema):
            calls.append((messages, json_schema))
            return {"text": json.dumps(self.response()), "model_reported": "offline-fixture"}
        r = interpret("AUCinf와 Cmax", chat_client=fake)
        self.assertEqual(len(calls), 1)
        self.assertEqual(r["model_calls"], 1)
        with self.assertRaises(ValueError):
            interpret("Cmax", chat_client=None)

    def test_missing_contract_or_extra_fields_are_rejected(self):
        for change in [lambda r: r.pop("requirements"), lambda r: r.update(answer="invented")]:
            r = deepcopy(self.response()); change(r)
            with self.assertRaises(ValueError):
                resolve("AUCinf와 Cmax", r)

    def test_endpoint_cannot_be_used_as_a_dimension_or_ci_without_level(self):
        for dimension, value in [("AUCinf", "GMR"), ("statistic", "CI")]:
            r = self.response(); r["requirements"]["requests"][0]["field"].update(dimension=dimension, value=value)
            with self.assertRaises(ValueError):
                resolve("AUCinf와 Cmax", r)

    def grouped(self):
        return {"request_groups": [{"id": "pk", "fields": [term("statistic", "GMR"), term("statistic", "CI90", "90% CI")],
            "conditions": [cond("analysis_set", "ADA_negative", "ADA 음성")],
            "axes": [{"options": [cond("endpoint", "AUCinf"), cond("endpoint", "Cmax")]}]}], "non_request_mentions": []}

    def test_explicit_axes_expand_all_requests_with_shared_AND_and_anchors(self):
        p = self.grouped(); r = self.response(); r["requirements"] = p
        out = resolve("ADA 음성 AUCinf와 Cmax GMR와 90% CI", r)
        requests = out["requirements"]["requests"]
        self.assertEqual(len(requests), 4)
        self.assertTrue(all(req["conditions"][0]["values"][0]["anchor"]["quote"] == "ADA 음성" for req in requests))
        self.assertEqual(out["requirements"]["status"], "anchored_candidate")
        requests[0]["conditions"][0]["values"][0]["value"] = "changed"
        self.assertEqual(requests[1]["conditions"][0]["values"][0]["value"], "ADA_negative")

    def test_correlated_groups_do_not_invent_other_dose_statistic_combinations(self):
        p = {"request_groups": [{"id": "a", "fields": [term("statistic", "median")], "conditions": [cond("dose", "20 mg")], "axes": []},
                                 {"id": "b", "fields": [term("statistic", "mean")], "conditions": [cond("dose", "40 mg")], "axes": []}], "non_request_mentions": []}
        out = expand_groups(p)
        self.assertEqual(len(out["requests"]), 2)
        self.assertEqual([(r["field"]["value"],r["conditions"][0]["values"][0]["value"]) for r in out["requests"]], [("median", "20 mg"), ("mean", "40 mg")])

    def test_group_arity_axis_collisions_and_expansion_budget_rejected(self):
        p = self.grouped(); p["request_groups"][0]["axes"][0]["options"][0]["values"].append(term("endpoint", "Cmax")["anchor"])
        with self.assertRaises(ValueError): expand_groups(p)
        p = self.grouped(); p["request_groups"][0]["axes"].append(deepcopy(p["request_groups"][0]["axes"][0]))
        with self.assertRaises(ValueError): expand_groups(p)
        p = self.grouped(); p["request_groups"][0]["fields"] *= 40
        with self.assertRaises(ValueError): expand_groups(p)

    def test_provider_schema_also_constrains_condition_arity(self):
        variants = condition_schema()["anyOf"]
        self.assertEqual([(s["properties"]["operator"]["enum"], s["properties"]["values"].get("maxItems")) for s in variants],
                         [(["eq", "not_eq"], 1), (["ordered_pair", "unordered_pair"], 2), (["range"], 2), (["one_of"], None)])

    def test_range_kg_bound_is_normalized_only_with_original_interval_anchors(self):
        r=self.response();r["requirements"]={"requests":[{"id":"r","field":term("endpoint","Cmax"),"conditions":[{
            "dimension":"weight","operator":"range","values":[{"value":"70","anchor":{"quote":"70","occurrence":0}},
                                                                    {"value":"110 kg","anchor":{"quote":"110 kg","occurrence":0}}]}]}],"non_request_mentions":[]}
        out=resolve("체중 70~110 kg Cmax",r)
        c=out["requirements"]["requests"][0]["conditions"][0]
        self.assertEqual([v["value"] for v in c["values"]],["70","110"]);self.assertEqual(c["unit"],"kg")
        self.assertEqual(out["requirements"]["status"],"anchored_candidate")

    def test_original_reference_comparison_omission_is_an_alarm(self):
        r=self.response();out=resolve("AUCinf와 Cmax EU Stelara 대비",r)
        self.assertTrue(any(a["dimension"]=="comparison" for a in out["requirements"]["missing_known_mentions"]))

    def test_repair_is_single_injected_question_only_call(self):
        from rag.contract_query import repair
        calls=[]
        def fake(messages,json_schema):
            calls.append(messages);return {"text":json.dumps(self.response()),"model_reported":"offline"}
        out=repair("AUCinf와 Cmax",self.response(),chat_client=fake)
        self.assertEqual(len(calls),1);self.assertFalse(out["recursive_repair"])
        self.assertIn("original_known_roles",calls[0][-1]["content"])
        self.assertNotIn("gold",json.dumps(self.response()))

    def test_question_typed_fields_prevent_mean_from_gmr_substring(self):
        from rag.contract_query import field_schema
        variants=field_schema("Cmax 기하평균비와 90% CI")["anyOf"]
        stat=next(v for v in variants if v["properties"]["dimension"]["enum"]==["statistic"])
        self.assertEqual(stat["properties"]["value"]["enum"],["GMR","CI90"])
        self.assertFalse(any(v["properties"]["dimension"]["enum"]==["endpoint"] for v in variants))

    def test_comparisons_require_pair_at_provider_and_unit_request_is_not_unit_condition(self):
        variants=condition_schema("tmax 중앙값과 단위")["anyOf"]
        self.assertFalse(any("comparison" in v["properties"]["dimension"]["enum"] for v in variants if v["properties"]["operator"]["enum"]!=["ordered_pair","unordered_pair"]))
        self.assertFalse(any("unit" in v["properties"]["dimension"]["enum"] for v in variants))

    def test_explicit_physical_unit_can_still_be_a_condition(self):
        variants=condition_schema("tmax 단위를 day로 표시")["anyOf"]
        self.assertTrue(any("unit" in v["properties"]["dimension"]["enum"] for v in variants))

    def test_query_prompt_includes_whole_question_role_inventory(self):
        from rag.contract_query import messages
        content=messages("AUCinf Cmax 기하평균비와 90% CI")[-1]["content"]
        self.assertIn('"value":"GMR"',content)
        self.assertNotIn('"value":"mean"',content)

    def test_checked_query_uses_one_call_when_contract_is_ready(self):
        from rag.contract_query import interpret_checked
        calls=[]
        def fake(messages,json_schema):
            calls.append(messages);return {"text":json.dumps(self.response())}
        out=interpret_checked("AUCinf와 Cmax",chat_client=fake,allow_repair=True)
        self.assertEqual(len(calls),1);self.assertEqual(out["state"],"contract_candidate_ready")

    def test_checked_query_can_repair_once_before_any_retrieval(self):
        from rag.contract_query import interpret_checked
        calls=[];bad=self.response();bad["requirements"]["requests"].pop()
        def fake(messages,json_schema):
            calls.append(messages);return {"text":json.dumps(bad if len(calls)==1 else self.response())}
        out=interpret_checked("AUCinf와 Cmax",chat_client=fake,allow_repair=True)
        self.assertEqual(len(calls),2);self.assertEqual(out["state"],"contract_candidate_ready")
        self.assertEqual(out["model_calls"],2)

    def test_checked_query_stops_after_one_failed_repair_or_when_disabled(self):
        from rag.contract_query import interpret_checked
        for allowed,expected in [(False,1),(True,2)]:
            calls=[];bad=self.response();bad["requirements"]["requests"].pop()
            def fake(messages,json_schema):
                calls.append(messages);return {"text":json.dumps(bad)}
            out=interpret_checked("AUCinf와 Cmax",chat_client=fake,allow_repair=allowed)
            self.assertEqual(len(calls),expected);self.assertEqual(out["state"],"question_review_required")


if __name__ == "__main__":
    unittest.main()
