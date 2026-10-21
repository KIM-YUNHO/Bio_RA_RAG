"""Source integrity and coverage failures in the offline inventory workflow."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from preprocess.detect import scan_page
from preprocess.ingest import Document, from_catalog, from_inputs, sha256
from preprocess.inventory import run_inventory
from preprocess.schema import validate_record


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def native_pdf(path, rotated=False):
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=400)
        page.insert_text((35, 35), "Table 1. Study overview")
        for x in (30, 140, 250):
            page.draw_line((x, 50), (x, 140))
        for y in (50, 80, 110, 140):
            page.draw_line((30, y), (250, y))
        for x, y, text in ((40, 70, "Study"), (150, 70, "Design"), (40, 100, "Trial A"),
                           (150, 100, "Parallel"), (40, 130, "Trial B"), (150, 130, "Crossover")):
            page.insert_text((x, y), text)
        if rotated:
            page.set_rotation(90)
        pdf.save(path)


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "result"

    def tearDown(self):
        self.temp.cleanup()

    def run_inventory(self, docs, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_inventory(docs, self.out, workers=1, **kwargs)

    def test_integer_free_native_table_is_detected_without_vlm_or_network(self):
        path = self.root / "new.PDF"
        native_pdf(path)
        docs = from_inputs([path], self.out)
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")):
            summary = self.run_inventory(docs)
        assets = read_rows(self.out / "assets.jsonl")
        tables = [a for a in assets if "native_grid_lines" in a["signals"]]
        self.assertTrue(tables)
        self.assertEqual(tables[0]["next_step"], "native_table_extract")
        self.assertFalse(tables[0]["numeric_evidence_eligible"])
        self.assertEqual(summary["vlm_calls"], 0)
        self.assertEqual(summary["pdf_pages"], 1)
        self.assertEqual(sha256(path), docs[0].version_id)

    def test_absence_of_candidates_remains_unknown_not_certified_empty(self):
        path = self.root / "prose.pdf"
        with fitz.open() as pdf:
            pdf.new_page().insert_text((50, 50), "A short plain paragraph.")
            pdf.save(path)
        self.run_inventory(from_inputs([path], self.out))
        document = read_rows(self.out / "documents.jsonl")[0]
        page = read_rows(self.out / "pages.jsonl")[0]
        self.assertIsNone(document["has_table_candidate"])
        self.assertIsNone(document["has_figure_candidate"])
        self.assertTrue(page["needs_layout_check"])
        self.assertEqual(page["coverage_status"], "layout_unassessed")

    def test_duplicate_uploads_share_snapshot_but_keep_registration_paths(self):
        a, b = self.root / "a.pdf", self.root / "b.pdf"
        native_pdf(a)
        b.write_bytes(a.read_bytes())
        docs = from_inputs([self.root], self.out)
        self.assertEqual(len(docs), 1)
        self.assertEqual(set(docs[0].source_paths), {str(a), str(b)})
        self.assertEqual(sha256(docs[0].raw_path), sha256(a))

    def test_checkpoint_reuse_avoids_page_redetection_and_keeps_asset_ids(self):
        path = self.root / "table.pdf"
        native_pdf(path)
        docs = from_inputs([path], self.out)
        self.run_inventory(docs)
        before = (self.out / "assets.jsonl").read_bytes()
        with patch("preprocess.inventory.scan_page", side_effect=AssertionError("Must use checkpoint")):
            summary = self.run_inventory(docs)
        self.assertEqual(summary["checkpoint_units_reused"], 1)
        self.assertEqual(before, (self.out / "assets.jsonl").read_bytes())

    def test_registered_hash_mismatch_fails_instead_of_reusing_old_results(self):
        path = self.root / "table.pdf"
        native_pdf(path)
        docs = from_inputs([path], self.out)
        self.run_inventory(docs)
        Path(docs[0].raw_path).write_bytes(b"changed content")
        summary = self.run_inventory(docs)
        document = read_rows(self.out / "documents.jsonl")[0]
        self.assertEqual(summary["document_statuses"], {"failed": 1})
        self.assertEqual(summary["candidate_regions"], 0)
        self.assertIn("checksum", document["warnings"][0])

    def test_changed_detection_settings_invalidate_page_cache(self):
        path = self.root / "table.pdf"
        native_pdf(path)
        docs = from_inputs([path], self.out)
        self.run_inventory(docs, strategies=("lines",))
        summary = self.run_inventory(docs, strategies=("lines", "text"))
        self.assertEqual(summary["checkpoint_units_reused"], 0)

    def test_html_offsets_and_external_images_without_fabricated_pdf_pages(self):
        text = '<html>한글<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table><img src="https://example.invalid/p.png"></html>'
        path = self.root / "doc.html"
        path.write_text(text, encoding="utf-8")
        with patch("urllib.request.urlopen", side_effect=AssertionError("Must not fetch HTML images")):
            summary = self.run_inventory(from_inputs([path], self.out))
        assets = read_rows(self.out / "assets.jsonl")
        table = next(a for a in assets if a["asset_type"] == "table")
        ref = table["source_refs"][0]
        self.assertEqual(text[ref["start"]:ref["end"]], text[text.index("<table>"):text.index("</table>") + len("</table>")])
        self.assertIsNone(table["pdf_page"])
        self.assertIsNone(table["bbox_pdf"])
        self.assertEqual(table["structure_hint"]["merged_cells"], 1)
        self.assertEqual(summary["html_documents_assessed"], 1)
        self.assertEqual(summary["pdf_pages"], 0)

    def test_captionless_vector_graphic_is_retained_as_unknown(self):
        path = self.root / "vector.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            page.draw_circle((150, 150), 40)
            page.insert_text((50, 50), "Study diagram")
            pdf.save(path)
        self.run_inventory(from_inputs([path], self.out))
        assets = read_rows(self.out / "assets.jsonl")
        self.assertTrue(any(a["modality"] == "vector" and a["asset_type"] == "unknown_visual" for a in assets))

    def test_rotated_page_retains_physical_page_and_unrotated_geometry(self):
        path = self.root / "rotated.pdf"
        native_pdf(path, rotated=True)
        self.run_inventory(from_inputs([path], self.out))
        page = read_rows(self.out / "pages.jsonl")[0]
        self.assertEqual(page["page_geometry"]["rotation"], 90)
        table = next(a for a in read_rows(self.out / "assets.jsonl") if "native_grid_lines" in a["signals"])
        self.assertEqual(table["pdf_page"], 1)
        self.assertAlmostEqual(table["bbox_pdf"][0], 30, places=1)

    def test_page_failures_are_reported_and_retried(self):
        path = self.root / "table.pdf"
        native_pdf(path)
        docs = from_inputs([path], self.out)
        with patch("preprocess.inventory.scan_page", side_effect=RuntimeError("detector failed")):
            summary = self.run_inventory(docs)
        self.assertEqual(summary["failed_pages"], 1)
        self.assertEqual(summary["document_statuses"], {"partial": 1})
        summary = self.run_inventory(docs)
        self.assertEqual(summary["failed_pages"], 0)
        self.assertEqual(summary["checkpoint_units_reused"], 0)

    def test_catalog_keeps_unknown_metadata_and_checks_original_checksum(self):
        path = self.root / "table.pdf"
        native_pdf(path)
        catalog = self.root / "catalog.jsonl"
        catalog.write_text(json.dumps({"document_id": "new:document", "version_id": sha256(path),
            "raw_path": path.name, "kind": "pdf"}) + "\n", encoding="utf-8")
        docs = from_catalog(catalog, self.root)
        self.assertIsNone(docs[0].metadata["product_id"])
        self.assertEqual(self.run_inventory(docs)["documents"], 1)
        with self.assertRaises(ValueError):
            from_catalog(catalog, self.root, ["missing:document"])

    def test_invalid_html_pdf_locator_is_rejected(self):
        path = self.root / "doc.html"
        path.write_text("<table><tr><td>A</td></tr></table>", encoding="utf-8")
        self.run_inventory(from_inputs([path], self.out))
        asset = read_rows(self.out / "assets.jsonl")[0]
        asset["pdf_page"] = 1
        with self.assertRaises(ValueError):
            validate_record(asset)

    def test_corrupted_checkpoint_is_recomputed(self):
        path = self.root / "table.pdf"
        native_pdf(path)
        docs = from_inputs([path], self.out)
        self.run_inventory(docs)
        checkpoint = next((self.out / "checkpoints").rglob("p00001.json"))
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        saved["page"]["text_chars"] = 9999
        checkpoint.write_text(json.dumps(saved), encoding="utf-8")
        summary = self.run_inventory(docs)
        self.assertEqual(summary["checkpoint_units_reused"], 0)
        self.assertNotEqual(read_rows(self.out / "pages.jsonl")[0]["text_chars"], 9999)

    def test_password_pdf_is_a_reported_failure(self):
        path = self.root / "locked.pdf"
        with fitz.open() as pdf:
            pdf.new_page().insert_text((50, 50), "Locked page")
            pdf.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
        summary = self.run_inventory(from_inputs([path], self.out))
        self.assertEqual(summary["document_statuses"], {"failed": 1})
        self.assertEqual(summary["pdf_pages"], 0)

    def test_outdated_legacy_table_does_not_become_reusable_evidence(self):
        path = self.root / "table.pdf"
        native_pdf(path)
        docs = from_inputs([path], self.out)
        legacy = self.root / "legacy"
        legacy.mkdir()
        docs[0].legacy_dir = str(legacy)
        (legacy / "p001_r1.table.json").write_text(json.dumps({"version_id": "wrong-version", "pdf_page": 1,
            "region": 1, "bbox_pdf": [30, 50, 250, 140], "status": "table_checked"}), encoding="utf-8")
        summary = self.run_inventory(docs)
        self.assertEqual(summary["assets_with_legacy_candidates"], 0)
        # A matching historical pass is still only a reuse candidate.
        (legacy / "p001_r1.table.json").write_text(json.dumps({"version_id": docs[0].version_id, "pdf_page": 1,
            "region": 1, "bbox_pdf": [30, 50, 250, 140], "status": "table_checked"}), encoding="utf-8")
        summary = self.run_inventory(docs)
        self.assertGreater(summary["assets_with_legacy_candidates"], 0)
        self.assertFalse(any(a["numeric_evidence_eligible"] for a in read_rows(self.out / "assets.jsonl")))

    def test_malformed_legacy_result_does_not_block_native_inventory(self):
        path = self.root / "table.pdf"
        native_pdf(path)
        docs = from_inputs([path], self.out)
        legacy = self.root / "legacy"
        legacy.mkdir()
        docs[0].legacy_dir = str(legacy)
        (legacy / "p001_r1.json").write_text(json.dumps({"version_id": docs[0].version_id}), encoding="utf-8")
        self.assertEqual(self.run_inventory(docs)["document_statuses"], {"heuristic_inventory_complete": 1})

    def test_asset_identity_survives_snapshot_relocation(self):
        path = self.root / "table.pdf"
        native_pdf(path)
        self.run_inventory(from_inputs([path], self.out))
        first_ids = [a["asset_id"] for a in read_rows(self.out / "assets.jsonl")]
        self.out = self.root / "other_run"
        self.run_inventory(from_inputs([path], self.out))
        self.assertEqual(first_ids, [a["asset_id"] for a in read_rows(self.out / "assets.jsonl")])

    def test_borderless_alignment_candidate_is_classified_before_extraction(self):
        path = self.root / "unruled.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page()
            for y, label, value in ((50, "Arm", "Count"), (80, "Group A", "10"), (110, "Group B", "12")):
                page.insert_text((50, y), label)
                page.insert_text((250, y), value)
            pdf.save(path)
        self.run_inventory(from_inputs([path], self.out))
        aligned = [a for a in read_rows(self.out / "assets.jsonl") if "native_grid_text" in a["signals"]]
        self.assertTrue(aligned)
        self.assertTrue(all(a["next_step"] == "layout_classify" for a in aligned))


if __name__ == "__main__":
    unittest.main()
