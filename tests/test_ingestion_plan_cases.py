"""Request shapes beyond 'a new biosimilar of the registered ingredient', checked without network or DB writes.

A plan must either be one promote can register without breaking targets.json, or a PlanError the agent replies with:
a biosimilar whose reference product is not registered yet, an ingredient with two originators, a product missing
names in some jurisdictions or a development code.
"""
import copy
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collection import collect
from ingestion import plan
from ingestion.contracts import ROOT, Candidate, Evidence


def licence(jurisdiction, name, licence_id):
    return {"jurisdiction": jurisdiction, "licence_id": licence_id, "name": name, "holder": "Example Pharma", "authorised": None,
            "primary": True, "collected": True}


def product(product_id, ingredient, kind, ema=None, us=None, ca=None, code=None, ref=None, studies=()):
    licences = [licence(j, n, i) for j, n, i in (("EU", ema, "EMEA/H/C/009999"), ("US", us, "BLA 761999"), ("CA", ca, None)) if n]
    return {"product_id": product_id, "ingredient": ingredient, "kind": kind, "reference_product_id": ref, "development_code": code,
            "ema_name": ema, "us_brand": us, "ca_brand": ca, "aliases": [n for n in (code, ema, us) if n], "study_ids": list(studies),
            "pmda_general_list_ids": [], "licences": licences, "company": "Example Pharma"}


def candidate(name, ingredient, level="biosimilar_product"):
    return Candidate(key=f"BRAND:{name}", level=level, ingredient=ingredient, product_id=None, names={"EU": name}, collected=False,
                     searchable=False, evidence=[Evidence(source="EMA medicines JSON", url=None, matched_text=name, as_of="2026-10-18",
                                                          snapshot_sha256=None)])


HUMIRA = product("HUMIRA", "adalimumab", "reference", ema="Humira", us="Humira", ca="Humira")
HYRIMOZ = product("GP2017", "adalimumab", "biosimilar", ema="Hyrimoz", us="Hyrimoz", ca="Hyrimoz", code="GP2017", ref="HUMIRA",
                  studies=["NCT02016105"])
PROLIA = product("PROLIA", "denosumab", "reference", ema="Prolia", us="Prolia")
XGEVA = product("XGEVA", "denosumab", "reference", ema="Xgeva", us="Xgeva")
JUBBONTI = product("GP2411", "denosumab", "biosimilar", ema="Jubbonti", us="Jubbonti", code="GP2411")
IMULDOSA = product("DMB3115", "ustekinumab", "biosimilar", ema="Imuldosa", us="Imuldosa", code="DMB-3115", ref="CNTO1275",
                   studies=["NCT04785326"])
EU_ONLY = product("XYZ123", "ustekinumab", "biosimilar", ema="Exampla", code="XYZ123", ref="CNTO1275")


class PlanCases(unittest.TestCase):
    def setUp(self):
        with patch.object(collect, "ROOT", collect.ROOT):  # discover redirects collect.ROOT at import time
            self.discover = importlib.import_module("product_search.discover")

    def plan(self, name, ingredient, products, level="biosimilar_product"):
        with patch.object(self.discover, "search", return_value={"products": copy.deepcopy(products)}):
            return plan.plan(candidate(name, ingredient, level), [name])

    def test_new_biosimilar_of_the_registered_ingredient(self):
        target = self.plan("Imuldosa", "ustekinumab", [IMULDOSA])["request"].products[0]
        self.assertEqual((target.product_id, target.reference_product_id), ("DMB3115", "CNTO1275"))

    def test_eu_only_biosimilar_keeps_null_names(self):
        target = self.plan("Exampla", "ustekinumab", [EU_ONLY])["request"]
        self.assertEqual(target.jurisdictions, ["EU"])
        self.assertIsNone(target.products[0].us_brand)

    def test_new_ingredient_reference_without_development_code(self):
        result = self.plan("Humira", "adalimumab", [HUMIRA, HYRIMOZ], level="reference_product")
        self.assertTrue(result["request"].include_reference)
        self.assertIsNone(result["request"].products[0].development_code)

    def test_biosimilar_before_its_reference_is_refused_with_the_reference_named(self):
        with self.assertRaises(plan.PlanError) as error:
            self.plan("Hyrimoz", "adalimumab", [HUMIRA, HYRIMOZ])
        self.assertIn("HUMIRA(Humira)", str(error.exception))
        self.assertIn("Humira 수집", str(error.exception))

    def test_two_originators_are_refused_instead_of_raising(self):
        with self.assertRaises(plan.PlanError) as error:
            self.plan("Jubbonti", "denosumab", [PROLIA, XGEVA, JUBBONTI])
        self.assertIn("PROLIA(Prolia)", str(error.exception))
        self.assertIn("XGEVA(Xgeva)", str(error.exception))


class PromoteEntry(unittest.TestCase):
    """The targets.json entry promote writes, and the check that runs before any file is copied."""
    def setUp(self):
        with patch.object(collect, "ROOT", collect.ROOT):
            self.promote = importlib.import_module("product_search.promote")
        self.targets = json.loads((ROOT / "configs/collection/targets.json").read_text(encoding="utf-8"))

    def test_missing_names_and_code_are_written_as_null(self):
        for p in (EU_ONLY, HUMIRA):
            with self.subTest(product=p["product_id"]):
                entry = self.promote.target_entry(copy.deepcopy(p), 3)
                self.assertTrue({"development_code", "ema_name", "us_brand", "ca_brand", "company"} <= set(entry))
                self.assertIsNone(self.promote.targets_problem(self.targets, entry))

    def test_entry_the_contract_refuses_is_reported(self):
        entry = self.promote.target_entry(copy.deepcopy(HYRIMOZ), 3)
        self.assertIn("reference_product_id", self.promote.targets_problem(self.targets, entry))

    def test_apply_stops_before_copying_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "configs/collection").mkdir(parents=True)
            (project / "configs/collection/targets.json").write_text(json.dumps(self.targets), encoding="utf-8")
            (project / "data/catalog").mkdir(parents=True)
            (project / "data/catalog/documents.jsonl").write_text("", encoding="utf-8")
            staged = (copy.deepcopy(HYRIMOZ), [], [], [])
            with patch.object(self.promote, "PROJECT", project), patch.object(self.promote, "staged", return_value=staged), \
                    patch.object(self.promote, "copy") as copied, patch("builtins.print"):
                with self.assertRaises(SystemExit) as stop:
                    self.promote.apply("GP2017")
            copied.assert_not_called()
            self.assertIn("targets.json would not validate", str(stop.exception))
            self.assertEqual(json.loads((project / "configs/collection/targets.json").read_text(encoding="utf-8")), self.targets)


if __name__ == "__main__":
    unittest.main()
