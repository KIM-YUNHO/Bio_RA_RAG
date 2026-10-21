"""Share immutable source-ID enums without widening any allowed set."""
from copy import deepcopy
import re
VERSION='shared-source-enums-1'
MAX_ENUM_VALUES=1000

def enum_count(value):
    if isinstance(value,dict):return len(value.get('enum',[]))+sum(enum_count(v) for k,v in value.items() if k!='enum')
    if isinstance(value,list):return sum(enum_count(v) for v in value)
    return 0

def compact_source_ids(schema):
    if enum_count(schema)<=MAX_ENUM_VALUES:return schema
    result=deepcopy(schema);locations=[]
    def visit(node):
        if isinstance(node,dict):
            choices=node.get('enum',[])
            if choices and all(isinstance(x,str) and re.fullmatch(r'S[1-9][0-9]*',x) for x in choices):locations.append(node)
            for k,v in node.items():
                if k!='enum':visit(v)
        elif isinstance(node,list):
            for v in node:visit(v)
    visit(result)
    memberships={}
    for n,node in enumerate(locations):
        for cid in node['enum']:memberships.setdefault(cid,set()).add(n)
    partitions={}
    for cid,mask in memberships.items():partitions.setdefault(tuple(sorted(mask)),[]).append(cid)
    definitions=result.setdefault('$defs',{})
    for n,(mask,ids) in enumerate(partitions.items()):
        name=f'allowed_source_group_{n}'
        if name in definitions:raise ValueError('Schema definition collision')
        definitions[name]={'type':'string','enum':ids}
        for index in mask:locations[index].setdefault('anyOf',[]).append({'$ref':'#/$defs/'+name})
    for node in locations:node.pop('enum')
    return result

def assert_supported(schema):
    if enum_count(schema)>MAX_ENUM_VALUES:raise ValueError('Structured output schema exceeds 1000 enum values; reduce supplied context before calling API')
