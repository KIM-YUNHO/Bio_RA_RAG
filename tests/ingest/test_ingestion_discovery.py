"""Discovery-to-plan regressions with official-list-shaped inputs and no DB/network writes."""
import copy
import importlib
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from collection import collect
from ingestion import plan
from ingestion.contracts import Candidate, Evidence, TargetsFile, load_targets


def medicine(name, ingredient, number, biosimilar="No"):
    return {"name_of_medicine": name, "active_substance": ingredient, "ema_product_number": number,
            "category": "Human", "medicine_status": "Authorised", "biosimilar": biosimilar,
            "marketing_authorisation_developer_applicant_holder": "Example Pharma", "marketing_authorisation_date": "08/01/2018"}


def purple(brand, proper, bla, licence="351(a)"):
    return {"Proprietary Name": brand, "Proper Name": proper, "BLA Number": bla,
            "License Type": licence, "Applicant": "Example Pharma"}


class DiscoveryPlan(unittest.TestCase):
    def setUp(self):
        # discover redirects collect.ROOT at import time for its standalone CLI.
        with patch.object(collect, "ROOT", collect.ROOT):
            self.discover = importlib.import_module("product_search.discover")
        self.ema = [medicine("Ocrevus", "ocrelizumab", "EMEA/H/C/004043")]
        self.us = [purple("Ocrevus", "ocrelizumab", "761053"),
                   purple("Ocrevus Zunovo", "ocrelizumab and hyaluronidase-ocsq", "761371")]
        self.codes = {"EMEA/H/C/004043": "RO4964913"}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, kwargs in {
            "get_json": {"side_effect": self.get_json},
            "purple_rows": {"side_effect": lambda: self.us},
            "epar_code": {"side_effect": lambda report: self.codes.get(report["ema_product_number"])},
            "canada_brand": {"return_value": None},
            "trials": {"return_value": []},
            "read_jsonl": {"return_value": []},
        }.items():
            self.stack.enter_context(patch.object(self.discover, name, **kwargs))
        self.gsrs = self.stack.enter_context(patch.object(self.discover, "gsrs_names", return_value={}))
        self.stack.enter_context(patch.object(plan, "staged_products", return_value=set()))

    def get_json(self, url, source, **kwargs):
        if url == self.discover.SOURCES["ema_products"]:
            return {"data": self.ema}
        if url == self.discover.SOURCES["ema_documents"]:
            return {"data": [{"ema_product_number": p["ema_product_number"], "type": "assessment-report",
                              "name": p["name_of_medicine"] + " : EPAR - Public assessment report"} for p in self.ema]}
        raise AssertionError(f"Unexpected external lookup: {url}")

    def candidate(self, key="EU:EMEA/H/C/004043", name="Ocrevus"):
        return Candidate(key=key, level="other_product", ingredient="ocrelizumab", product_id=None,
                         names={"EU": name}, collected=False, searchable=False,
                         evidence=[Evidence(source="EMA medicines JSON", url=None, matched_text=name,
                                            as_of="2026-10-14", snapshot_sha256=None)])

    def test_originator_ingredient_without_biosimilars_is_discovered(self):
        result = self.discover.search("ocrelizumab")
        self.assertEqual(result["kind"], "ingredient")
        self.assertEqual([p["product_id"] for p in result["products"]], ["OCREVUS"])
        p = result["products"][0]
        self.assertEqual((p["kind"], p["us_brand"]), ("reference", "Ocrevus"))
        self.assertEqual({l["licence_id"] for l in p["licences"]}, {"EMEA/H/C/004043", "BLA 761053"})
        self.assertIsNone(p["development_code"])
        self.gsrs.assert_not_called()

    def test_unregistered_ocrevus_builds_a_valid_plan_and_registration(self):
        result = plan.plan(self.candidate(), ["Ocrevus 적재해줘"])
        req = result["request"]
        self.assertEqual(req.products[0].product_id, "OCREVUS")
        self.assertTrue(req.include_reference)
        self.assertEqual(req.jurisdictions, ["EU", "US"])
        self.assertFalse(result["already_staged"])
        targets = load_targets().model_dump()
        targets["products"].append(req.products[0].model_dump())
        TargetsFile.model_validate(targets)

    def test_brand_and_ingredient_find_the_same_originator(self):
        by_brand = self.discover.search("Ocrevus")
        by_ingredient = self.discover.search("ocrelizumab")
        self.assertEqual(by_brand["kind"], "product")
        self.assertEqual(by_brand["products"], by_ingredient["products"])

    def test_us_only_originator_can_be_planned_without_a_development_code(self):
        self.ema = []
        req = plan.plan(self.candidate("US:BLA761053"), ["Ocrevus 적재해줘"])["request"]
        self.assertEqual(req.products[0].product_id, "OCREVUS")
        self.assertIsNone(req.products[0].development_code)
        self.assertEqual(req.jurisdictions, ["US"])

    def test_biosimilar_search_still_groups_the_reference_and_licences(self):
        self.ema = [medicine("Stelara", "ustekinumab", "EMEA/H/C/000958"),
                    medicine("Pyzchiva", "ustekinumab", "EMEA/H/C/006183", "Yes")]
        self.us = [purple("Stelara", "ustekinumab", "125261"),
                   purple("Pyzchiva", "ustekinumab-auub", "761373", "351(k) Biosimilar")]
        self.codes = {"EMEA/H/C/000958": "CNTO1275", "EMEA/H/C/006183": "SB17"}
        products = self.discover.search("ustekinumab")["products"]
        self.assertEqual([p["product_id"] for p in products], ["CNTO1275", "SB17"])
        self.assertEqual(products[1]["reference_product_id"], "CNTO1275")
        self.assertEqual([p["product_id"] for p in self.discover.search("Pyzchiva")["products"]], ["SB17"])

    def test_us_only_biosimilar_ingredient_is_recognised(self):
        self.ema = []
        self.us = [purple("Stelara", "ustekinumab", "125261"),
                   purple("Pyzchiva", "ustekinumab-auub", "761373", "351(k) Biosimilar")]
        products = self.discover.search("ustekinumab")["products"]
        self.assertEqual([p["us_brand"] for p in products], ["Stelara", "Pyzchiva"])

    def test_us_originator_proper_name_keeps_its_suffix(self):
        self.ema = []
        self.us = [purple("Example", "example-abcd", "100")]
        self.assertEqual(self.discover.search("example-abcd")["ingredient"], "example-abcd")
        self.assertEqual(self.discover.search("example")["ingredient"], "example-abcd")

    def test_unknown_or_blank_names_return_no_products(self):
        for query in ("MadeUpDrug", "", "   "):
            with self.subTest(query=query):
                self.assertEqual(self.discover.search(query)["products"], [])

    def test_brand_that_cannot_be_grouped_never_returns_other_products(self):
        with patch.object(self.discover, "discover", return_value=[{"licences": [{"name": "Different"}], "aliases": []}]):
            self.assertEqual(self.discover.search("Ocrevus")["products"], [])

    def test_official_licence_identity_wins_over_an_alias_match(self):
        valid = self.discover.search("ocrelizumab")["products"][0]
        wrong = copy.deepcopy(valid)
        wrong["product_id"] = "WRONG"
        wrong["licences"][0]["licence_id"] = "EMEA/H/C/999999"
        with patch.object(self.discover, "search", return_value={"products": [wrong, valid]}):
            self.assertEqual(plan.find_entry(self.candidate())["product_id"], "OCREVUS")
        with patch.object(self.discover, "search", return_value={"products": [wrong]}):
            with self.assertRaises(plan.PlanError):
                plan.find_entry(self.candidate())

    def test_us_bla_matching_is_exact_not_a_number_prefix(self):
        valid = self.discover.search("ocrelizumab")["products"][0]
        wrong = copy.deepcopy(valid)
        wrong["licences"][1]["licence_id"] = "BLA 7610530"
        with patch.object(self.discover, "search", return_value={"products": [wrong]}):
            with self.assertRaises(plan.PlanError):
                plan.find_entry(self.candidate("US:BLA761053"))

    def test_licence_match_cannot_cross_ingredients_or_hide_ambiguity(self):
        valid = self.discover.search("ocrelizumab")["products"][0]
        wrong = dict(valid, ingredient="different")
        for products in ([wrong], [valid, copy.deepcopy(valid)]):
            with self.subTest(count=len(products)), patch.object(self.discover, "search", return_value={"products": products}):
                with self.assertRaises(plan.PlanError):
                    plan.find_entry(self.candidate())

    def test_originator_trial_number_is_not_guessed_as_its_development_code(self):
        with patch.object(self.discover, "epar_code", return_value="WA21092") as guess:
            product = self.discover.search("Ocrevus")["products"][0]
        guess.assert_not_called()
        self.assertIsNone(product["development_code"])
        self.assertEqual(product["product_id"], "OCREVUS")


if __name__ == "__main__":
    unittest.main()
