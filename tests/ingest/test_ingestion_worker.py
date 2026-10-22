"""Approval-time check table: rebuilt from this folder's staging, never from another project that shares the database."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ingestion import worker


class CheckHereTests(unittest.TestCase):
    def check(self, done):
        with tempfile.TemporaryDirectory() as tmp, patch.object(worker, 'RUNS', Path(tmp)), patch.object(worker, 'run', return_value=done) as run:
            digest = worker.check_here('SB17', 'request-1')
            self.assertTrue((Path(tmp) / 'request-1').is_dir())
        run.assert_called_once_with(['product_search.promote', 'SB17'], 'promote_check_SB17_at_approval.txt', Path(tmp) / 'request-1')
        return digest

    def test_missing_staging_gives_none(self):
        self.assertIsNone(self.check(SimpleNamespace(returncode=1, stdout='', stderr='SB17 is not in product_search/data')))

    def test_digest_matches_the_worker_report(self):
        self.assertEqual(self.check(SimpleNamespace(returncode=0, stdout='check table', stderr='')), hashlib.sha256(b'check table').hexdigest())


if __name__ == '__main__':
    unittest.main()
