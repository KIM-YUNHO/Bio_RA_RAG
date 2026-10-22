"""Regression cases for source metadata, page furniture and title pagination."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from preprocess.ingest import Document, catalog_metadata, document_date, refresh_catalog_metadata
from preprocess.visual.visual_groups import build_visual_groups
from rag.index.preprocessed_chunks import CONFIG, furniture_units
from tests.preprocessing.test_visual_groups import asset


class MetadataTests(unittest.TestCase):
    def test_source_dates_and_final_url_survive_intake(self):
        cases = [({'application_doc': {'ApplicationDocsDate': '2025-02-05 00:00:00'}}, '2025-02-05'),
                 ({'last_updated_date': '2024-06-11T12:45:00Z'}, '2024-06-11'),
                 ({'date': '2021-02-15'}, '2021-02-15')]
        for source, expected in cases:
            with self.subTest(source=source):
                meta = catalog_metadata({'source_metadata': source, 'final_url': 'https://example.org/final.pdf'})
                self.assertEqual(meta['doc_date'], expected)
                self.assertEqual(meta['source_metadata'], source)
                self.assertEqual(meta['final_url'], 'https://example.org/final.pdf')
        for value in ('2024-02-30', 'unknown', None, 20240101):
            self.assertIsNone(document_date({'source_metadata': {'date': value}, 'retrieved_at': '2026-10-08'}))

    def test_refresh_matches_version_and_preserves_search_evaluation_policy(self):
        doc = Document('d', 'v', 'pdf', 'raw', 'raw', ['raw'],
            {'product_id': 'SB17', 'default_search': False, 'model_execution_allowed': False,
             'evaluation_cohort': 'held_out'}, 'v')
        row = {'document_id': 'd', 'version_id': 'v', 'sha256': 'v', 'product_id': 'SB17',
               'source_metadata': {'date': '2024-01-01'}, 'default_search': True,
               'model_execution_allowed': True, 'evaluation_cohort': 'development'}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'catalog.jsonl'
            path.write_text(json.dumps(row)+'\n', encoding='utf-8')
            refresh_catalog_metadata([doc], path)
            self.assertEqual(doc.metadata['doc_date'], '2024-01-01')
            self.assertFalse(doc.metadata['default_search'])
            self.assertFalse(doc.metadata['model_execution_allowed'])
            self.assertEqual(doc.metadata['evaluation_cohort'], 'held_out')
            for field, value in [('version_id', 'other'), ('product_id', 'OTHER'), ('sha256', 'other')]:
                changed = {**row, field: value}
                path.write_text(json.dumps(changed)+'\n', encoding='utf-8')
                with self.assertRaises(ValueError): refresh_catalog_metadata([deepcopy(doc)], path)


class FurnitureTests(unittest.TestCase):
    def test_only_recurrent_marginal_furniture_and_page_numbers_are_excluded(self):
        units, assets, pages = [], {}, {}
        for page in range(1, 7):
            pages[('d', 'v', page)] = {'status': 'parsed'}
            for prefix, label, box, text, structure in [
                ('footer', 'footer', [10, 950, 800, 980], f'Reference ID: {page}', {}),
                ('number', 'number', [20, 900, 50, 920], str(page), {}),
                ('body', 'footer', [10, 400, 800, 430], 'An actual body paragraph.', {}),
                ('caption', 'header', [10, 20, 800, 45], 'Table 1. Results', {'logical_group_id': 'g'}),
                ('unique', 'header', [10, 20, 800, 45], 'Unique title '+chr(64+page), {})]:
                uid = f'{prefix}{page}'
                units.append({'unit_id': uid, 'document_id': 'd', 'version_id': 'v',
                              'kind': 'text', 'text': text, 'structure': structure})
                assets[uid] = {'block_label': label, 'pdf_page': page, 'bbox_input_px': box,
                               'input_image_size': [1000, 1000]}
        excluded = furniture_units(units, assets, pages, CONFIG)
        self.assertEqual(excluded, {f'{prefix}{page}' for prefix in ('footer', 'number') for page in range(1, 7)})


class PaginatedCaptionTests(unittest.TestCase):
    def rows(self):
        return [asset('cap', 'table_title', [100, 690, 800, 730], 'Table 7. Immunogenicity'),
            asset('footer', 'footer', [100, 950, 800, 975], 'Reference ID: 123'),
            asset('head', 'header', [100, 30, 800, 50], 'Review report', page=2),
            asset('body', 'table', [100, 130, 800, 600], '', page=2,
                  html='<table><tr><th>Group</th><th>N</th></tr><tr><td>A</td><td>10</td></tr></table>')]

    def test_caption_above_bottom_margin_links_to_clear_next_page_table(self):
        groups, _ = build_visual_groups(self.rows())
        group = next(g for g in groups if g['label'] == 'Table 7')
        self.assertEqual(group['member_asset_ids'], ['body'])
        self.assertEqual(group['continuation_status'], 'caption_on_previous_page')
        self.assertEqual(group['pdf_pages'], [1, 2])

    def test_intervening_prose_new_caption_wrong_column_or_other_version_prevents_link(self):
        for condition in ('after', 'before', 'caption', 'column', 'version'):
            rows = self.rows()
            if condition == 'after': rows.append(asset('prose', 'text', [100, 760, 800, 810], 'Unrelated prose'))
            if condition == 'before': rows.append(asset('prose', 'text', [100, 70, 800, 110], 'Unrelated prose', page=2))
            if condition == 'caption': rows.append(asset('cap2', 'table_title', [100, 75, 800, 100], 'Table 8. Other table', page=2))
            if condition == 'column': rows[3]['bbox_input_px'] = [850, 130, 990, 600]
            if condition == 'version': rows[3]['version_id'] = 'other'
            with self.subTest(condition=condition):
                groups, _ = build_visual_groups(rows)
                self.assertFalse(any(g['label'] == 'Table 7' for g in groups))


if __name__ == '__main__': unittest.main()
