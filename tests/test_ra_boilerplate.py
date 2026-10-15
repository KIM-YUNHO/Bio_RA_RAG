import unittest
from types import SimpleNamespace

from rag import ra_retrieval


def chunk(doc, page, text, tokens=None):
    return {'document_id': doc, 'pdf_page': page, 'text': text, 'n_tokens': tokens or len(text.split())}


class BoilerplateTests(unittest.TestCase):
    def test_running_footers_and_content_free_chunks_are_boilerplate(self):
        chunks = [chunk('EPAR', p, f'Assessment report EMA/228742/2024\nPage {p}/20') for p in range(1, 21)]
        chunks += [chunk('EPAR', 3, 'The applicant submitted on 20 April 2023 an application for marketing authorisation.'),
                   chunk('PI', 1, '85'), chunk('PI', 2, '1/1'), chunk('PI', 3, '(b) (4)')]
        found = ra_retrieval.boilerplate_ids(SimpleNamespace(chunks=chunks))
        self.assertEqual(set(range(20)) | {21, 22, 23}, found)

    def test_a_short_fact_printed_once_is_not_boilerplate(self):
        chunks = [chunk('SBD', 3, 'Date Filed: 2022-11-15'), chunk('SBD', 3, 'Authorization Date: 2023-12-27')]
        self.assertFalse(ra_retrieval.boilerplate_ids(SimpleNamespace(chunks=chunks)))

    def test_a_fact_repeated_on_a_few_pages_is_not_boilerplate(self):
        # SmPC section 9 is printed once per presentation (3 of 140 pages).
        chunks = [chunk('PI', p, 'Date of first authorisation: 19 April 2024') for p in (17, 43, 64)]
        chunks += [chunk('PI', p, f'Body text of page {p} about dosing and administration.') for p in range(1, 141)]
        found = ra_retrieval.boilerplate_ids(SimpleNamespace(chunks=chunks))
        self.assertFalse(found & {0, 1, 2})

    def test_retrieval_options_default_to_previous_behaviour(self):
        self.assertFalse(ra_retrieval.DEFAULT_PROFILE['suppress_boilerplate'])
        self.assertEqual(('correspondence',), ra_retrieval.DEFAULT_PROFILE['extra_default_groups'])  # since 2026-10-16
        self.assertFalse(ra_retrieval.DEFAULT_PROFILE['collapse_duplicates'])
        self.assertTrue(ra_retrieval.DEFAULT_PROFILE['role_boost'])


if __name__ == '__main__':
    unittest.main()
