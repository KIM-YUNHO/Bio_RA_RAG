"""Regression tests for scope integrity, broad fallback and source provenance. No API calls."""
import sys
import sqlite3
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rag"))
from answer import Corpus, answer_unit, retrieve, validate_answer
from evidence import contains_quote, contains_value
from plan_query import plan
from search import Bm25, to_pages
from study_context import annotate
from build_chunks import scan_sections
from study_search import preference, search
from understand_query import cache_fingerprint, resolve, resolve_cached, understand
import study_registry as sr
from collection.load_reviewed import validate as validate_observations


def understanding(studies=(), **kwargs):
    return {"intent": "fact_lookup", "products": ["CTP43"], "studies": list(studies),
            "jurisdictions": ["EU"], "doc_groups": ["assessment"], "topic": "design",
            "english_query": "study design", "keywords": [], "search_terms": "study design", **kwargs}


def chunk(doc="doc", product="CTP43", jurisdiction="EU", version="v1", page=1, text="study design", studies=(), group="assessment"):
    return {"chunk_id": f"{doc}:{version}:{page}:n0", "document_id": doc, "version_id": version,
            "pdf_page": page, "product_id": product, "jurisdiction": jurisdiction, "doc_group": group,
            "content_hash": text, "doc_date": "2026-01-01", "locator_type": "pdf_page",
            "document_type": "review", "title": doc, "section": None, "source_kind": "native",
            "redaction_markers": 0, "header": "", "text": text, "studies": list(studies)}


class ScopeTests(unittest.TestCase):
    def test_negation_does_not_replace_valid_model_target(self):
        q = "스테키마의 일본인이 아닌 건강인 시험에서 투여량은?"
        self.assertEqual(resolve(q, understanding(["ST_CTP43_1_1"]))["studies"], ["ST_CTP43_1_1"])

    def test_paraphrases_do_not_clear_targets(self):
        for q, key in [("스테키마의 일본 사람을 모집한 연구", "ST_CTP43_1_2"),
                       ("두 건강인 연구의 자료를 결합한 결과", "ST_CTP43_POOLED"),
                       ("두 번째 약동학 연구 결과", "ST_CTP43_1_2")]:
            with self.subTest(q=q):
                self.assertEqual(resolve(q, understanding([key]))["studies"], [key])

    def test_explicit_components_do_not_override_pooled_target(self):
        q = "CT-P43 1.1 Part 2와 1.2 통합 분석에서 Cmax는?"
        u = understanding(["ST_CTP43_POOLED"], study_scope="specified", study_mentions=[
            {"study_key": "ST_CTP43_1_1_P2", "role": "component", "quote": "CT-P43 1.1 Part 2"},
            {"study_key": "ST_CTP43_POOLED", "role": "target", "quote": "CT-P43 1.1 Part 2와 1.2 통합 분석"}])
        self.assertEqual(resolve(q, u)["studies"], ["ST_CTP43_POOLED"])

    def test_redundant_component_selection_does_not_clear_valid_target(self):
        q = "두 연구의 통합 분석"
        u = understanding(["ST_CTP43_1_1_P2", "ST_CTP43_1_2", "ST_CTP43_POOLED"], study_scope="specified", study_mentions=[
            {"study_key": k, "role": role, "quote": q} for k, role in
            [("ST_CTP43_1_1_P2", "component"), ("ST_CTP43_1_2", "component"), ("ST_CTP43_POOLED", "target")]])
        self.assertEqual(resolve(q, u)["studies"], ["ST_CTP43_POOLED"])

    def test_part_not_collapsed(self):
        self.assertEqual(resolve("Part 2", understanding(["ST_CTP43_1_1_P2"]))["studies"], ["ST_CTP43_1_1_P2"])

    def test_grounding_failure_broadens_scope(self):
        u = understanding(["ST_CTP43_1_2"], study_scope="specified", study_mentions=[
            {"study_key": "ST_CTP43_1_2", "role": "target", "quote": "일본인"}])
        r = resolve("두 번째 연구의 결과는?", u)
        self.assertEqual((r["studies"], r["study_scope"]), ([], "ambiguous"))
        self.assertTrue(r["scope_warnings"])

    def test_exclusion_conflict_broadens_scope(self):
        u = understanding(["ST_CTP43_1_2"], study_scope="specified", study_mentions=[
            {"study_key": "ST_CTP43_1_2", "role": role, "quote": "일본인이 아닌"} for role in ("target", "excluded")])
        self.assertEqual(resolve("일본인이 아닌 시험", u)["studies"], [])

    def test_reference_region_is_not_agency(self):
        u = understanding(jurisdiction_mentions=[{"jurisdiction": "EU", "role": "comparator", "quote": "EU-Stelara"}])
        self.assertEqual(resolve("EU-Stelara 대비 Cmax", u)["jurisdictions"], [])

    def test_agency_and_reference_region_can_coexist(self):
        u = understanding(jurisdiction_mentions=[{"jurisdiction": "EU", "role": "regulator", "quote": "EMA는"},
                                                {"jurisdiction": "US", "role": "comparator", "quote": "US-Stelara"}])
        self.assertEqual(resolve("EMA는 US-Stelara 비교를 어떻게 평가했나?", u)["jurisdictions"], ["EU"])

    def test_unspecified_topics_do_not_restrict_phase(self):
        for topic in ("design", "dosing", "endpoints", "efficacy", "pk_results", "safety", "other"):
            with self.subTest(topic=topic):
                unit = plan(understanding(topic=topic))[0]
                self.assertEqual(unit["studies"], [])
                self.assertEqual(set(unit["study_candidates"]), set(sr.studies_of("CTP43")))

    def test_multiple_products_have_independent_scopes(self):
        units = plan(understanding(["ST_SB17_1001"], products=["SB17", "CTP43"]))
        self.assertEqual(units[0]["studies"], ["ST_SB17_1001"])
        self.assertEqual(units[1]["studies"], [])
        self.assertIn("ST_CTP43_3_1", units[1]["study_candidates"])

    def test_out_of_scope_does_not_search(self):
        self.assertEqual(plan(understanding(intent="out_of_scope")), [])

    def test_query_change_invalidates_cache(self):
        self.assertNotEqual(cache_fingerprint("질문 A"), cache_fingerprint("질문 B"))

    def test_cache_preserves_original_scope_for_revalidation(self):
        raw = understanding(["ST_CTP43_1_2"], study_scope="specified", study_mentions=[
            {"study_key": "ST_CTP43_1_2", "role": "target", "quote": "일본인 시험"}])
        cached = {**raw, "studies": [], "study_scope": "ambiguous", "model_interpretation": raw}
        self.assertEqual(resolve_cached("일본인 시험", cached)["studies"], ["ST_CTP43_1_2"])

    def test_nonliteral_role_quote_gets_one_repair_attempt(self):
        q = "CT-P43 1.1 Part 2와 1.2 통합 분석"
        raw = understanding(["ST_CTP43_POOLED"], jurisdictions=[], study_scope="specified", jurisdiction_mentions=[],
                            study_mentions=[{"study_key": "ST_CTP43_POOLED", "role": "target", "quote": "CT-P43 1.1 Part 2 and 1.2"}],
                            english_query="CT-P43 1.1 Part 2 and 1.2 pooled analysis")
        fixed = {**raw, "study_mentions": [{"study_key": "ST_CTP43_POOLED", "role": "target", "quote": q}]}
        def response(out):
            return {"text": json.dumps(out), "prompt_tokens": 1, "completion_tokens": 1, "model_reported": "test"}
        with patch("understand_query.chat", side_effect=[response(raw), response(fixed)]) as client:
            result = understand(q, glossary=[])
        self.assertEqual(client.call_count, 2)
        self.assertEqual(result["studies"], ["ST_CTP43_POOLED"])
        self.assertEqual(result["model_interpretation"], fixed)


class RetrievalTests(unittest.TestCase):
    def test_split_registry_id_is_not_a_section(self):
        breaks, sections, _, _ = scan_sections(["Study CT-P43", "1.1 Part 2", "2.1 Methods"], None, product="CTP43")
        self.assertNotIn(1, breaks)
        self.assertIsNone(sections[1])
        self.assertIn(2, breaks)

    def test_split_id_supports_new_registry_products(self):
        registry = {"ST_NEW": {"study_key": "ST_NEW", "product_id": "NEW", "aliases": [r"NEW\s+7\.1"]}}
        with patch.object(sr, "load", return_value=registry):
            sr._patterns.cache_clear()
            try:
                breaks, sections, _, _ = scan_sections(["Study NEW", "7.1 Part 2"], None, product="NEW")
                self.assertFalse(breaks)
                self.assertIsNone(sections[1])
            finally:
                sr._patterns.cache_clear()

    def test_wrong_tag_does_not_exclude_best_source(self):
        cs = [chunk("relevant", text="highly relevant", studies=["ST_CTP43_3_1"]),
              chunk("tagged", text="irrelevant", studies=["ST_CTP43_1_2"])]
        vec = np.array([[1., 0.], [0., 1.]])
        class EmptyBm:
            def hits(self, *_):
                return []
        pages = search(np.array([1., 0.]), "", np.array([0, 1]), cs, vec, EmptyBm(), ["ST_CTP43_1_2"], "E", k=2)
        self.assertEqual({p["document_id"] for p in pages}, {"relevant", "tagged"})

    def test_broad_document_fallback_respects_agency_product(self):
        cs = [chunk("protocol", group="protocol", text="study design dose 45 mg"),
              chunk("wrongagency", jurisdiction="US", text="study design dose 45 mg"),
              chunk("wrongproduct", product="SB17", text="study design dose 45 mg")]
        corpus = type("CorpusFixture", (), {"chunks": cs, "vec": None, "bm": Bm25(cs)})()
        pages = retrieve(corpus, understanding(), None, use_dense=False, policy="general")[0][1]
        self.assertEqual([p["document_id"] for p in pages], ["protocol"])
        self.assertEqual(pages[0]["version_id"], "v1")

    def test_duplicate_text_does_not_merge_products_or_agencies(self):
        cs = [chunk("a"), chunk("b", product="SB17"), chunk("c", jurisdiction="US")]
        self.assertEqual(len(to_pages([(1., i) for i in range(3)], cs, 5)), 3)

    def test_duplicate_locations_retain_versions(self):
        cs = [chunk("a"), chunk("b", version="v2")]
        page = to_pages([(1., 0), (.9, 1)], cs)[0]
        self.assertEqual(set(page["locations"]), {("a", "v1", 1), ("b", "v2", 1)})

    def test_parent_is_only_context_for_a_part(self):
        self.assertGreater(preference(["ST_CTP43_1_1_P2"], ["ST_CTP43_1_1_P2"]),
                           preference(["ST_CTP43_1_1"], ["ST_CTP43_1_1_P2"]))

    def test_unattributed_is_unknown(self):
        c = chunk(text="copyright address")
        self.assertEqual(annotate([c])[0]["study_scope"], "unknown")
        self.assertEqual(c["study_attribution_status"], "heuristic_unverified")

    def test_study_carry_does_not_cross_document_versions(self):
        cs = [chunk(version="v1", text="Study CT-P43 1.1 assessed pharmacokinetics in healthy subjects."),
              chunk(version="v2", page=2, text="This section provides methodological details without naming a clinical trial. " * 4)]
        annotate(cs)
        self.assertTrue(cs[0]["studies"])
        self.assertEqual(cs[1]["studies"], [])

    def test_large_selected_table_keeps_its_footnote(self):
        c = Corpus.__new__(Corpus)
        text = "Table 1\n" + "A | 1.0124 (0.9289, 1.1034)\n" * 300 + "Note: analysis set and comparison are specified here."
        c.chunks = [chunk(text=text)]
        c.by_page = {("doc", "v1", 1): [0]}
        self.assertTrue(c.page_text("doc", 1, version_id="v1").endswith("Note: analysis set and comparison are specified here."))

    def test_page_version_is_required_when_ambiguous(self):
        c = Corpus.__new__(Corpus)
        c.by_page = {("doc", "v1", 1): [], ("doc", "v2", 1): []}
        with self.assertRaises(ValueError):
            c.page_text("doc", 1)


class AnswerEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.ev = {"E1": {"text": "Study CT-P43 1.2 used a single dose of 45 mg."}}
        self.it = {"study": "ST_CTP43_1_2", "item": "dose", "source_label": "single dose", "value": "45 mg",
                   "quote": self.ev["E1"]["text"], "exact": True, "evidence": ["E1"]}

    def result(self, item):
        return validate_answer({"status": "answered", "items": [item], "answer_ko": "45 mg [E1]"}, self.ev)

    def test_valid_quote_is_kept(self):
        self.assertEqual(len(self.result(self.it)["items"]), 1)

    def test_nonexistent_citation_is_removed(self):
        r = self.result({**self.it, "evidence": ["E99"]})
        self.assertEqual(r["items"], [])
        self.assertNotIn("45 mg", r["answer_ko"])

    def test_value_outside_quote_is_removed(self):
        self.assertEqual(self.result({**self.it, "value": "90 mg"})["status"], "not_found")

    def test_quote_is_checked_against_cited_block_only(self):
        self.ev["E2"] = {"text": "Not reported."}
        self.assertEqual(self.result({**self.it, "evidence": ["E2"]})["items"], [])

    def test_whitespace_normalization_preserves_literal_numbers(self):
        self.assertTrue(contains_quote("45\nmg", "45 mg"))
        self.assertFalse(contains_quote("145 mg", "45.0 mg"))

    def test_value_cannot_be_a_fragment_of_another_number(self):
        self.assertFalse(contains_value("145 mg", "45 mg"))
        self.assertFalse(contains_value("0.45 mg", "45 mg"))
        self.assertFalse(contains_value("99.4%", "99"))
        self.assertTrue(contains_value("Dose: 45 mg.", "45 mg"))

    def test_cache_cannot_reuse_citation_from_another_version(self):
        class FixedCorpus:
            def page_text(corpus, *args):
                return self.ev["E1"]["text"]
        model_result = {"text": json.dumps({"status": "answered", "items": [self.it], "answer_ko": "45 mg [E1]"}),
                        "model_reported": "test", "prompt_tokens": 0, "completion_tokens": 0}
        cache = {}
        unit = plan(understanding())[0]
        page = to_pages([(1., 0)], [chunk()])[0]
        with patch("answer.chat", return_value=model_result) as client:
            a = answer_unit("dose?", understanding(), unit, [page], FixedCorpus(), cache)
            b = answer_unit("dose?", understanding(), unit, [{**page, "version_id": "v2"}], FixedCorpus(), cache)
        self.assertEqual(client.call_count, 2)
        self.assertEqual(a["evidence"]["E1"]["version_id"], "v1")
        self.assertEqual(b["evidence"]["E1"]["version_id"], "v2")

    def test_cached_values_are_revalidated_without_another_model_call(self):
        class FixedCorpus:
            def page_text(corpus, *args):
                return self.ev["E1"]["text"]
        response = {"text": json.dumps({"status": "answered", "items": [self.it], "answer_ko": "45 mg [E1]"}),
                    "model_reported": "test", "prompt_tokens": 0, "completion_tokens": 0}
        cache = {}
        unit = plan(understanding())[0]
        pages = to_pages([(1., 0)], [chunk()])
        with patch("answer.chat", return_value=response) as client:
            answer_unit("dose?", understanding(), unit, pages, FixedCorpus(), cache)
            next(iter(cache.values()))["items"][0]["value"] = "5 mg"
            result = answer_unit("dose?", understanding(), unit, pages, FixedCorpus(), cache)
        self.assertEqual(client.call_count, 1)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["status"], "not_found")


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("create table pages(document_id text,version_id text,pdf_page integer,product_id text)")
        self.db.execute("insert into pages values ('doc','v1',1,'SB17')")
        self.row = {"observation_id": "o1", "study_id": "SB17-1001", "product_id": "SB17",
                    "evidence": {"document_id": "doc", "version_id": "v1", "pdf_page": 1},
                    "raw_scale": "percent", "raw_value": "99", "raw_ci_lower": "90", "raw_ci_upper": "108",
                    "value_ratio": .99, "ci_lower_ratio": .9, "ci_upper_ratio": 1.08, "ci_level": .9}

    def tearDown(self):
        self.db.close()

    def test_canonical_key_does_not_change_recorded_identifier(self):
        row = validate_observations(self.db, [self.row])[0]
        self.assertEqual(row["study_key"], "ST_SB17_1001")
        self.assertEqual(row["study_id"], "SB17-1001")

    def test_wrong_version_rejected(self):
        with self.assertRaises(ValueError):
            validate_observations(self.db, [{**self.row, "evidence": {**self.row["evidence"], "version_id": "v2"}}])

    def test_wrong_product_rejected(self):
        with self.assertRaises(ValueError):
            validate_observations(self.db, [{**self.row, "product_id": "ABP654"}])

    def test_ci_scale_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            validate_observations(self.db, [{**self.row, "raw_ci_lower": "80"}])

    def test_unknown_study_rejected(self):
        with self.assertRaises(ValueError):
            validate_observations(self.db, [{**self.row, "study_id": "SB17-9999"}])

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            validate_observations(self.db, [self.row, self.row])

    def test_complete_part_identifier_is_resolved(self):
        self.assertEqual(sr.identifier_key("CT-P43 1.1 Part 2", "CTP43"), "ST_CTP43_1_1_P2")
        self.assertIsNone(sr.identifier_key("일본인 대상 시험", "CTP43"))


if __name__ == "__main__":
    unittest.main()
