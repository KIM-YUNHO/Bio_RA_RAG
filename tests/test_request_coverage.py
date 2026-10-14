import unittest

from rag.request_coverage import classify, coverage


def check(request, *statuses, parent="match"):
    return {"request_id": request, "status": parent,
            "checks": [{"dimension": str(index), "status": status}
                       for index, status in enumerate(statuses)]}


class RequestCoverageTests(unittest.TestCase):
    def test_partial_AND_checks_cannot_be_joined_across_source_values(self):
        contract = {"requests": [{"id": "r"}]}
        result = coverage(contract, {"cell-A": [check("r", "match", "unknown")],
                                     "cell-B": [check("r", "unknown", "match")]})
        self.assertEqual(result["covered"], [])
        self.assertEqual(result["missing"], ["r"])
        self.assertEqual(result["unknown_selected_ids"], ["cell-A", "cell-B"])

    def test_distinct_field_conditions_are_not_broadcast_to_other_fields(self):
        contract = {"requests": [{"id": "result"}, {"id": "criterion"}]}
        result = coverage(contract, {"result-cell": [check("result", "match", "unknown"),
                                                     check("criterion", "mismatch")],
                                     "criterion-cell": [check("result", "mismatch"),
                                                         check("criterion", "match")]})
        self.assertEqual(result["covered"], ["criterion"])
        self.assertEqual(result["missing"], ["result"])
        self.assertEqual(result["source_support"]["criterion"], ["criterion-cell"])

    def test_parent_match_cannot_override_conflicting_condition_or_unbound_role(self):
        self.assertEqual(classify([check("r", "match", "mismatch")])["status"], "mismatch")
        self.assertEqual(classify([check("r")])["status"], "unknown")
        self.assertEqual(classify([check("r", "match", parent="unknown")])["status"], "unknown")

    def test_whole_source_can_supply_two_fields_without_claiming_full_question_certification(self):
        contract = {"requests": [{"id": "estimate"}, {"id": "bounds"}]}
        result = coverage(contract, {"whole-cell": [check("estimate", "match"), check("bounds", "match")]})
        self.assertTrue(result["compiled_requests_complete"])
        self.assertEqual(result["source_support"], {"estimate": ["whole-cell"], "bounds": ["whole-cell"]})
        self.assertFalse(result["complete_question_interpretation"])
        self.assertFalse(result["semantic_approval"])

    def test_unknown_request_or_duplicate_id_is_a_contract_error(self):
        with self.assertRaises(ValueError):
            coverage({"requests": [{"id": "r"}]}, {"cell": [check("other", "match")]})
        with self.assertRaises(ValueError):
            coverage({"requests": [{"id": "r"}, {"id": "r"}]}, {})


if __name__ == "__main__":
    unittest.main()
