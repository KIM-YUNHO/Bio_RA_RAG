"""Registering uploads: identical files share one snapshot but keep every registration path."""
from pathlib import Path
import tempfile
import unittest

import fitz

from preprocess.ingest import from_inputs, sha256


def native_pdf(path):
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=400)
        page.insert_text((35, 35), "Table 1. Study overview")
        pdf.save(path)


class IngestUploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "result"

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_uploads_share_snapshot_but_keep_registration_paths(self):
        a, b = self.root / "a.pdf", self.root / "b.pdf"
        native_pdf(a)
        b.write_bytes(a.read_bytes())
        docs = from_inputs([self.root], self.out)
        self.assertEqual(len(docs), 1)
        self.assertEqual(set(docs[0].source_paths), {str(a), str(b)})
        self.assertEqual(sha256(docs[0].raw_path), sha256(a))


if __name__ == "__main__":
    unittest.main()
