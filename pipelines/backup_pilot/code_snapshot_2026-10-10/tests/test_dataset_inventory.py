"""Unchanged inventory reuse must retain current metadata and original bounds."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import fitz

from preprocess.dataset import prepare
from preprocess.dataset_inventory import compose
from preprocess.ingest import from_catalog, sha256
from preprocess.inventory import run_inventory


class ComposedInventoryTests(unittest.TestCase):
    def test_reuse_adds_dataset_metadata_without_losing_page_source_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            docs = []
            for identifier in ('old', 'new'):
                path = root/(identifier+'.pdf')
                with fitz.open() as pdf:
                    page = pdf.new_page()
                    page.insert_text((40, 50), identifier+' synthetic study text')
                    pdf.save(path)
                digest = sha256(path)
                docs.append({'document_id': identifier, 'version_id': digest, 'sha256': digest,
                    'kind': 'pdf', 'raw_path': path.name, 'product_id': 'dev', 'source': 'FDA',
                    'document_type': 'report', 'page_count': 1, 'default_search': False})
            catalog, old_catalog = root/'catalog.jsonl', root/'old_catalog.jsonl'
            catalog.write_text(''.join(json.dumps(d)+'\n' for d in docs))
            old_catalog.write_text(json.dumps(docs[0])+'\n')
            old_run, delta_run = root/'old_inventory', root/'delta_inventory'
            with contextlib.redirect_stdout(io.StringIO()):
                run_inventory(from_catalog(old_catalog, root), old_run)
            for name, value in [('exposure.json', {'documents': []}), ('split.json', {'cohorts': {}}),
                ('policy.json', {'dataset_version': 'synthetic-v2', 'development_product_ids': ['dev'], 'locked_product_cohorts': {}})]:
                (root/name).write_text(json.dumps(value))
            dataset, out = root/'dataset', root/'composed'
            prepare(root, catalog, old_run/'manifest.json', root/'exposure.json', root/'split.json', root/'policy.json', dataset)
            with contextlib.redirect_stdout(io.StringIO()):
                run_inventory(from_catalog(dataset/'delta_catalog.jsonl', root), delta_run)
            summary = compose(dataset, old_run, delta_run, out)
            self.assertEqual(summary['unchanged_documents_reused'], 1)
            self.assertEqual(summary['pdf_pages'], 2)
            self.assertEqual(summary['validation']['status'], 'passed')
            old_page = json.loads((old_run/'pages.jsonl').read_text().splitlines()[0])
            composed_page = json.loads((out/'pages.jsonl').read_text().splitlines()[0])
            self.assertEqual(old_page, composed_page)
            record = json.loads((out/'documents.jsonl').read_text().splitlines()[0])
            self.assertIs(record['metadata']['default_search'], False)
            self.assertEqual(record['metadata']['dataset_version'], 'synthetic-v2')
            self.assertEqual(summary['model_calls'] if 'model_calls' in summary else summary['vlm_calls'], 0)


if __name__ == '__main__':
    unittest.main()
