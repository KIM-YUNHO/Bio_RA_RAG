"""Section boundaries and scoped metadata must preserve source ownership."""
import json
import hashlib
import sqlite3
import tempfile
import sys
import unittest
from pathlib import Path

from rag.index import build_chunks as bc, context_units as cu, document_structure as ds
from rag.index.chunking import pack_units


class Counter:
    def one(self, text):
        return len(text)


def refs(texts):
    return [{"block_id": f"b{i}", "bbox_pdf": [0, i * 20, 100, i * 20 + 10],
             "line_start": 0, "line_end": 0, "joiner": "\n"} for i in range(len(texts))]


class StructureTests(unittest.TestCase):
    def test_same_page_study_switch_is_a_hard_boundary(self):
        tracker = ds.Tracker("doc", "v1", "CTP43")
        texts = ["2.3. Clinical aspects", "Study CT-P43 1.1", "Methods", "old trial body",
                 "Study CT-P43 1.2", "Methods", "Japanese trial body"]
        breaks, _, _, states = tracker.scan(texts, refs(texts), 31)
        chunks = pack_units(texts, Counter(), 200, 150, 40, breaks)
        self.assertTrue(all(not ("old trial body" in c.text and "Study CT-P43 1.2" in c.text) for c in chunks))
        self.assertNotEqual(states[3]["section_id"], states[6]["section_id"])
        self.assertEqual(states[3]["heading_studies"], ["ST_CTP43_1_1"])
        self.assertEqual(states[6]["heading_studies"], ["ST_CTP43_1_2"])
        self.assertEqual(states[6]["path"], ["2.3. Clinical aspects", "Study CT-P43 1.2", "Methods"])
        _, _, _, continuation = tracker.scan(["continued results"], refs(["continued results"]), 32)
        self.assertEqual(continuation[0]["section_id"], states[6]["section_id"])
        self.assertEqual(continuation[0]["heading_anchors"][1]["heading_location"]["pdf_page"], 31)

    def test_prose_toc_and_wrapped_identifier_are_not_headings(self):
        self.assertIsNone(ds.identify("As in Study CT-P43 1.2, the same method was used.", "CTP43"))
        self.assertIsNone(ds.identify("2.3. Clinical aspects .... 31", "CTP43"))
        self.assertIsNone(ds.identify("Study SB17-1001)", "SB17"))
        self.assertIsNone(ds.identify("clinical study 20190230.", "ABP654"))
        self.assertEqual(ds.identify("Clinical Phase I study (SB17-1001)", "SB17")["study_keys"], ["ST_SB17_1001"])
        tracker = ds.Tracker("doc", "v1", "CTP43")
        texts = ["Study CT-P43", "1.1 Part 2, a separate analysis"]
        breaks, _, _, _ = tracker.scan(texts, refs(texts), 31)
        self.assertFalse(breaks)

    def test_side_by_side_table_labels_are_not_headings(self):
        texts = ["Results", "CT-P43 1.1", "123"]
        positions = refs(texts)
        for i, ref in enumerate(positions):
            ref["bbox_pdf"] = [i * 110, 10, i * 110 + 100, 20]
        tracker = ds.Tracker("doc", "v1", "CTP43")
        breaks, _, _, _ = tracker.scan(texts, positions, 31)
        self.assertFalse(breaks)

    def test_dense_real_subsections_are_not_suppressed_as_toc(self):
        texts = [text for i in range(1, 9) for text in (
            f"2.2.{i}. Source subsection", "This is the actual narrative body accompanying this source subsection.")]
        tracker = ds.Tracker("doc", "v1", "CTP43")
        breaks, _, _, states = tracker.scan(texts, refs(texts), 26)
        self.assertEqual(len(breaks), 8)
        self.assertEqual(states[-1]["path"], ["2.2.8. Source subsection"])
        headings = texts[::2]
        toc = ds.Tracker("doc", "v1", "CTP43")
        breaks, _, _, _ = toc.scan(headings, refs(headings), 2)
        self.assertFalse(breaks)

    def test_html_subheadings_keep_original_locators_and_replace_branches(self):
        raw = "<main><h2>Clinical Basis</h2><h3>Comparative Pharmacokinetics</h3><h3>Comparative Clinical Efficacy</h3></main>"
        texts = ["Clinical Basis", "Comparative Pharmacokinetics", "Methods", "body", "Comparative Clinical Efficacy", "new body"]
        pages = [{"pdf_page": 13, "text": "\n".join(texts)}]
        mapped = ds.html_heading_map(raw, pages, bc.clean_line)
        self.assertEqual(set(mapped), {(13, 0), (13, 1), (13, 4)})
        positions = refs(texts)
        for i, ref in enumerate(positions):
            ref["line_start"] = ref["line_end"] = i
        tracker = ds.Tracker("doc", "v1", "CTP43")
        _, _, _, states = tracker.scan(texts, positions, 13, explicit={i: h for (p, i), h in mapped.items()})
        self.assertEqual(states[-1]["path"], ["Clinical Basis", "Comparative Clinical Efficacy"])
        self.assertEqual(states[-1]["heading_anchors"][-1]["heading_location"]["pdf_page"], 13)

    def test_ranges_inherit_only_where_child_uses_source(self):
        assignments = [{"assignment_id": "a", "study_key": "one", "evidence": ["proof"],
                        "status": "candidate", "applies_to": [{"unit_id": "u", "start": 5, "end": 15}]}]
        children = {"old": {"source_ranges": [{"unit_id": "u", "start": 0, "end": 5}]},
                    "mix": {"source_ranges": [{"unit_id": "u", "start": 0, "end": 10}]},
                    "new": {"source_ranges": [{"unit_id": "other", "start": 0, "end": 20}]}}
        got = cu.inherit(assignments, children)
        self.assertEqual(got["old"]["status"], "unresolved")
        self.assertEqual(got["new"]["status"], "unresolved")
        self.assertEqual(got["mix"]["contexts"][0]["applies_to"], [{"unit_id": "u", "start": 5, "end": 10}])
        self.assertEqual(got["mix"]["contexts"][0]["evidence"], ["proof"])
        self.assertEqual(got["mix"]["status"], "partial_candidate")
        self.assertEqual(got["mix"]["unresolved_ranges"], [{"unit_id": "u", "start": 0, "end": 5}])

    def test_source_identity_is_stable_across_retrieval_splits(self):
        span = {"block_id": "b", "line_start": 1, "line_end": 2, "joiner": "\n", "normalized_start": 0, "normalized_end": 10}
        self.assertEqual(cu.identity("doc", "v1", 1, "native", None, span)[0],
                         cu.identity("doc", "v1", 1, "ocr_region", 3, {**span, "normalized_start": 5})[0])
        self.assertNotEqual(cu.identity("doc", "v1", 1, "native", None, span)[0],
                            cu.identity("doc", "v2", 1, "native", None, span)[0])

    def test_materialized_parent_remains_section_and_has_exact_child_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            with sqlite3.connect(root / "data/collection.sqlite") as db:
                db.execute("create table ocr_regions (document_id, version_id, pdf_page, region, text)")
            db.close()
            text = "Trial body text"
            page = {"pdf_page": 1, "blocks": [{"block_id": "b", "text": text}], "text": text}
            payload = (json.dumps(page) + "\n").encode()
            (root / "pages.jsonl").write_bytes(payload)
            span = {"block_id": "b", "line_start": 0, "line_end": 0, "joiner": "\n", "unit_index": 0,
                    "normalized_start": 6, "normalized_end": len(text), "bbox_pdf": None}
            chunk = {"chunk_id": "child", "document_id": "doc", "version_id": "v1", "product_id": "CTP43", "pdf_page": 1,
                "source_kind": "native", "region": None, "provenance": {"parsed_path": "pages.jsonl",
                    "parsed_sha256": hashlib.sha256(payload).hexdigest(), "source_spans": [span]},
                "structure": {"parent_id": "doc:v1:0001", "section_id": "section", "ancestor_section_ids": ["section"],
                    "heading_studies": [], "table_id": None, "heading_anchors": [{"section_id": "section", "title": "Methods",
                    "kind": "subsection", "parent_section_id": None, "heading_location": {"pdf_page": 1}}]}}
            units, parents, children = cu.materialize([chunk], root)
        self.assertEqual(parents["section"]["kind"], "section")
        self.assertEqual(parents["section"]["heading_kind"], "subsection")
        uid = next(iter(units))
        self.assertEqual(units[uid]["text"], text)
        self.assertEqual(children["child"]["source_ranges"], [{"unit_id": uid, "start": 6, "end": len(text)}])
        self.assertEqual(len(list(cu.windows(units, parents, Counter(), 100))), 1)


class ContextSafetyTests(unittest.TestCase):
    def test_assay_flow_and_recruitment_are_separate_boundaries(self):
        texts = ["Analysis populations", "population body", "ADA assay", "assay body", "NAb assay", "neutralising body", "Patient flow", "flow body"]
        tracker = ds.Tracker("doc", "v1", "SB17")
        breaks, _, _, states = tracker.scan(texts, refs(texts), 42)
        self.assertEqual(breaks, {0, 2, 4, 6})
        self.assertEqual(states[5]["path"], ["NAb assay"])
        self.assertEqual(ds.identify("Primary objective (Part 2):", "CTP43")["kind"], "detail")

    def test_pooled_heading_closes_preceding_single_study_scope(self):
        tracker = ds.Tracker("doc", "v1", "CTP43")
        texts = ["2.3. Clinical aspects", "Study CT-P43 3.1", "Results", "old results",
                 "Post-Hoc pooled analysis", "pooled results"]
        _, _, _, states = tracker.scan(texts, refs(texts), 51)
        self.assertEqual(states[-1]["heading_studies"], [])
        self.assertEqual(states[-1]["path"], ["2.3. Clinical aspects", "Post-Hoc pooled analysis"])

    def test_conflicting_analyses_are_flagged_but_distinct_studies_are_allowed(self):
        span = {"unit_id": "u", "start": 0, "end": 10}
        a = {"assignment_id": "a", "study_key": "trial", "role": "subject", "analysis": "primary", "applies_to": [span]}
        b = {**a, "assignment_id": "b", "analysis": "pooled"}
        children = {"child": {"source_ranges": [span]}}
        self.assertEqual(cu.inherit([a, b], children)["child"]["status"], "conflict")
        self.assertEqual(cu.inherit([a, {**b, "study_key": "other"}], children)["child"]["conflicts"], [])
        self.assertEqual(cu.inherit([a, {**b, "analysis": "unknown"}], children)["child"]["conflicts"], [])

    def test_no_study_conflicts_with_clinical_subject_on_same_range(self):
        span = {"unit_id": "u", "start": 0, "end": 10}
        a = {"assignment_id": "a", "study_key": "trial", "role": "subject", "applies_to": [span]}
        b = {"assignment_id": "b", "study_key": None, "role": "no_study", "applies_to": [span]}
        self.assertEqual(cu.inherit([a, b], {"c": {"source_ranges": [span]}})["c"]["status"], "conflict")

    def test_complementary_analysis_dimensions_do_not_conflict(self):
        span = {"unit_id": "u", "start": 0, "end": 10}
        a = {"assignment_id": "a", "study_key": "trial", "analysis": "pooled", "analysis_details": {"aggregation": "pooled", "timing": "unknown", "intent": "unknown"}, "applies_to": [span]}
        b = {"assignment_id": "b", "study_key": "trial", "analysis": "sensitivity_or_post_hoc", "analysis_details": {"aggregation": "unknown", "timing": "post_hoc", "intent": "unknown"}, "applies_to": [span]}
        children = {"c": {"source_ranges": [span]}}
        self.assertEqual(cu.inherit([a, b], children)["c"]["conflicts"], [])
        b["analysis_details"]["aggregation"] = "single_study"
        self.assertEqual(cu.inherit([a, b], children)["c"]["conflicts"][0]["fields"], ["analysis_details.aggregation"])

    def test_line_rejoining_preserves_coordinates_and_stops_at_heading_or_page(self):
        def unit(i, text, page=1):
            return {"unit_id": str(i), "product_id": "SB17", "text": text, "pdf_page": page,
                    "source_span": {"block_id": "b", "line_start": i, "line_end": i}, "section_id": "s"}
        units = [unit(0, "First source line"), unit(1, "continued line"), unit(2, "NAb assay"),
                 unit(3, "new assay text"), unit(4, "next page", 2)]
        groups = cu.coalesce(units, Counter(), 200)
        self.assertEqual(len(groups), 4)
        self.assertEqual(groups[0]["text"], "First source line\ncontinued line")
        self.assertEqual([s["unit_id"] for s in groups[0]["source_ranges"]], ["0", "1"])

    def test_related_methods_from_same_study_never_become_targets(self):
        units = {"m": {"unit_id": "m", "text": "Methods describe the actual design of this clinical study.", "section_id": "methods", "product_id": "CTP43"},
                 "r": {"unit_id": "r", "text": "Results report the clinical outcomes for the same study.", "section_id": "results", "product_id": "CTP43"}}
        parents = {"study": {"context_id": "study", "kind": "section", "heading_kind": "study", "title": "Study CT-P43 1.1", "heading_location": {"pdf_page": 31}, "unit_ids": ["m", "r"]},
                   "methods": {"context_id": "methods", "kind": "section", "title": "Methods", "parent_section_id": "study", "unit_ids": ["m"]},
                   "results": {"context_id": "results", "kind": "section", "title": "Results", "parent_section_id": "study", "unit_ids": ["r"]}}
        result = next(w for w in cu.windows(units, parents, Counter(), 500) if w["context_id"] == "results")
        self.assertEqual([u["unit_id"] for u in result["related"]], ["m"])
        self.assertEqual([u["unit_id"] for u in result["units"]], ["r"])


if __name__ == "__main__":
    unittest.main()
