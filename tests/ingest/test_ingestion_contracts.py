import copy
import json
import unittest

from pydantic import ValidationError

from ingestion.contracts import (ROOT, GuidelineSeed, IngestionRequest, TargetsFile, TurnParse, load_guidelines, load_targets)

RAW = json.loads((ROOT / "configs/collection/targets.json").read_text(encoding="utf-8"))


def targets_with(change):
    raw = copy.deepcopy(RAW)
    change(raw)
    return raw


def product(product_id):
    return copy.deepcopy(next(p for p in RAW["products"] if p["product_id"] == product_id))


EVIDENCE = {"source": "EMA medicines JSON", "url": "https://www.ema.europa.eu/x", "matched_text": "Pyzchiva", "as_of": "2026-10-14",
            "snapshot_sha256": "0" * 64}


def request(**change):
    data = {"kind": "refresh", "ingredient": "ustekinumab", "products": [product("SB17")], "include_reference": False,
            "jurisdictions": ["EU", "US"], "provenance": {"SB17.development_code": EVIDENCE, "SB17.study_ids": EVIDENCE,
                                                         "SB17.pmda_general_list_ids": EVIDENCE}}
    data.update(change)
    return data


SB17_SEEN = ["discover: SB17 Pyzchiva NCT04772274 NCT04967508 PMDA 3999476"]


class RegistrationFiles(unittest.TestCase):
    def test_project_files_validate(self):
        self.assertEqual([p.product_id for p in load_targets().products], [p["product_id"] for p in RAW["products"]])
        self.assertEqual(len(load_guidelines().guidelines), 14)

    def test_biosimilar_must_name_its_reference(self):
        raw = targets_with(lambda r: r["products"][0].update(reference_product_id=None))
        with self.assertRaisesRegex(ValidationError, "names its reference product"):
            TargetsFile.model_validate(raw)

    def test_reference_must_be_a_reference_product(self):
        raw = targets_with(lambda r: r["products"][0].update(reference_product_id="ABP654"))
        with self.assertRaisesRegex(ValidationError, "must name a reference product"):
            TargetsFile.model_validate(raw)

    def test_product_id_is_the_development_code(self):
        raw = targets_with(lambda r: r["products"][0].update(product_id="PYZCHIVA"))
        with self.assertRaisesRegex(ValidationError, "product_id is the development code"):
            TargetsFile.model_validate(raw)

    def test_one_licence_name_belongs_to_one_product(self):
        def change(r):
            r["products"][1]["licences"].append(dict(r["products"][0]["licences"][1]))
        with self.assertRaisesRegex(ValidationError, "listed under two products"):
            TargetsFile.model_validate(targets_with(change))

    def test_primary_eu_licence_is_the_collected_name(self):
        raw = targets_with(lambda r: r["products"][0].update(ema_name="Other"))
        with self.assertRaisesRegex(ValidationError, "primary EU licence"):
            TargetsFile.model_validate(raw)

    def test_unknown_product_field_is_refused(self):
        raw = targets_with(lambda r: r["products"][0].update(kr_brand="Epyztek"))
        with self.assertRaises(ValidationError):
            TargetsFile.model_validate(raw)

    def test_guideline_has_either_url_or_pages(self):
        seed = {"id": "X-1", "source": "EMA", "document_type": "guideline", "jurisdiction": "EU", "title": "t", "landing": "https://l",
                "topics": ["overarching"]}
        with self.assertRaisesRegex(ValidationError, "either url"):
            GuidelineSeed.model_validate(seed)
        GuidelineSeed.model_validate(dict(seed, url="https://u.pdf"))


class Requests(unittest.TestCase):
    def test_request_with_seen_identifiers_is_runnable(self):
        r = IngestionRequest.model_validate(request(), context={"evidence_text": SB17_SEEN})
        self.assertEqual(r.products[0].product_id, "SB17")

    def test_made_up_identifier_is_refused(self):
        p = product("SB17"); p["study_ids"] = p["study_ids"] + ["NCT09999999"]
        with self.assertRaisesRegex(ValidationError, "NCT09999999"):
            IngestionRequest.model_validate(request(products=[p]), context={"evidence_text": SB17_SEEN})

    def test_validation_needs_the_evidence_context(self):
        with self.assertRaisesRegex(ValidationError, "evidence_text"):
            IngestionRequest.model_validate(request())

    def test_identifier_needs_provenance(self):
        with self.assertRaisesRegex(ValidationError, "no provenance"):
            IngestionRequest.model_validate(request(provenance={}), context={"evidence_text": SB17_SEEN})

    def test_jp_needs_checked_pmda_pages(self):
        p = product("ABP654")
        seen = ["ABP 654 NCT04607980 NCT04761627"]
        with self.assertRaisesRegex(ValidationError, "PMDA"):
            IngestionRequest.model_validate(request(products=[p], jurisdictions=["JP"], provenance={"ABP654.development_code": EVIDENCE,
                                                    "ABP654.study_ids": EVIDENCE}), context={"evidence_text": seen})

    def test_reference_product_only_when_chosen(self):
        p = product("CNTO1275")
        with self.assertRaisesRegex(ValidationError, "include_reference"):
            IngestionRequest.model_validate(request(products=[p], provenance={"CNTO1275.development_code": EVIDENCE}),
                                            context={"evidence_text": ["CNTO1275 Stelara"]})

    def test_turn_parse_is_strict(self):
        TurnParse.model_validate({"intent": "licence_status", "mentions": [{"text": "에피즈텍", "guessed_type": "korean", "english_guess": "Epyztek"}],
                                  "jurisdictions": ["KR"], "selected_keys": []})
        with self.assertRaises(ValidationError):
            TurnParse.model_validate({"intent": "licence_status", "mentions": [], "jurisdictions": [], "selected_keys": [], "extra": 1})


if __name__ == "__main__":
    unittest.main()
