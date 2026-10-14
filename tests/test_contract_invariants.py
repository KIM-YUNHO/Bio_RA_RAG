"""Structure mutations, independent of evaluation products/pages/answer values."""
import unittest
from rag.contract_candidate import inventory, validate_selection
from rag.request_contract import ground
from tests.test_request_contract import proposal, term, cond


class ContractInvariantTests(unittest.TestCase):
    def audit(self,text,q,requirements):
        inter={"requirements":ground(q,requirements),"understanding":{"studies":[],"topic":"pk_results","products":[],"english_query":q}}
        evidence={"E1":{"text":text}}
        c,a=inventory(q,inter,{},evidence)
        return c,a

    def test_row_column_transposition_preserves_endpoint_statistic_binding(self):
        q="tmax 중앙값";p=proposal(term("statistic","median","중앙값"),[cond("endpoint","tmax")])
        tables=[
            '<table><tr><th>Endpoint</th><th>Median</th><th>Mean</th></tr><tr><td>tmax (day)</td><td>5.5</td><td>6.5</td></tr><tr><td>Cmax (ng/mL)</td><td>5.5</td><td>6.5</td></tr></table>',
            '<table><tr><th>Statistic</th><th>tmax (day)</th><th>Cmax (ng/mL)</th></tr><tr><td>Median</td><td>5.5</td><td>5.5</td></tr><tr><td>Mean</td><td>6.5</td><td>6.5</td></tr></table>']
        for table in tables:
            c,a=self.audit(table,q,p)
            values=[(c[cid]["value"],r["status"]) for cid,r in a.items() if c[cid]["kind"]=="table_cell" and c[cid]["value"] in ("5.5","6.5")]
            self.assertEqual(sorted(values),sorted([("5.5","supported"),("5.5","mismatch"),("6.5","mismatch"),("6.5","mismatch")]))

    def test_equal_values_do_not_merge_distinct_endpoints_or_offsets(self):
        q="tmax 중앙값";p=proposal(term("statistic","median","중앙값"),[cond("endpoint","tmax")])
        c,a=self.audit('<table><tr><th>Endpoint</th><th>Median</th></tr><tr><td>Cmax (ng/mL)</td><td>5.5</td></tr><tr><td>tmax (day)</td><td>5.5</td></tr></table>',q,p)
        cells=[v for v in c.values() if v["kind"]=="table_cell" and v["value"]=="5.5"]
        self.assertEqual(len(cells),2);self.assertNotEqual(cells[0]["start"],cells[1]["start"])
        self.assertEqual([a[v["id"]]["status"] for v in cells],["mismatch","supported"])

    def test_unit_change_changes_only_own_value_condition(self):
        q="tmax 중앙값 day";p=proposal(term("statistic","median","중앙값"),[cond("endpoint","tmax"),cond("unit","day")])
        for unit,expected in [("day","supported"),("mL/kg","mismatch")]:
            c,a=self.audit('<table><tr><th>Endpoint</th><th>Median</th></tr><tr><td>tmax ('+unit+')</td><td>5.5</td></tr></table>',q,p)
            cid=next(cid for cid,v in c.items() if v["value"]=="5.5")
            self.assertEqual(a[cid]["status"],expected)

    def test_adding_AND_condition_cannot_keep_a_previous_partial_row_certified(self):
        text='<table><tr><th>Subgroup</th><th>Endpoint</th><th>GMR</th></tr><tr><td>Protein content adjusted</td><td>Cmax</td><td>1.1</td></tr><tr><td>Protein content adjusted &amp; ADA negative</td><td>Cmax</td><td>1.1</td></tr></table>'
        # The ADA-negative row cannot stand in for the unqualified population;
        # adding that requested condition can support it while the other row
        # becomes unresolved, without borrowing the neighbouring label.
        for extra,statuses in [(False,["supported","mismatch"]),(True,["unknown","supported"])]:
            q="Cmax GMR 단백질 보정"+ (" ADA 음성" if extra else "")
            conditions=[cond("endpoint","Cmax"),cond("covariate","protein_adjusted","단백질 보정")]
            if extra:conditions.append(cond("analysis_set","ADA_negative","ADA 음성"))
            c,a=self.audit(text,q,proposal(term("statistic","GMR"),conditions))
            self.assertEqual([a[cid]["status"] for cid,v in c.items() if v["kind"]=="table_cell" and v["value"]=="1.1"],statuses)


if __name__=="__main__":
    unittest.main()
