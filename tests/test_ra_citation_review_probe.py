import unittest

from eval.ra_citation_review_probe import validate


class CitationProofTests(unittest.TestCase):
    def check(self, fragment='90mg을 투여하였다.', proof='The 90% confidence interval', index=0):
        claim = {'id': 'C1', 'text': '90mg을 투여하였다.', 'sources': [
            {'quote': 'The 90% confidence interval was calculated.'},
            {'quote': 'Subjects received 90 mg as a single SC injection.'}]}
        raw = {'claims': [{'id': 'C1', 'clauses': [{'claim_fragment': fragment,
            'verdict': 'supported', 'proofs': [{'source_index': index, 'quote': proof}], 'reason': 'test'}]}]}
        return validate(raw, [claim])[0]

    def test_percent_cannot_prove_mg_even_when_llm_approves(self):
        row = self.check()
        self.assertTrue(row['model_all_clauses_supported'])
        self.assertFalse(row['supported_after_proof_binding'])
        self.assertIn('clause_0:quantity_value_or_unit_not_in_proof', row['binding_errors'])

    def test_literal_dose_proof_with_space_variant_is_retained(self):
        self.assertTrue(self.check(proof='Subjects received 90 mg', index=1)['supported_after_proof_binding'])

    def test_fabricated_proof_is_rejected(self):
        row = self.check(proof='Subjects received 90 mg', index=0)
        self.assertIn('clause_0:proof_not_literal_selected_quote', row['binding_errors'])
        self.assertFalse(row['supported_after_proof_binding'])

    def test_proof_cannot_point_outside_selected_quotations(self):
        row = self.check(proof='Subjects received 90 mg', index=2)
        self.assertIn('clause_0:unknown_selected_quote', row['binding_errors'])

    def test_verifier_cannot_rewrite_the_original_claim_fragment(self):
        row = self.check(fragment='90mg을 정맥 투여하였다.', proof='Subjects received 90 mg', index=1)
        self.assertIn('clause_0:fragment_not_in_claim', row['binding_errors'])


if __name__ == '__main__':
    unittest.main()
