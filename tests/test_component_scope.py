import unittest
from rag.component_scope import establish,validate


class ComponentScopeTests(unittest.TestCase):
    def evidence(self,caption='Part 2',doc='d'):
        text=f'Table 1. PK results ({caption})\n\n<table><tr><td>0.91</td></tr></table>'
        return {'E1':{'text':text,'document_id':'d','version_id':'v','product_id':'CTP43','structure':{'section_id':'s'}},
                'E2':{'text':'Study CT-P43 1.1','document_id':doc,'version_id':'v','product_id':'CTP43','structure':{'section_id':'s'}}}

    def candidate(self,evidence):
        text=evidence['E1']['text'];start=text.index('0.91')
        return {'evidence':'E1','kind':'table_cell','start':start,'end':start+4,'quote':'<td>0.91</td>','table_start':text.index('<table')}

    def test_own_component_and_connected_parent_allow_result(self):
        e=self.evidence();c=self.candidate(e);p=establish(c,e,'ST_CTP43_1_1_P2')
        self.assertIsNotNone(p)
        item={'value':'0.91','quote':c['quote'],'source_component_scope':p}
        self.assertTrue(validate(item,e,'ST_CTP43_1_1_P2'))
        item['value']='0.92';self.assertFalse(validate(item,e,'ST_CTP43_1_1_P2'))

    def test_other_part_mixed_parts_or_unconnected_parent_fail(self):
        for caption,doc in [('Part 1','d'),('Part 1 and Part 2','d'),('Part 2','other')]:
            e=self.evidence(caption,doc)
            self.assertIsNone(establish(self.candidate(e),e,'ST_CTP43_1_1_P2'))
        e=self.evidence();e['E2']['structure']['section_id']='other'
        self.assertIsNone(establish(self.candidate(e),e,'ST_CTP43_1_1_P2'))

    def test_component_branch_keeps_numeric_facts_in_their_part(self):
        text='Study CT-P43 1.1. In Part 1 subjects received 30 mg. In Part 2 subjects received 60 mg.'
        e=self.evidence();e['E1']['text']=text
        for value,expected in [('30 mg',False),('60 mg',True)]:
            start=text.index(value)
            c={'evidence':'E1','kind':'literal_number','start':start,'end':start+len(value),'quote':text,'quote_start':0}
            proof=establish(c,e,'ST_CTP43_1_1_P2')
            self.assertEqual(expected,proof is not None)
            if proof:self.assertTrue(validate({'value':value,'quote':text,'source_component_scope':proof},e,'ST_CTP43_1_1_P2'))

    def test_shared_design_does_not_approve_part_specific_counts(self):
        e=self.evidence();text='Study CT-P43 1.1 was a Phase 1, randomized two-part study. Part 1 enrolled 30 subjects. Part 2 enrolled 200 subjects.';e['E1']['text']=text
        c={'evidence':'E1','kind':'source_paragraph','start':0,'end':len(text),'quote':text}
        proof=establish(c,e,'ST_CTP43_1_1_P2','design');self.assertIsNotNone(proof)
        self.assertTrue(validate({'value':text,'quote':text,'source_component_scope':proof},e,'ST_CTP43_1_1_P2'))
        c.update(kind='literal_number',start=text.index('30'),end=text.index('30')+2)
        self.assertIsNone(establish(c,e,'ST_CTP43_1_1_P2','design'))

    def test_sentence_retains_its_own_paragraph_component_branch(self):
        e=self.evidence();text='Study CT-P43 1.1 was randomized. In Part 1 subjects received 30 mg. In Part 2 subjects received 60 mg. The drug was administered subcutaneously.';e['E1']['text']=text
        value='The drug was administered subcutaneously.';a=text.index(value)
        c={'evidence':'E1','kind':'source_paragraph','start':a,'end':a+len(value),'quote':value}
        proof=establish(c,e,'ST_CTP43_1_1_P2');self.assertIsNotNone(proof)
        self.assertTrue(validate({'value':value,'quote':value,'source_component_scope':proof},e,'ST_CTP43_1_1_P2'))
        e['E1']['text']=text.replace('The drug','\n\nThe drug');a=e['E1']['text'].index(value);c.update(start=a,end=a+len(value))
        self.assertIsNone(establish(c,e,'ST_CTP43_1_1_P2'))

    def test_parent_two_part_design_can_name_component_in_next_paragraph(self):
        e=self.evidence();value='Study CT-P43 1.1 was a Phase 1, two-part, randomized study.'
        e['E1']['text']=value+'\n\nPart 1 had 31 subjects. Part 2 had 62 subjects.'
        c={'evidence':'E1','kind':'source_paragraph','start':0,'end':len(value),'quote':value}
        proof=establish(c,e,'ST_CTP43_1_1_P2','design');self.assertIsNotNone(proof)
        self.assertTrue(validate({'value':value,'quote':value,'source_component_scope':proof},e,'ST_CTP43_1_1_P2'))
        self.assertIsNone(establish(c,e,'ST_CTP43_1_1_P2'))
        e['E1']['text']=value+'\n[UNSUPPLIED SOURCE GAP]\nPart 2 had 62 subjects.'
        self.assertIsNone(establish(c,e,'ST_CTP43_1_1_P2','design'))
