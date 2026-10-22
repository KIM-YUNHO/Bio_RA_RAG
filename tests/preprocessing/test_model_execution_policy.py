"""A document locked against model use must fail before any backend or output side effect."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from preprocess.ingest import Document, assert_model_execution_allowed
from preprocess.extraction.hybrid import run_hybrid
from preprocess.hybrid_config import HybridConfig


class ModelExecutionPolicyTests(unittest.TestCase):
    def test_locked_model_request_fails_before_backend_or_output_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = Document('locked', 'sha', 'pdf', 'unused', 'unused', [], {'model_execution_allowed': False})
            backend = Mock()
            with self.assertRaisesRegex(ValueError, 'execution denied'):
                run_hybrid([doc], root/'model_run', HybridConfig(), paddle=backend)
            backend.fingerprint.assert_not_called()
            self.assertFalse((root/'model_run').exists())
            doc.metadata['model_execution_allowed'] = 'false'
            with self.assertRaisesRegex(ValueError, 'Invalid model execution policy'):
                assert_model_execution_allowed([doc])

if __name__ == '__main__':
    unittest.main()
