"""rag.index.portable_paths: the original project's absolute paths resolve to this project's copies."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from rag.index import portable_paths as pp

ORIGINAL = "C:\\Users\\Admin\\Documents\\biosimilar_collection\\"
LATEST = pp.ROOT / "data/versions/03_pipeline_h_v2_table_recovery_2026-10-14"


class Rules(unittest.TestCase):
    def test_relative_in_original(self):
        self.assertEqual(pp.relative_in_original(ORIGINAL + "data\\raw\\a.pdf"), "data/raw/a.pdf")
        self.assertEqual(pp.relative_in_original("D:/work/biosimilar_collection/data/raw/a.pdf"), "data/raw/a.pdf")
        self.assertIsNone(pp.relative_in_original("C:\\Users\\Other\\project\\data\\raw\\a.pdf"))
        self.assertIsNone(pp.relative_in_original("data/raw/a.pdf"))

    def test_mapping_rules(self):
        cases = {
            "data/raw/x.pdf": "data/raw/x.pdf",
            "data/parsed/x.pages.jsonl": "data/parsed/x.pages.jsonl",
            "data/preprocessed/corpus_update_2026-10-09/a/extraction": "data/versions/01_pilot_baseline_2026-10-10/preprocessed/corpus_update_2026-10-09/a/extraction",
            "data/rag/candidates/rag_context_update_2026-10-10_v1/components/ab": "data/versions/01_pilot_baseline_2026-10-10/embedded/components/rag_context_update_2026-10-10_v1/components/ab",
            "data/rag/releases/rag_finalization_2026-10-10_v2/candidate": "data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2/candidate",
            "data/rag/supplemental/ra_guidelines_native_2026-10-12_v2/manifest.json": "data/guidelines/ra_guidelines_native_2026-10-12_v2/manifest.json",
            "data/runs/h_v2_rag_development_2026-10-14_luna_v1/native_candidate_v2/candidate_final": "data/versions/03_pipeline_h_v2_table_recovery_2026-10-14/embedded/candidate_final",
            "pipeline_h/runs/ustekinumab_20261014_v2/extraction": "data/versions/02_pipeline_h_2026-10-14/preprocessed/extraction",
            "pipeline_h/runs/ustekinumab_20261014_v2/release/candidate": "data/versions/02_pipeline_h_2026-10-14/embedded/release/candidate",
            "pipeline_h/export.py": "pipelines/pipeline_h/export.py",
        }
        for rel, want in cases.items():
            self.assertEqual(pp.mapped(rel), pp.ROOT / want, rel)

    def test_other_paths_are_unchanged(self):
        for value in ("data/raw/a.pdf", "C:\\somewhere\\else\\a.pdf", Path("relative/a.pdf")):
            self.assertEqual(pp.resolve(value), Path(value))

    def test_missing_copy_keeps_original(self):
        value = ORIGINAL + "data\\raw\\" + "0" * 64 + ".pdf"
        self.assertEqual(pp.resolve(value), Path(value))

    def test_original_mode_keeps_original(self):
        value = ORIGINAL + "data\\raw\\x.pdf"
        with mock.patch.dict(os.environ, {"BIOSIM_PATHS": "original"}):
            self.assertEqual(pp.resolve(value), Path(value))


@unittest.skipUnless((LATEST / "preprocessed/extraction/extraction_manifest.json").is_file(), "data/versions/03 is not present")
class ReleaseManifests(unittest.TestCase):
    def test_extraction_documents_resolve_into_this_project(self):
        manifest = json.loads((LATEST / "preprocessed/extraction/extraction_manifest.json").read_text(encoding="utf-8"))
        for doc in manifest["documents"]:
            for key in ("raw_path", "rendered_path"):
                if doc.get(key):
                    path = pp.resolve(doc[key])
                    self.assertTrue(path.is_file(), f"{doc['document_id']} {key}: {path}")
                    self.assertTrue(path.is_relative_to(pp.ROOT), str(path))

    def test_release_components_resolve(self):
        release = json.loads((LATEST / "embedded/release_final/release_manifest.json").read_text(encoding="utf-8"))
        for item in release["components"]:
            for key in ("run", "candidate"):
                self.assertTrue(pp.resolve(item[key]).is_dir(), item[key])
                self.assertTrue(pp.resolve(item[key]).is_relative_to(pp.ROOT))


if __name__ == "__main__":
    unittest.main()


class SelfBuiltReleasePaths(unittest.TestCase):
    def test_builder_absolute_path_maps_to_this_clone(self):
        from rag.index.portable_paths import resolve, ROOT
        rel = 'data/versions/versions.json'
        other = r'D:\work\elsewhere\project_shared\data\versions\versions.json'
        self.assertEqual(resolve(other), ROOT / rel)
        missing = r'D:\work\elsewhere\project_shared\data\versions\no_such_file.json'
        self.assertEqual(str(resolve(missing)), missing)
        self.assertEqual(str(resolve(r'D:\x\project_shared\README.md')), r'D:\x\project_shared\README.md')
