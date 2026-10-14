"""Regression tests for structural boundaries, source integrity and exact budgets."""
import collections
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rag"))
import build_chunks as bc
from chunk_schema import profile_id, validate_dataset
from chunking import Fragment, pack_units, split_fragment


class CharacterCounter:
    def one(self, text):
        return len(text)

    def embedding(self, header, text):
        return len(header + "\n" + text) + 2


class PackingTests(unittest.TestCase):
    def setUp(self):
        self.counter = CharacterCounter()

    def test_short_section_boundary_prevents_mixing_and_overlap(self):
        chunks = pack_units(["old", "new section", "body"], self.counter, 80, 60, 20, breaks={1})
        self.assertEqual([c.text for c in chunks], ["old", "new section\nbody"])
        self.assertEqual(chunks[1].overlap_tokens, 0)

    def test_joined_separator_counts_toward_limit(self):
        chunks = pack_units(["abcde", "fghij"], self.counter, 10, 10, 0)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(self.counter.one(c.text) <= 10 for c in chunks))

    def test_prefix_counts_against_every_part(self):
        chunks = pack_units(["a" * 7, "b" * 7, "c" * 7], self.counter, 15, 15, 0, prefix="Header")
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len("Header\n" + c.text) <= 15 for c in chunks))

    def test_no_space_unicode_preserves_source_and_has_hard_split_warning(self):
        original = "한글문자" * 12
        fragments = split_fragment(Fragment(original, 5), 7, self.counter)
        self.assertEqual("".join(f.text for f in fragments), original)
        self.assertTrue(all(len(f.text) <= 7 and f.hard_split for f in fragments))
        self.assertTrue(all(original[f.start:f.end] == f.text for f in fragments))

    def test_sentence_and_numeric_text_preserve_original_offsets(self):
        original = "Ratio 0.9391 (0.8620, 1.0232). Second sentence has some extra words."
        fragments = split_fragment(Fragment(original, 0), 35, self.counter)
        self.assertEqual(fragments[0].text, "Ratio 0.9391 (0.8620, 1.0232).")
        self.assertTrue(all(original[f.start:f.end] == f.text and len(f.text) <= 35 for f in fragments))

    def test_recurring_body_table_header_is_not_removed_as_boilerplate(self):
        blocks = [{"block_id": str(i), "bbox": [0, i * 10, 100, i * 10 + 8], "text": text}
                  for i, text in enumerate(["Header", "More header", "Treatment | Cmax | AUC", "body", "Footer", "1"])]
        texts, _ = bc.source_units({"blocks": blocks, "text": ""}, {bc.mask_digits("Treatment | Cmax | AUC"), "Header", "Footer"})
        self.assertIn("Treatment | Cmax | AUC", texts)
        self.assertNotIn("Header", texts)

    def test_profile_identity_changes_with_size_or_overlap(self):
        self.assertNotEqual(profile_id(bc.CONFIG), profile_id({**bc.CONFIG, "max_tokens": 512}))
        self.assertNotEqual(profile_id(bc.CONFIG), profile_id({**bc.CONFIG, "overlap_tokens": 0}))

    def test_removed_interior_margin_line_does_not_pollute_source_range(self):
        page = {"text": "", "blocks": [{"block_id": "b", "bbox": [0, 0, 100, 20],
                                         "text": "date\nEMA/106045/2024\nCommittee name\nbody text"}]}
        texts, refs = bc.source_units(page, {"EMA/#/#"})
        self.assertEqual(texts, ["date", "Committee name\nbody text"])
        self.assertEqual([(r["line_start"], r["line_end"]) for r in refs], [(0, 0), (2, 3)])

    def test_table_context_provenance_and_unverified_status_survive_build(self):
        page = {"pdf_page": 1, "pdf_page_label": "1", "text": "", "parser": "fixture", "parser_version": "1",
                "blocks": [{"block_id": "caption", "bbox": [0, 0, 100, 10], "text": "Table 1. PK results"}]}
        row = {"pdf_page": 1, "region": 1, "mode": "region", "text": "Arm | Cmax | AUC\nA | 1.2 | 3.4\nNote: unreviewed",
               "processing_status": "ocr_candidate_decimals_disagree",
               "metadata_json": json.dumps({"bbox_pdf": [0, 20, 100, 100], "table": {"status": "grid_suspect_packed_cells"},
                                            "image_path": "source.png", "text_engine": "fixture"})}
        class DB:
            def execute(self, *_):
                return [row]
        doc = {"document_id": "DOC", "version_id": "v1", "product_id": "CTP43", "source": "EMA", "role": "review",
               "document_type": "assessment-report", "title": "Report", "parsed_path": "page.jsonl", "url": "https://example.org/source"}
        groups = {"doc_group": {"EMA": {"assessment-report": "assessment"}}, "jurisdiction": {"EMA": "EU"},
                  "product_label": {"CTP43": "CT-P43"}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "page.jsonl").write_text(json.dumps(page) + "\n", encoding="utf-8")
            with patch.object(bc, "ROOT", root):
                chunks = bc.build_document(doc, groups, self.counter, DB(), bc.CONFIG, collections.Counter())
        bc.study_context.annotate(chunks)
        validate_dataset(chunks, self.counter)
        table = next(c for c in chunks if c["source_kind"] == "ocr_region")
        self.assertIn("Table 1. PK results\nArm | Cmax | AUC", table["text"])
        self.assertEqual(table["structure"]["part_count"], 1)
        self.assertEqual(table["quality"]["table_status"], "grid_suspect_packed_cells")
        self.assertIn("ocr_numeric_disagreement", table["quality"]["warnings"])
        self.assertEqual(table["provenance"]["source_spans"][0]["block_id"], "caption")
        self.assertEqual(table["analysis_context"]["status"], "unresolved")
        self.assertIn(":v1:", table["chunk_id"])


if __name__ == "__main__":
    unittest.main()
