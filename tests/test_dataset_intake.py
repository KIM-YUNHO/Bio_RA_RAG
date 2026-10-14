"""Incremental intake must preserve locked families, versions and source identity."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from preprocess.dataset import prepare, rows, verify
from preprocess.ingest import Document, assert_model_execution_allowed, from_catalog, sha256
from preprocess.hybrid import run_hybrid
from preprocess.hybrid_config import HybridConfig


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root/'dataset'
        self.catalog = self.root/'catalog.jsonl'
        self.baseline = self.root/'baseline.json'
        self.exposure = self.root/'exposure.json'
        self.split = self.root/'split.json'
        self.policy = self.root/'policy.json'
        self.write(self.policy, {'dataset_version': 'synthetic-v2', 'development_product_ids': ['old', 'dev'],
            'locked_product_cohorts': {'new': 'product_test_locked'}})
        self.write(self.exposure, {'documents': []})
        self.write(self.split, {'cohorts': {}})
        self.write(self.baseline, {'documents': []})

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def doc(self, identifier, product='dev', content=None, kind='report'):
        raw = self.root/(identifier.replace(':', '_') + '.pdf')
        raw.write_bytes(content if content is not None else identifier.encode())
        digest = sha256(raw)
        return {'document_id': identifier, 'version_id': digest, 'sha256': digest,
            'product_id': product, 'source': 'FDA', 'kind': 'pdf', 'document_type': kind,
            'raw_path': raw.name, 'page_count': 1, 'default_search': False,
            'ingredient': 'synthetic', 'product_kind': 'biosimilar', 'tier': 'record'}

    def prepare(self, documents):
        self.catalog.write_text(''.join(json.dumps(d)+'\n' for d in documents), encoding='utf-8')
        return prepare(self.root, self.catalog, self.baseline, self.exposure, self.split, self.policy, self.out)

    def test_new_fda_letter_inherits_old_locked_dossier(self):
        old, new, dev = self.doc('FDA:old', 'old'), self.doc('FDA:supplement', 'old', kind='letter'), self.doc('FDA:dev')
        self.write(self.baseline, {'documents': [{'document_id': old['document_id'], 'version_id': old['version_id']}]})
        self.write(self.exposure, {'documents': [{'document_id': old['document_id'], 'family_id': 'protected'}]})
        self.write(self.split, {'cohorts': {'reserve': {'family_ids': ['protected']}}})
        summary = self.prepare([old, new, dev])
        self.assertEqual(summary['delta_documents'], 2)
        self.assertEqual([d['document_id'] for d in rows(self.out/'development_delta_catalog.jsonl')], ['FDA:dev'])
        self.assertEqual(next(d for d in rows(self.out/'delta_catalog.jsonl') if d['document_id'] == new['document_id'])['evaluation_cohort'], 'legacy_v1_locked')
        self.assertEqual(verify(self.out)['status'], 'passed')

    def test_same_file_alias_cannot_cross_locked_and_development_products(self):
        dev, locked = self.doc('FDA:dev', content=b'same bytes'), self.doc('FDA:new', 'new', b'same bytes')
        summary = self.prepare([dev, locked])
        self.assertEqual(summary['development_model_documents'], 0)
        self.assertFalse(next(d for d in rows(self.out/'catalog.jsonl') if d['product_id'] == 'dev')['model_execution_allowed'])

    def test_changed_version_is_delta_and_metadata_survives_catalog_adapter(self):
        doc = self.doc('FDA:dev')
        self.write(self.baseline, {'documents': [{'document_id': doc['document_id'], 'version_id': 'old-version'}]})
        self.prepare([doc])
        normalized = from_catalog(self.out/'catalog.jsonl', self.root)[0]
        self.assertEqual(normalized.metadata['baseline_status'], 'baseline_new_version')
        self.assertIs(normalized.metadata['default_search'], False)
        self.assertEqual(normalized.metadata['ingredient'], 'synthetic')
        self.assertEqual(normalized.metadata['evaluation_cohort'], 'development')

    def test_bad_raw_hash_leaves_no_dataset_output(self):
        doc = self.doc('FDA:dev')
        (self.root/doc['raw_path']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.prepare([doc])
        self.assertFalse(self.out.exists())

    def test_sealed_queue_cannot_be_reassigned_and_catalog_changes_are_detected(self):
        self.prepare([self.doc('FDA:dev')])
        self.catalog.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            verify(self.out)
        (self.out/'development_delta_catalog.jsonl').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            verify(self.out)

    def test_locked_model_request_fails_before_backend_or_output_side_effects(self):
        doc = Document('locked', 'sha', 'pdf', 'unused', 'unused', [], {'model_execution_allowed': False})
        backend = Mock()
        with self.assertRaisesRegex(ValueError, 'execution denied'):
            run_hybrid([doc], self.root/'model_run', HybridConfig(), paddle=backend)
        backend.fingerprint.assert_not_called()
        self.assertFalse((self.root/'model_run').exists())
        doc.metadata['model_execution_allowed'] = 'false'
        with self.assertRaisesRegex(ValueError, 'Invalid model execution policy'):
            assert_model_execution_allowed([doc])

    def test_duplicate_catalog_id_does_not_guess_which_version_is_current(self):
        doc = self.doc('FDA:dev')
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.prepare([doc, dict(doc, version_id='other')])

    def test_qa_exposure_is_bound_to_source_sha_without_emitting_answers(self):
        from preprocess.dataset import history
        doc = self.doc('FDA:new', 'new')
        audit = self.root/'qa_audit.json'
        self.write(audit, {'sources': {'Synthetic': {'expected_sha256': doc['sha256'], 'annotated_pages': [1]}},
                          'gold_answer': 'must not appear in metadata output'})
        result = history(self.root, [doc], audit)
        self.assertEqual(result['events'][0]['document_id'], doc['document_id'])
        self.assertEqual(result['events'][0]['kind'], 'prior_qa_source_derivation')
        self.assertNotIn('must not appear', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
