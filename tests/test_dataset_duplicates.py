"""A repeated page in a locked source must not enter development inference."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import fitz

from preprocess.dataset import prepare
from preprocess.dataset_duplicates import build, verify_plan
from preprocess.ingest import sha256


class DuplicatePlanTests(unittest.TestCase):
    def test_page_copy_is_quarantined_and_plan_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            common = 'Synthetic repeated source paragraph for testing page identity only. ' * 30
            docs = []
            for identifier, product in [('FDA:dev', 'dev'), ('FDA:locked', 'locked')]:
                path = root/(product+'.pdf')
                with fitz.open() as pdf:
                    first = pdf.new_page()
                    first.insert_textbox(fitz.Rect(40, 40, 540, 800), common, fontsize=9)
                    second = pdf.new_page()
                    second.insert_text((40, 50), 'Distinct page: '+product)
                    pdf.set_metadata({'title': product})
                    pdf.save(path)
                digest = sha256(path)
                docs.append({'document_id': identifier, 'version_id': digest, 'sha256': digest,
                    'kind': 'pdf', 'raw_path': path.name, 'product_id': product, 'source': 'FDA',
                    'document_type': 'report', 'page_count': 2})
            catalog = root/'catalog.jsonl'
            catalog.write_text(''.join(json.dumps(d)+'\n' for d in docs), encoding='utf-8')
            for name, value in [('baseline.json', {'documents': []}), ('exposure.json', {'documents': []}),
                ('split.json', {'cohorts': {}}), ('policy.json', {'dataset_version': 'synthetic',
                'development_product_ids': ['dev'], 'locked_product_cohorts': {'locked': 'test_locked'}})]:
                (root/name).write_text(json.dumps(value), encoding='utf-8')
            dataset, out = root/'dataset', root/'duplicates'
            prepare(root, catalog, root/'baseline.json', root/'exposure.json', root/'split.json', root/'policy.json', dataset)
            with contextlib.redirect_stdout(io.StringIO()):
                report = build(dataset, catalog, None, out)
            self.assertEqual(report['development_pages_quarantined'], 1)
            plan = verify_plan(dataset, out/'execution_plan.json')
            self.assertEqual(plan['pages_by_document'], {'FDA:dev': [2]})
            plan['pages_by_document']['FDA:dev'].append(1)
            (out/'execution_plan.json').write_text(json.dumps(plan), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'artifact changed'):
                verify_plan(dataset, out/'execution_plan.json')


if __name__ == '__main__':
    unittest.main()
