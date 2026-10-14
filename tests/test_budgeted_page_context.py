import unittest
from types import SimpleNamespace
from unittest.mock import patch
from rag.budgeted_evidence import BudgetedEvidence


class PageContext(unittest.TestCase):
    def chunk(self, identity, text, page=1):
        return {'chunk_id':identity,'document_id':'doc','version_id':'v','pdf_page':page,
            'text':text,'structure':{'kind':'table_candidate','caption':'','section_path':[]}}

    def test_answer_table_outside_dense_shortlist_precedes_page_prose(self):
        prose=self.chunk('prose','Dose linearity was examined.')
        table=self.chunk('answer','<table><tr><th>Cmax median mean 45 mg</th></tr><tr><td>2.4</td></tr></table>')
        excluded=self.chunk('excluded','Cmax median mean 45 mg Cmax')
        other=self.chunk('other','Cmax median mean 45 mg',page=2)
        adapter=object.__new__(BudgetedEvidence)
        adapter.by_page={('doc','v',1):[prose,table,excluded],('doc','v',2):[other]}
        got=adapter.page_candidates([prose],{'prose','answer','other'},'Cmax median mean 45 mg')
        self.assertEqual([c['chunk_id'] for c in got],['answer','prose'])

    def test_explicit_table_label_selects_its_source_without_crossing_page(self):
        a=self.chunk('a','Table 2: safety results')
        b=self.chunk('b','Table 7: post hoc results')
        adapter=object.__new__(BudgetedEvidence)
        adapter.by_page={('doc','v',1):[a,b]}
        got=adapter.page_candidates([a],{'a','b'},'Table 7 post hoc results')
        self.assertEqual(got[0]['chunk_id'],'b')

    def test_caption_is_not_counted_twice_and_adult_plural_matches_printed_heading(self):
        adult=self.chunk('adult','Adult recommended dosage for plaque psoriasis: initially, 4 weeks later, then every 12 weeks.')
        child=self.chunk('child','Recommended dose for pediatric patients with plaque psoriasis')
        child['structure']['caption']=child['text']
        adapter=object.__new__(BudgetedEvidence);adapter.by_page={('doc','v',1):[child,adult]}
        got=adapter.page_candidates([child],{'child','adult'},'Adults recommended doses for plaque psoriasis initially then every 12 weeks')
        self.assertEqual(got[0]['chunk_id'],'adult')

    def test_endpoint_table_beats_repeated_dose_prose(self):
        prose=self.chunk('prose','Population analysis: median time after single 45 mg or 90 mg subcutaneous dose in psoriasis. '*4)
        table=self.chunk('table','Single 45 mg or 90 mg subcutaneous administration in adult patients with psoriasis. '
            '<table><tr><td>Median</td><td>Mean</td></tr><tr><td>Cmax</td><td>2.4</td></tr></table>')
        adapter=object.__new__(BudgetedEvidence);adapter.by_page={('doc','v',1):[prose,table]}
        got=adapter.page_candidates([prose],{'prose','table'},'median average Cmax 45 mg 90 mg single subcutaneous administration adults psoriasis')
        self.assertEqual(got[0]['chunk_id'],'table')

    def test_link_from_earlier_page_does_not_demote_a_later_hit(self):
        first=self.chunk('first','first page',1)
        table=self.chunk('table','endpoint table',2)
        third=self.chunk('third','third page',3)
        chunks=[first,table,third]
        adapter=object.__new__(BudgetedEvidence)
        adapter.chunks=chunks;adapter.by_id={c['chunk_id']:c for c in chunks}
        adapter.bundle=SimpleNamespace(units={})
        adapter.page_candidates=lambda selected,allowed,query:selected
        adapter.linked_candidates=lambda chunk,allowed:[table] if chunk is first else []
        adapter.blocks=lambda rows:{str(i):dict(c,retrieval_role=role) for i,(c,role) in enumerate(rows)}
        adapter.budget={'query':'endpoint','core_tokens':96,'counter':None,'fits':lambda blocks:len(blocks)<=2}
        with patch('rag.budgeted_evidence.view',side_effect=lambda chunk,*args:chunk):
            got=adapter.evidence(chunks,range(3))
        self.assertEqual([b['chunk_id'] for b in got.values()],['first','table'])
        self.assertEqual([b['retrieval_role'] for b in got.values()],['retrieved','retrieved'])

    def test_qualified_result_table_precedes_broad_discussion_under_tight_budget(self):
        prose=self.chunk('prose','A broad discussion about ratios.',1);prose['structure']['kind']='text'
        table=self.chunk('table','Primary pharmacokinetic parameters. Ratio and confidence intervals.',2)
        adapter=object.__new__(BudgetedEvidence);adapter.chunks=[prose,table];adapter.by_id={c['chunk_id']:c for c in adapter.chunks}
        adapter.bundle=SimpleNamespace(units={});adapter.page_candidates=lambda selected,allowed,query:selected
        adapter.linked_candidates=lambda chunk,allowed:[]
        adapter.blocks=lambda rows:{str(i):dict(c,retrieval_role=role) for i,(c,role) in enumerate(rows)}
        adapter.budget={'query':'GMR primary results','core_tokens':96,'counter':None,'fits':lambda blocks:len(blocks)<=1}
        with patch('rag.budgeted_evidence.view',side_effect=lambda chunk,*args:chunk):got=adapter.evidence(adapter.chunks,range(2))
        self.assertEqual([b['chunk_id'] for b in got.values()],['table'])


if __name__=='__main__':unittest.main()
