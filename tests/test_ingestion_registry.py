"""Offline rules of the name registry. The full resolver is scored with `python -m ingestion.eval_resolver` (needs the DB and network)."""
import unittest

from ingestion import registry, sources
from ingestion.licence_status import us_date


def row(name, key, ingredient="x", name_type="brand", jurisdiction="EU"):
    return {"name": name, "name_norm": sources.norm(name), "name_type": name_type, "jurisdiction": jurisdiction, "ingredient": ingredient,
            "product_key": key, "product_kind": "biosimilar"}


class Rules(unittest.TestCase):
    def test_norm_keeps_any_script_and_matches_collect_for_ascii(self):
        from collection import collect
        for value in ["ABP 654", "CT-P43", "Stelara®", "ustekinumab-auub"]:
            self.assertEqual(sources.norm(value), collect.norm(value))
        self.assertEqual(sources.norm("에피즈텍 정맥주사"), "에피즈텍정맥주사")

    def test_code_shape(self):
        for code in ["SB17", "ABP 654", "CT-P43", "DMB-3115", "AVT04"]:
            self.assertTrue(registry.CODE.match(code), code)
        for word in ["Stelara", "Jamteki", "aspirin tablets"]:
            self.assertFalse(registry.CODE.match(word), word)

    def test_purple_ingredient_drops_biosimilar_suffix_only(self):
        self.assertEqual(sources.purple_ingredient({"Proper Name": "ustekinumab-auub", "License Type": "351(k) Biosimilar"}), "ustekinumab")
        self.assertEqual(sources.purple_ingredient({"Proper Name": "ustekinumab", "License Type": "351(a)"}), "ustekinumab")

    def test_dates(self):
        self.assertEqual(us_date("October 31, 2023"), "2023-10-31")
        self.assertIsNone(us_date(""))
        self.assertEqual(sources.ema_date("16/01/2009"), "2009-01-16")

    def test_first_word_of_canadian_brand(self):
        self.assertEqual(registry.first_word("STELARA I.V."), "stelara")

    def test_unify_joins_same_brand_across_jurisdictions_and_keeps_registered_key(self):
        b = registry.Builder.__new__(registry.Builder)
        b.rows = [row("Humira", "EU:1"), row("Humira", "US:BLA2", jurisdiction="US"), row("humira-x", "US:BLA2", name_type="proper_name", jurisdiction="US"),
                  row("Pyzchiva", "SB17"), row("Pyzchiva", "US:BLA3", jurisdiction="US"),
                  row("Adalimumab Viatris", "EU:4"), row("Adalimumab Mylan", "US:BLA5", jurisdiction="US")]
        b.unify()
        keys = [r["product_key"] for r in b.rows]
        self.assertEqual(keys[:5], ["EU:1", "EU:1", "EU:1", "SB17", "SB17"])
        self.assertEqual(keys[5:], ["EU:4", "US:BLA5"])  # same INN prefix, different products


if __name__ == "__main__":
    unittest.main()
