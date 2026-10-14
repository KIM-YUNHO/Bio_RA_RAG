import unittest

from rag.audit_document_transfer import assemble, source_mentions


def document(identifier="doc-A", version="version-A", product="product-A"):
    return {"document_id": identifier, "version_id": version, "product_id": product,
            "ingredient": "ingredient", "source": "agency", "family": "label"}


class DocumentTransferAuditTests(unittest.TestCase):
    def test_allowlist_ids_are_not_source_exposure(self):
        payload = {"approved": [{"document_id": "doc-A", "version_id": "version-A"}],
                   "runtime_sha256": {"doc-A": "version-A"}}
        self.assertEqual(source_mentions(payload, {"doc-A": {"doc-A"}},
                                         {"version-A": {"doc-A"}}), [])

    def test_prior_revision_is_exposure_without_assuming_latest_text(self):
        payload = {"results": [{"evidence": {"E1": {
            "document_id": "doc-A", "version_id": "prior-version", "text": "Printed evidence", "pdf_page": 3}}}]}
        mentions = source_mentions(payload, {"doc-A": {"doc-A"}}, {"version-A": {"doc-A"}})
        row = assemble([document()], mentions, {"protected"})[0]
        self.assertTrue(row["known_development_source_exposure"])
        self.assertEqual(row["prior_version_records"], 1)
        self.assertEqual(row["source_version_unresolved_records"], 0)
        self.assertFalse(row["certified_unseen_document"])

    def test_sibling_products_share_conservative_group_and_unknown_is_not_unseen(self):
        documents = [document(), document("doc-B", "version-B", "product-B"),
                     {**document("doc-C", "version-C"), "source": "other-agency"}]
        mentions = source_mentions({"references": [{"document_id": "doc-A", "text": "Source"}]},
                                   {d["document_id"]: {d["document_id"]} for d in documents}, {})
        rows = {r["document_id"]: r for r in assemble(documents, mentions, {"protected"})}
        self.assertEqual(rows["doc-A"]["source_version_unresolved_records"], 1)
        self.assertTrue(rows["doc-B"]["format_group_has_known_exposure"])
        self.assertFalse(rows["doc-C"]["format_group_has_known_exposure"])
        for name in ("doc-B", "doc-C"):
            self.assertTrue(rows[name]["requires_manual_exposure_review"])
            self.assertFalse(rows[name]["unseen_document_eval_eligible"])

    def test_protected_version_cannot_be_declared_approved(self):
        with self.assertRaisesRegex(ValueError, "protected document"):
            assemble([document()], [], {"version-A"})


if __name__ == "__main__":
    unittest.main()
