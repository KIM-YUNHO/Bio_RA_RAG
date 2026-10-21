import unittest
from rag.index.incremental_embeddings import reuse_plan

class IncrementalInputTests(unittest.TestCase):
    def test_reordered_metadata_reuses_but_title_body_changes_do_not(self):
        old=[{'header':'A','text':'Body','chunk_id':'old1'},{'header':'B','text':'17','chunk_id':'old2'}]
        new=[{'header':'B','text':'17','chunk_id':'new2','new_source_contract':True},
            {'header':'A','text':'Body','chunk_id':'new1'}, {'header':'Changed title','text':'Body'},
            {'header':'B','text':'170'}]
        pairs,changed=reuse_plan(old,new)
        self.assertEqual(pairs,[(0,1),(1,0)]);self.assertEqual(changed,[2,3])
    def test_header_newline_body_boundary_is_an_exact_encoder_input(self):
        # Different fields may produce the same actual encoder string. Conversely
        # stripping whitespace would incorrectly reuse a different encoder input.
        old=[{'header':'A','text':'B\nC'}]
        new=[{'header':'A\nB','text':'C'},{'header':'A','text':'B C'}]
        self.assertEqual(reuse_plan(old,new),([(0,0)],[1]))
