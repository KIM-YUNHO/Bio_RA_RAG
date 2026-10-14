import unittest
from rag.request_contract import ground, locate, translation_omissions
from rag.contract_terms import atoms


def term(dimension, value, quote=None, occurrence=0):
    return {"dimension": dimension, "value": value, "anchor": {"quote": quote or value, "occurrence": occurrence}}


def proposal(field, conditions=None):
    return {"requests": [{"id": "r1", "field": field, "conditions": conditions or []}], "non_request_mentions": []}


def cond(dimension, value, quote=None, operator="eq"):
    return {"dimension": dimension, "operator": operator, "values": [{"value": value, "anchor": {"quote": quote or value, "occurrence": 0}}]}


class RequestContractTests(unittest.TestCase):
    def test_korean_request_survives_incomplete_english_translation(self):
        missing = translation_omissions("AUCinf와 Cmax의 기하평균비", "geometric mean ratio of AUCinf")
        self.assertEqual([(m["dimension"], m["value"]) for m in missing], [("endpoint", "Cmax")])

    def test_omitted_requested_endpoint_is_incomplete(self):
        out = ground("AUCinf와 Cmax", proposal(term("endpoint", "AUCinf")))
        self.assertEqual(out["status"], "incomplete")
        self.assertEqual(out["missing_known_mentions"][0]["value"], "Cmax")

    def test_and_conditions_are_independent_and_anchored(self):
        out = ground("AUCinf 단백질 보정 및 ADA 음성", proposal(term("endpoint", "AUCinf"),
            [cond("covariate", "protein_adjusted", "단백질 보정"), cond("analysis_set", "ADA_negative", "ADA 음성")]))
        self.assertEqual(out["status"], "anchored_candidate")
        self.assertEqual(out["requests"][0]["condition_logic"], "AND")
        self.assertEqual(len(out["requests"][0]["conditions"]), 2)
        self.assertFalse(out["semantic_approval"])

    def test_direction_is_an_ordered_pair_not_a_set(self):
        c = {"dimension": "comparison", "operator": "ordered_pair", "values": [
            {"value": "Arm B", "anchor": {"quote": "Arm B", "occurrence": 0}},
            {"value": "Arm A", "anchor": {"quote": "Arm A", "occurrence": 0}}]}
        out = ground("Cmax Arm B 대비 Arm A", proposal(term("endpoint", "Cmax"), [c]))
        self.assertEqual([v["value"] for v in out["requests"][0]["conditions"][0]["values"]], ["Arm B", "Arm A"])

    def test_nonliteral_anchor_fails(self):
        with self.assertRaises(ValueError):
            ground("Cmax", proposal(term("endpoint", "Cmax", "AUClast")))

    def test_mean_substring_inside_gmr_is_not_an_arithmetic_mean_request(self):
        r = ground("기하평균비",proposal(term("statistic","mean","평균")))
        self.assertEqual(r["requests"][0]["field"]["normalization_status"],"review")
        self.assertEqual(r["warnings"][0]["reason"],"question_anchor_role_conflict")

    def test_parenthesized_alias_does_not_require_duplicate_request(self):
        r=ground("기하평균비(GMR)",proposal(term("statistic","GMR","기하평균비")))
        self.assertEqual(r["status"],"anchored_candidate")
        r=ground("기하평균비와 나중의 GMR",proposal(term("statistic","GMR","기하평균비")))
        self.assertEqual(r["status"],"incomplete")

    def test_latin_statistic_followed_by_korean_particle_keeps_its_role(self):
        r=ground("GMR와 90% CI",proposal(term("statistic","GMR")),normalizer=None)
        self.assertEqual(r["requests"][0]["normalization_status"],"grounded")

    def test_repeated_quote_selects_declared_occurrence(self):
        self.assertEqual(locate("Cmax 비교 Cmax", {"quote": "Cmax", "occurrence": 1})["start"], 8)
        with self.assertRaises(ValueError):
            locate("Cmax", {"quote": "Cmax", "occurrence": 1})

    def test_duplicate_id_fails(self):
        p = proposal(term("endpoint", "Cmax")); p["requests"] *= 2
        with self.assertRaises(ValueError):
            ground("Cmax", p)

    def test_unquoted_semantic_normalization_is_reviewed(self):
        out = ground("주 분석군", proposal(term("population", "healthy volunteers", "주 분석군")))
        self.assertEqual(out["status"], "normalization_review")

    def test_endpoint_substitution_is_not_grounded(self):
        out = ground("AUClast", proposal(term("endpoint", "AUCinf", "AUClast")))
        self.assertEqual(out["status"], "incomplete")
        self.assertTrue(out["warnings"])

    def test_operator_arity_is_checked(self):
        with self.assertRaises(ValueError):
            ground("Cmax", proposal(term("endpoint", "Cmax"), [cond("comparison", "Cmax", operator="ordered_pair")]))

    def test_geometric_ratio_is_not_arithmetic_mean(self):
        self.assertEqual([a["value"] for a in atoms("geometric mean ratio")], ["GMR"])

    def test_compound_unit_is_not_scalar_day(self):
        self.assertEqual([a["value"] for a in atoms("mL/day/kg")], ["ml/day/kg"])

    def test_auc_tex_markup_does_not_change_endpoint(self):
        for value in ["AUCinf", "AUC0-inf", r"$ AUC_{0-\text{inf}} $", r"AUC $ _{inf} $"]:
            self.assertEqual([a["value"] for a in atoms(value)], ["AUCinf"])

    def test_auc_compound_unit_does_not_become_day(self):
        for value in ["mcg·day/mL", "day*μg/mL", r"day* $ \mu g/mL $"]:
            found = [a["value"] for a in atoms(value)]
            self.assertEqual(len(found), 1)
            self.assertNotIn("day", found)

    def test_unparsed_compound_unit_is_not_scalar_day(self):
        self.assertFalse(any(a["value"] == "day" for a in atoms("day*unknown/mL")))

    def test_explicit_excluded_mention_is_retained_with_quote(self):
        p = proposal(term("endpoint", "Cmax"))
        p["non_request_mentions"] = [{"term": term("endpoint", "AUCinf"), "role": "excluded"}]
        out = ground("AUCinf 제외, Cmax", p)
        self.assertEqual(out["status"], "anchored_candidate")
        self.assertEqual(out["non_request_mentions"][0]["role"], "excluded")
        self.assertFalse(out["semantic_approval"])


if __name__ == "__main__":
    unittest.main()
