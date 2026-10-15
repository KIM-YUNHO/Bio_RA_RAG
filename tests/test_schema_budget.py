import unittest
from rag.schema_budget import compact_source_ids,enum_count,assert_supported

def allowed(node,root):
    if '$ref' in node:return allowed(root['$defs'][node['$ref'].split('/')[-1]],root)
    if 'anyOf' in node:return set().union(*(allowed(n,root) for n in node['anyOf']))
    return set(node.get('enum',[]))

class SchemaBudgetTests(unittest.TestCase):
    def test_large_overlapping_roles_keep_exact_allowed_source_sets(self):
        choices=[f'S{i}' for i in range(1,701)]
        root={'type':'object','properties':{'value':{'type':'string','enum':choices[:500]},'dose':{'type':'string','enum':choices[300:]},'study':{'type':'string','enum':choices[100:600]}},'additionalProperties':False,'required':['value','dose','study']}
        result=compact_source_ids(root)
        self.assertGreater(enum_count(root),1000);self.assertLessEqual(enum_count(result),1000)
        for name,node in root['properties'].items():self.assertEqual(set(node['enum']),allowed(result['properties'][name],result))
        self.assertNotIn('S650',allowed(result['properties']['value'],result))
        assert_supported(result)

    def test_small_schema_and_non_source_limits_are_preserved(self):
        small={'type':'string','enum':['S1','S2']};self.assertIs(small,compact_source_ids(small))
        with self.assertRaises(ValueError):assert_supported({'enum':[str(i) for i in range(1001)]})


class RaAnswerEnumBudgetTests(unittest.TestCase):
    class Client:
        input_limit=10**6
        class tokenizer:
            messages=staticmethod(lambda m:10)
            one=staticmethod(lambda s:5)

    @staticmethod
    def evidence(paragraphs):
        text='\n\n'.join(f'Paragraph {i}.' for i in range(paragraphs))
        return {'E1':{'text':text,'version_id':'v1','document_id':'d1','product_id':'p1','chunk_id':'c1'}}

    def test_large_span_catalog_switches_to_pattern_and_is_token_bound(self):
        from rag import ra_answer
        from rag.schema_budget import enum_count
        tasks=[{'id':'T1'}]
        small=ra_answer.schema(self.evidence(5),tasks,'spans')
        large=ra_answer.schema(self.evidence(1200),tasks,'spans')
        self.assertEqual(15,ra_answer.cost(self.Client,[],small))
        self.assertTrue(ra_answer.fits(self.Client,[],small))
        self.assertLessEqual(enum_count(large['schema']),1000)
        self.assertTrue(ra_answer.fits(self.Client,[],large))

    def test_any_other_schema_over_enum_cap_never_fits(self):
        from rag import ra_answer
        schema={'name':'x','schema':{'type':'string','enum':[str(i) for i in range(1001)]}}
        self.assertGreater(ra_answer.cost(self.Client,[],schema),self.Client.input_limit)
