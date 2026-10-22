import unittest
from rag.evidence import population_counts as pc
from rag.evidence.source_selection import catalog


def block(text):
    return {'text':text,'product_id':'CTP43','structure':{},'document_id':'d','version_id':'v'}


class PopulationCountTests(unittest.TestCase):
    def test_pk_header_N_is_count_but_body_n_is_not(self):
        text='Table 8. Secondary PK parameters (PK set)\n\n<table><thead><tr><th>Parameter</th><th>Drug A (N=80)</th></tr></thead><tbody><tr><td>Cmax n</td><td>78</td></tr></tbody></table>'
        evidence={'E1':block(text)};c=pc.annotate(catalog(evidence),evidence,'PK set 군별 인원')
        count=next(x for x in c.values() if x['kind']=='literal_number' and x['value']=='80')
        self.assertTrue(count['value_eligible']);self.assertIn('Drug A',count['population_count_role']['arm'])
        self.assertNotIn('population_count_role',next(x for x in c.values() if x['value']=='78'))
        self.assertTrue(pc.printed_cues(block(text)))

    def test_wrong_caption_and_total_cannot_qualify_header(self):
        for caption,arm in [('Safety set','Drug A'),('PK set','Total')]:
            text=f'Table 1. Results ({caption})\n\n<table><thead><tr><th>{arm} (N=17)</th></tr></thead><tbody><tr><td>2</td></tr></tbody></table>'
            evidence={'E1':block(text)};c=pc.annotate(catalog(evidence),evidence,'PK set 군별 인원')
            self.assertFalse(any(x.get('population_count_role') for x in c.values()))

    def test_drug_number_and_percentage_are_not_counts(self):
        evidence={'E1':block('ABP 654 PK analysis set included n=78 subjects (98%).')}
        c=pc.annotate(catalog(evidence),evidence,'PK set 군별 인원')
        numeric={x['value']:x for x in c.values() if x['kind']=='literal_number'}
        self.assertFalse(numeric['654']['value_eligible']);self.assertTrue(numeric['78']['value_eligible'])
        self.assertFalse(next(c['value_eligible'] for value,c in numeric.items() if '98' in value))
