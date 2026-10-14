"""Leakage prevention and annotation truth-state checks, without model services."""
import copy
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
import fitz

from preprocess.ingest import sha256
from preprocess.visual_evaluation import (Families, duplicate_pairs, family_key, select_sample,
    validate_annotation, validate_annotations, verify_inputs, verify_pack, write, write_rows, audit, freeze)


def doc(uid, family, clean=False, dev=False, pages=50):
    return {'document_id': uid, 'version_id': (uid.encode().hex()+'0'*64)[:64],
            'metadata': {'source': 'source-'+uid, 'product_id': 'same_product', 'document_type': 'review'},
            'family_id': family, 'pages': pages, 'record_clean': clean, 'confirmed_development': dev}


def page(uid, number):
    return {'document_id': uid, 'pdf_page': number, 'bounds': [0, 0, 500, 700], 'rotation': 0}


def object_(uid, kind='figure', parents=None):
    return {'object_id': uid, 'kind': kind, 'bbox_pdf': [10, 10, 100, 100],
            'parent_ids': parents or [], 'certainty': 'certain', 'legibility': 'not_applicable'}


def annotation(objects=None, uid='A', number=1):
    return {'document_id': uid, 'version_id': '0'*64, 'bounds': [0, 0, 500, 700], 'pdf_page': number,
            'schema_version': 'visual-evaluation-1', 'family_id': 'family', 'rotation': 0, 'clinical_semantics_reviewed': False,
            'annotation_status': 'agent_draft', 'reviewer': 'agent', 'reviewer_type': 'agent',
            'reviewed_at': '2026-10-08T00:00:00Z', 'objects': objects or [], 'negative_page': not objects}


class VisualEvaluationTests(unittest.TestCase):
    def test_related_versions_and_protocol_sap_are_not_separate_families(self):
        a = {'document_id': 'CTG:NCT12345678:Prot.pdf', 'metadata': {'source': 'ClinicalTrials.gov', 'product_id': 'P', 'document_type': 'Protocol'}}
        b = {'document_id': 'CTG:NCT12345678:SAP.pdf', 'metadata': {'source': 'ClinicalTrials.gov', 'product_id': 'P', 'document_type': 'SAP'}}
        self.assertEqual(family_key(a), family_key(b))
        a['metadata'].update(source='EMA', document_type='rmp')
        b['metadata'].update(source='EMA', document_type='rmp-summary')
        self.assertEqual(family_key(a), family_key(b))
        b['metadata']['product_id'] = 'Q'
        self.assertNotEqual(family_key(a), family_key(b))

    def test_exact_alias_and_metadata_family_links_are_transitive(self):
        f = Families(['A', 'B', 'C'])
        f.join('A', 'B')
        f.join('B', 'C')
        self.assertEqual(f.find('A'), f.find('C'))

    def test_development_sibling_excluded_and_history_cohorts_never_mixed(self):
        docs = [doc('A', 'dev', dev=True), doc('B', 'dev', clean=True),
                doc('C', 'old'), doc('D', 'reserve', clean=True)]
        pages = [page(d['document_id'], n) for d in docs for n in range(1, 51)]
        split = select_sample(docs, pages, [])
        self.assertEqual(split['development_document_ids'], ['A', 'B'])
        self.assertEqual(split['cohorts']['record_clean_reserve']['document_ids'], ['D'])
        self.assertEqual(split['cohorts']['retrospective_locked']['document_ids'], ['C'])

    def test_duplicate_chain_is_excluded_before_random_sampling(self):
        docs = [doc('A', 'dev', dev=True), doc('B', 'old'), doc('C', 'other')]
        pages = [page(d['document_id'], n) for d in docs for n in range(1, 51)]
        pairs = [{'a': ['A', 1], 'b': ['B', 2]}, {'a': ['B', 2], 'b': ['C', 3]}]
        split = select_sample(docs, pages, pairs)
        selected = {(p['document_id'], p['pdf_page']) for c in split['cohorts'].values() for p in c['pages']}
        self.assertNotIn(('B', 2), selected)
        self.assertNotIn(('C', 3), selected)
        self.assertEqual(split['excluded_duplicate_page_keys'], [['B', 2], ['C', 3]])

    def test_random_sampling_reproducible_and_probability_has_correct_frame(self):
        docs = [doc('C', 'old')]
        pages = [page('C', n) for n in range(1, 51)]
        a = select_sample(docs, pages, [], seed=42)
        b = select_sample(list(reversed(docs)), list(reversed(pages)), [], seed=42)
        self.assertEqual(a, b)
        jobs = a['cohorts']['retrospective_locked']['pages']
        anchors = [p for p in jobs if 'random_anchor' in p['memberships']]
        self.assertEqual(len(anchors), 20)
        for p in anchors:
            detail = next(d for d in p['selection_details'] if d['membership'] == 'random_anchor')
            self.assertEqual(detail['inclusion_probability'], .4)
            self.assertIsNone(p['negative_page'])
            self.assertIsNone(p['objects'])
            validate_annotation(p)

    def test_changed_or_missing_source_invalidates_freeze(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'source.pdf'
            path.write_bytes(b'original')
            inputs = {'source.pdf': sha256(path)}
            verify_inputs(root, inputs)
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                verify_inputs(root, inputs)

    def test_repeated_header_image_does_not_create_duplicate_chart_link(self):
        pages = []
        for uid in ('A', 'B'):
            for number in range(1, 4):
                pages.append({'document_id': uid, 'version_id': uid, 'pdf_page': number, 'native_sha256': None,
                              'native_chars': 0, 'text_sketch': [], 'raster': [{'digest': 'logo'}]})
        self.assertEqual(duplicate_pairs(pages), [])

    def test_blank_job_cannot_be_reported_as_negative(self):
        job = annotation()
        job.update(annotation_status='not_annotated', objects=None, negative_page=False)
        with self.assertRaises(ValueError):
            validate_annotation(job)

    def test_bbox_redaction_and_dangling_owner_rejected(self):
        fig = object_('f')
        panel = object_('p', 'panel', ['missing'])
        with self.assertRaises(ValueError):
            validate_annotation(annotation([fig, panel]))
        panel['parent_ids'] = ['f']
        panel['bbox_pdf'] = [10, 10, 900, 100]
        with self.assertRaises(ValueError):
            validate_annotation(annotation([fig, panel]))
        label = object_('l', 'printed_label', ['f'])
        label.update(legibility='redacted', literal_text='12.5')
        with self.assertRaises(ValueError):
            validate_annotation(annotation([fig, label]))

    def test_inset_requires_panel_and_parent_graph_must_be_acyclic(self):
        fig = object_('f')
        inset = object_('i', 'inset', ['f'])
        with self.assertRaises(ValueError):
            validate_annotation(annotation([fig, inset]))
        panel = object_('p', 'panel', ['f'])
        inset['parent_ids'] = ['p']
        validate_annotation(annotation([fig, panel, inset]))
        fig['parent_ids'] = ['i']
        with self.assertRaises(ValueError):
            validate_annotation(annotation([fig, panel, inset]))

    def test_previous_page_caption_can_link_but_another_document_cannot(self):
        fig = object_('f')
        caption = object_('c', 'caption', ['f'])
        caption['literal_text'] = 'Figure 1'
        first, second = annotation([caption]), annotation([fig], number=2)
        validate_annotations([first, second])
        other = copy.deepcopy(second)
        other['document_id'] = 'B'
        with self.assertRaises(ValueError):
            validate_annotations([first, other])

    def test_agent_review_cannot_be_promoted_to_human_gold(self):
        job = annotation()
        job['annotation_status'] = 'human_reviewed'
        with self.assertRaises(ValueError):
            validate_annotation(job)

    def test_reserve_duplicate_to_processed_document_is_excluded(self):
        docs = [doc('old', 'old'), doc('new', 'new', clean=True)]
        pages = [page(d['document_id'], n) for d in docs for n in range(1, 51)]
        split = select_sample(docs, pages, [{'a': ['old', 1], 'b': ['new', 2]}])
        self.assertEqual(split['excluded_reserve_duplicate_page_keys'], [['new', 2]])
        self.assertNotIn(2, [p['pdf_page'] for p in split['cohorts']['record_clean_reserve']['pages']])

    def make_pack(self, root):
        out = root/'evaluation'
        job = annotation()
        job.update(annotation_status='not_annotated', objects=None, negative_page=None,
                   reviewer=None, reviewer_type=None, reviewed_at=None, memberships=['random_anchor'], selection_details=[])
        write_rows(out/'cohort/annotation_jobs.jsonl', [job])
        write_rows(out/'cohort/annotations.jsonl', [job])
        write(out/'exposure.json', {'inputs': {}, 'artifact_scan_paths': [],
                'documents': [{'document_id': 'A', 'version_id': '0'*64, 'family_id': 'family'}]})
        write(out/'split.json', {'cohorts': {'cohort': {'family_ids': ['family']}}})
        write(out/'seal.json', {'artifacts': {str(p.relative_to(out)): sha256(p) for p in out.rglob('*')
            if p.is_file() and p.name != 'annotations.jsonl'}, 'configs': {},
            'mutable_annotations': ['cohort/annotations.jsonl']})
        return out, job

    def test_sealed_selection_rejects_label_file_coordinate_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, job = self.make_pack(root)
            self.assertEqual(verify_pack(root, out)['annotated_pages'], 0)
            job['bounds'] = [0, 0, 600, 700]
            write_rows(out/'cohort/annotations.jsonl', [job])
            with self.assertRaises(ValueError):
                verify_pack(root, out)

    def test_sealed_page_list_cannot_be_changed_without_new_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, job = self.make_pack(root)
            job['pdf_page'] = 2
            write_rows(out/'cohort/annotation_jobs.jsonl', [job])
            with self.assertRaises(ValueError):
                verify_pack(root, out)

    def test_new_model_artifact_for_reserved_family_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, job = self.make_pack(root)
            write_rows(root/'data/preprocessed/new_run/extraction_pages.jsonl',
                       [{'document_id': 'A', 'version_id': '0'*64, 'pdf_page': 1}])
            with self.assertRaises(ValueError):
                verify_pack(root, out)

    def test_independent_labels_can_fill_jobs_without_rewriting_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, job = self.make_pack(root)
            job.update(annotation_status='human_reviewed', reviewer='reviewer', reviewer_type='human',
                       reviewed_at='2026-10-08T00:00:00Z', objects=[], negative_page=True)
            write_rows(out/'cohort/annotations.jsonl', [job])
            result = verify_pack(root, out)
            self.assertEqual(result['annotated_pages'], 1)
            self.assertFalse(result['gold_release_ready'])

    def test_offline_audit_freeze_verify_end_to_end_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_root = Path(__file__).resolve().parents[1]
            for relative in ('preprocess/visual_evaluation.py', 'configs/preprocess.visual.annotation.schema.json',
                             'configs/preprocess.visual.evaluation.protocol.json'):
                destination = root/relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((source_root/relative).read_bytes())
            catalog, inv_docs, inv_pages = [], [], []
            for index in range(2):
                path = root/f'data/raw/test{index}.pdf'
                path.parent.mkdir(parents=True, exist_ok=True)
                with fitz.open() as pdf:
                    page_ = pdf.new_page()
                    page_.insert_text((30, 30), f'Synthetic document {index}; no clinical source')
                    pdf.save(path)
                digest = sha256(path)
                uid = f'test:{index}'
                catalog.append({'document_id': uid, 'version_id': digest, 'kind': 'pdf',
                    'raw_path': str(path.relative_to(root)), 'source': f'source{index}', 'product_id': f'product{index}',
                    'document_type': 'test'})
                inv_docs.append({'document_id': uid, 'version_id': digest, 'candidate_count': 0})
                inv_pages.append({'document_id': uid, 'version_id': digest, 'pdf_page': 1, 'candidate_count': 0,
                                  'signals': {}, 'needs_ocr': False})
            write_rows(root/'data/catalog/documents.jsonl', catalog)
            inv = root/'data/preprocessed/inventory_2026-10-07'
            write_rows(inv/'documents.jsonl', inv_docs)
            write_rows(inv/'pages.jsonl', inv_pages)
            write(inv/'inventory_summary.json', {})
            write(inv/'manifest.json', {})
            with contextlib.redirect_stdout(io.StringIO()):
                data = audit(root)
            audit_path = root/'audit.json'
            write(audit_path, data)
            out = root/'data/evaluation/visual_generalization_v1'
            split = freeze(audit_path, root, out)
            self.assertFalse(split['strict_holdout_ready'])
            result = verify_pack(root, out)
            self.assertEqual(result['pages'], 2)
            self.assertEqual(result['annotated_pages'], 0)
            self.assertEqual(result['model_calls'], {'ocr': 0, 'vlm': 0, 'layout': 0})
            with self.assertRaises(ValueError):
                freeze(audit_path, root, out)


if __name__ == '__main__':
    unittest.main()
