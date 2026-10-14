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
