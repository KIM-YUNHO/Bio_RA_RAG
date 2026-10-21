import unittest
from rag.grounded_table_graph import build, selected_fact, validate_graph
from tests.contract_match import condition, match_request
from tests.request_contract import ground
from tests.test_request_contract import term, proposal, cond


def graph(table, **extra):
    return build({"text": table, "document_id": "synthetic", "version_id": "v1", "pdf_page": 1, **extra})


def fact(g, value):
    cell = next(c for t in g["tables"] for c in t["cells"] if c["value"] == value)
    return selected_fact(g, cell["source_range"])


class GroundedTableGraphTests(unittest.TestCase):
    TABLE = ('<table><tr><th>Endpoint</th><th>Median</th><th>Mean</th></tr>'
             '<tr><td>tmax (day)</td><td>13.5</td><td>15.3</td></tr>'
             '<tr><td>Vz/F (mL/kg)</td><td>160.5 (37.3, 354.1)</td><td>178.7</td></tr></table>')

    def test_foreign_row_literal_does_not_inherit_tmax_or_day(self):
        g = graph(self.TABLE); text = self.TABLE; a = text.index("37.3")
        f = selected_fact(g, [a, a + 4])
        self.assertEqual(condition(f, "endpoint", "eq", ["tmax"])["status"], "mismatch")
        self.assertEqual(condition(f, "unit", "eq", ["day"])["status"], "mismatch")

    def test_valid_median_and_mean_keep_own_roles(self):
        g = graph(self.TABLE)
        for value, statistic in [("13.5", "median"), ("15.3", "mean")]:
            f = fact(g, value)
            self.assertEqual(condition(f, "endpoint", "eq", ["tmax"])["status"], "match")
            self.assertEqual(condition(f, "statistic", "eq", [statistic])["status"], "match")
            self.assertFalse(f["semantic_approval"])

    def test_transposition_preserves_endpoint_and_statistic_roles(self):
        g = graph('<table><tr><th>Statistic</th><th>tmax (day)</th><th>Vz/F (mL/kg)</th></tr>'
                  '<tr><td>Median</td><td>13.5</td><td>160.5</td></tr><tr><td>Mean</td><td>15.3</td><td>178.7</td></tr></table>')
        f = fact(g, "15.3")
        self.assertEqual(condition(f, "endpoint", "eq", ["tmax"])["status"], "match")
        self.assertEqual(condition(f, "statistic", "eq", ["mean"])["status"], "match")

    def test_multilevel_colspan_does_not_leak_other_dose(self):
        g = graph('<table><tr><th>Endpoint</th><th colspan="2">30 mg</th><th colspan="2">75 mg</th></tr>'
                  '<tr><th></th><th>Median</th><th>Mean</th><th>Median</th><th>Mean</th></tr>'
                  '<tr><td>tmax (day)</td><td>1.1</td><td>1.2</td><td>2.1</td><td>2.2</td></tr></table>')
        f = fact(g, "2.2")
        self.assertEqual(condition(f, "dose", "eq", ["75 mg"])["status"], "match")
        self.assertEqual(condition(f, "dose", "eq", ["30 mg"])["status"], "mismatch")

    def test_and_cannot_borrow_ada_from_another_body_row(self):
        g = graph('<table><tr><th>Analysis</th><th>Cmax</th></tr>'
                  '<tr><td>Protein adjusted</td><td>12.3</td></tr>'
                  '<tr><td>Protein adjusted &amp; ADA negative</td><td>11.2</td></tr></table>')
        question = "Cmax 단백질 보정 ADA 음성"
        c = ground(question, proposal(term("endpoint", "Cmax"), [cond("covariate", "protein_adjusted", "단백질 보정"), cond("analysis_set", "ADA_negative", "ADA 음성")]))
        self.assertEqual(match_request(c, c["requests"][0], fact(g, "12.3"))["status"], "unknown")
        self.assertEqual(match_request(c, c["requests"][0], fact(g, "11.2"))["status"], "match")

    def test_rowspan_label_is_local_to_its_own_rows(self):
        g = graph('<table><tr><th>Analysis</th><th>Arm</th><th>Cmax</th></tr>'
                  '<tr><td rowspan="2">ADA negative</td><td>A</td><td>12.3</td></tr>'
                  '<tr><td>B</td><td>11.2</td></tr><tr><td>Other</td><td>C</td><td>9.8</td></tr></table>')
        self.assertEqual(condition(fact(g, "11.2"), "analysis_set", "eq", ["ADA_negative"])["status"], "match")
        self.assertEqual(condition(fact(g, "9.8"), "analysis_set", "eq", ["ADA_negative"])["status"], "unknown")

    def test_same_page_second_table_is_not_context(self):
        g = graph(self.TABLE + '<table><tr><th>Endpoint</th><th>Mean</th></tr><tr><td>AUCinf</td><td>234</td></tr></table>')
        self.assertEqual(condition(fact(g, "234"), "endpoint", "eq", ["tmax"])["status"], "mismatch")

    def test_structural_problem_remains_unknown(self):
        g = graph('<table><tr><th>Endpoint</th><th>Mean</th></tr><tr><td>Cmax</td><td>2.4</td><td>3.1</td></tr></table>')
        self.assertTrue(g["tables"][0]["issues"])
        self.assertEqual(condition(fact(g, "2.4"), "endpoint", "eq", ["Cmax"])["status"], "unknown")

    def test_auclast_cannot_be_replaced_by_aucinf(self):
        g = graph('<table><tr><th>Endpoint</th><th>Mean</th></tr><tr><td>AUCinf</td><td>123.4</td></tr></table>')
        self.assertEqual(condition(fact(g, "123.4"), "endpoint", "eq", ["AUClast"])["status"], "mismatch")

    def test_compound_unit_cannot_be_day(self):
        g = graph('<table><tr><th>Endpoint</th><th>Mean</th></tr><tr><td>CL/F (mL/day/kg)</td><td>5.8</td></tr></table>')
        self.assertEqual(condition(fact(g, "5.8"), "unit", "eq", ["day"])["status"], "mismatch")

    def test_source_quotes_and_hash_are_bound_to_original_representation(self):
        g = graph(self.TABLE, source_refs=[{"pdf_page": 1}], source_spans=[{"unit_id": "u1"}])
        for t in g["tables"]:
            for c in t["cells"]:
                a, b = c["source_range"]
                self.assertEqual(c["quote"], self.TABLE[a:b])
        changed = graph(self.TABLE.replace("13.5", "14.5"))
        self.assertNotEqual(g["text_sha256"], changed["text_sha256"])
        self.assertNotEqual(g["tables"][0]["id"], changed["tables"][0]["id"])
        self.assertEqual(g["source_spans"], [{"unit_id": "u1"}])
        self.assertTrue(validate_graph(g, self.TABLE))
        with self.assertRaises(ValueError):
            validate_graph(g, self.TABLE.replace("13.5", "14.5"))

    def test_graph_rejects_forged_relationship_to_other_row(self):
        g = graph(self.TABLE)
        f = next(f for t in g["tables"] for f in t["facts"] if next(c for c in t["cells"] if c["id"] == f["value_node"])["value"] == "178.7")
        wrong = next(c for t in g["tables"] for c in t["cells"] if c["value"] == "tmax (day)")
        f["relations"].append({"node_id": wrong["id"], "kind": "row_context", "status": "structural_candidate"})
        with self.assertRaises(ValueError):
            validate_graph(g, self.TABLE)

    def test_unclosed_additional_table_keeps_relationships_unresolved(self):
        g = graph(self.TABLE + '<table><tr><td>unfinished')
        self.assertTrue(g["representation_issues"])
        self.assertEqual(condition(fact(g, "13.5"), "endpoint", "eq", ["tmax"])["status"], "unknown")

    def test_literal_less_than_text_and_inline_markup_preserve_cell_alignment(self):
        text = '<table><tr><th>Endpoint</th><th>Mean</th></tr><tr><td>C<sub>max</sub></td><td>2.4</td></tr><tr><td>&lt;NO DATA&gt; and weight &lt; 60 kg</td><td>3.1</td></tr></table>'
        g = graph(text)
        self.assertFalse(g["tables"][0]["issues"])
        self.assertEqual(condition(fact(g, "2.4"), "endpoint", "eq", ["Cmax"])["status"], "match")
        self.assertTrue(validate_graph(g, text))

    def test_sd_literal_does_not_become_mean_of_parent_cell(self):
        text = '<table><tr><th>Endpoint</th><th>Mean (SD)</th></tr><tr><td>Cmax</td><td>2.4 (1.2)</td></tr></table>'
        g = graph(text); a = text.index("1.2"); f = selected_fact(g, [a, a + 3])
        self.assertEqual(condition(f, "statistic", "eq", ["mean"])["status"], "unknown")
        c = ground("Cmax 평균", proposal(term("endpoint", "Cmax"), [cond("statistic", "mean", "평균")]))
        self.assertEqual(match_request(c, c["requests"][0], f)["status"], "unknown")
        self.assertEqual(match_request(c, c["requests"][0], fact(g, "2.4 (1.2)"))["status"], "match")

    def test_order_and_range_require_explicit_binding(self):
        f = fact(graph(self.TABLE), "13.5")
        for operator in ["ordered_pair", "range"]:
            self.assertEqual(condition(f, "unit", operator, ["day", "h"])["status"], "unknown")

    def test_no_table_and_invalid_range_cannot_create_facts(self):
        g = graph("Cmax was 2.4; unrelated tmax was 13.5.")
        self.assertIsNone(selected_fact(g, [11, 14]))
        with self.assertRaises(ValueError):
            selected_fact(g, [-1, 4])


if __name__ == "__main__":
    unittest.main()
