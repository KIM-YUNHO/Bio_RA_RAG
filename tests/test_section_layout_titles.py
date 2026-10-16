import unittest

from rag import document_structure as ds


def refs(n):
    return [{'block_id': f'u{i}', 'line_start': 0, 'line_end': 0} for i in range(n)]


class LayoutTitleRuleTests(unittest.TestCase):
    def test_top_level_number_needs_layout_title_evidence(self):
        title = '9. DATE OF FIRST AUTHORISATION/RENEWAL OF THE AUTHORISATION'
        self.assertEqual('9', ds.identify(title, version=ds.SECTIONS_9, layout_title=True)['code'])
        # Same shape in running text is a list item, e.g. an injection step.
        self.assertIsNone(ds.identify('9. Remove the cap', version=ds.SECTIONS_9))
        self.assertIsNone(ds.identify(title, version=ds.SECTIONS_9))

    def test_default_adds_study_boundaries_without_adopting_sections_9_layout_rules(self):
        self.assertEqual('sections-10', ds.VERSION)
        from rag.preprocessed_chunks import CONFIG
        self.assertEqual('sections-10', CONFIG['section_rules_version'])
        self.assertIsNone(ds.identify('9. Remove the cap',version=ds.VERSION,layout_title=True))

    def test_previous_rule_versions_are_unchanged(self):
        title = '7. MARKETING AUTHORISATION HOLDER'
        for version in (ds.VERSION, 'sections-7', ds.COMPAT_VERSION):
            self.assertIsNone(ds.identify(title, version=version, layout_title=True), version)
        self.assertEqual('6.6', ds.identify('6.6 Special precautions for disposal and other handling',
                                            version=ds.VERSION)['code'])

    def test_layout_title_never_makes_prose_or_toc_a_heading(self):
        self.assertIsNone(ds.identify('9. Other issues .... 31', version=ds.SECTIONS_9, layout_title=True))
        self.assertIsNone(ds.identify('9. remove the cap', version=ds.SECTIONS_9, layout_title=True))

    def test_tracker_nests_and_closes_top_level_sections(self):
        texts = ['6. PHARMACEUTICAL PARTICULARS', '6.6 Special precautions for disposal and other handling',
                 'Dilute before use with sodium chloride solution for infusion.',
                 '9. DATE OF FIRST AUTHORISATION/RENEWAL OF THE AUTHORISATION',
                 'Date of first authorisation: 19 April 2024']
        tracker = ds.Tracker('doc', 'v', None, version=ds.SECTIONS_9)
        _, _, paths, _ = tracker.scan(texts, refs(len(texts)), 17, 'preprocessed', layout_titles={0, 1, 3})
        self.assertEqual(['6. PHARMACEUTICAL PARTICULARS', '6.6 Special precautions for disposal and other handling'], paths[2])
        self.assertEqual(['9. DATE OF FIRST AUTHORISATION/RENEWAL OF THE AUTHORISATION'], paths[4])
        # Without layout evidence the previous behaviour holds: 9. stays inside 6.6.
        tracker = ds.Tracker('doc', 'v', None, version=ds.SECTIONS_9)
        _, _, paths, _ = tracker.scan(texts, refs(len(texts)), 17, 'preprocessed')
        self.assertEqual(['6.6 Special precautions for disposal and other handling'], paths[4])


if __name__ == '__main__':
    unittest.main()
