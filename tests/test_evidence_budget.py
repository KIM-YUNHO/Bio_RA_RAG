import unittest
from rag.evidence.evidence_budget import select


class Budget(unittest.TestCase):
    def chunk(self, key, page, size):
        return {'chunk_id':key,'document_id':'d','version_id':'v','pdf_page':page,'text':'x'*size}

    def test_each_hit_precedes_extra_and_linked_visual(self):
        rows = [(self.chunk('a',1,3),'retrieved'),(self.chunk('v',9,6),'linked_visual_candidate'),
                (self.chunk('b',2,3),'retrieved'),(self.chunk('c',3,3),'retrieved'),
                (self.chunk('a2',1,3),'retrieved')]
        got, omitted = select(rows,lambda proposed:sum(len(c['text']) for c,_ in proposed)<=9)
        self.assertEqual([c['chunk_id'] for c,_ in got],['a','b','c'])
        self.assertEqual({c['chunk_id'] for c in omitted},{'v','a2'})

    def test_oversized_chunk_is_recorded_without_hiding_later_page(self):
        rows = [(self.chunk('big',1,100),'retrieved'),(self.chunk('small',2,3),'retrieved')]
        got, omitted = select(rows,lambda proposed:sum(len(c['text']) for c,_ in proposed)<=9)
        self.assertEqual(got[0][0]['text'],'xxx')
        self.assertEqual(omitted[0]['chunk_id'],'big')


if __name__ == '__main__': unittest.main()
