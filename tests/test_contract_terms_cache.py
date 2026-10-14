import unittest
from rag.contract_terms import atoms,_cached_atoms


class ContractTermsCacheTests(unittest.TestCase):
    def test_another_call_cannot_inherit_mutated_roles_or_offsets(self):
        text='T_{max} Mean (SD), 31 mg, 8.7 days'
        original=atoms(text);changed=atoms(text)
        changed[0]['value']='different endpoint';changed[0]['start']=999
        changed.append({'dimension':'population','value':'invented'})
        self.assertEqual(original,atoms(text))
        for atom in atoms('prefix '+text):
            self.assertEqual(atom['quote'],('prefix '+text)[atom['start']:atom['end']])
        self.assertNotEqual(original,atoms('prefix '+text))

    def test_cache_is_bounded_and_exact_source_wording_is_preserved(self):
        self.assertEqual(4096,_cached_atoms.cache_parameters()['maxsize'])
        for text in ('Cmax 11 ng/mL','Cmax 12 ng/mL','C_{max} 11 ng/mL'):
            for atom in atoms(text):self.assertEqual(atom['quote'],text[atom['start']:atom['end']])
        self.assertNotEqual(atoms('Cmax 11 ng/mL'),atoms('C_{max} 11 ng/mL'))

if __name__=='__main__':unittest.main()
