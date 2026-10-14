"""Question-only source IDs for typed obligations; never reads document answers.

The model chooses roles and combinations. Code supplies exact quotation offsets
and recognized canonical aliases, avoiding generated occurrence-number drift.
Unknown wording stays literal and is subject to the ordinary review checker.
"""
from copy import deepcopy
import re
from rag.chunk_schema import obj
from rag.contract_terms import atoms
from rag.contract_relations import ranges

VERSION = 'question-anchors-5-bounded-axes'


def catalog(question):
    found = list(atoms(question))
    for r in ranges(question):
        for number in r['values']:
            m=re.search(re.escape(number),question[r['start']:r['end']])
            start=r['start']+m.start()
            found.append({'dimension':'weight','value':number,'quote':number,'start':start,'end':start+len(number)})
    from rag.study_registry import products,finditer,load
    registry=load()
    studies=list(dict.fromkeys(s for pid in products() for s in finditer(question,pid)))
    studies=[s for s in studies if not any(registry[t[2]].get('parent')==s[2] for t in studies)]
    found.extend({'dimension':'study','value':identifier,'quote':question[start:end],
                  'start':start,'end':end} for start,end,identifier in studies)
    for pid,p in products().items():
        names=sorted({pid,*p['names'],*p.get('ko_names',[])},key=len,reverse=True)
        for m in re.finditer(r'(?<![A-Za-z0-9])(?:'+'|'.join(map(re.escape,names))+r')(?![A-Za-z0-9])',question,re.I):
            found.append({'dimension':'comparison','value':pid,'quote':m.group(),'start':m.start(),'end':m.end()})
    reference=r'(?:EU|US)[-\s]*(?:(?:approved|licensed)[-\s]+)?Stelara|(?:유럽|미국)\s*(?:(?:허가|승인)\s*)?(?:대조약|스텔라라)'
    from rag.contract_relations import arm
    for m in re.finditer(reference,question,re.I):
        found.append({'dimension':'comparison','value':arm(m.group()),'quote':m.group(),'start':m.start(),'end':m.end()})
    # Coordinated reference regions share a printed product noun; retain each
    # region's own literal offset rather than fabricating two full quotations.
    for m in re.finditer(r'\b(EU)\s*[·/&,]\s*(US)\s+Stelara\b',question,re.I):
        for n,region in ((1,'EU'),(2,'US')):
            found.append({'dimension':'comparison','value':region+'-Stelara','quote':m[n],
                          'start':m.start(n),'end':m.end(n),'alias_basis':'explicit_coordinated_Stelara_noun',
                          'alias_source_range':[m.start(),m.end()]})
    unique={}
    comparison_requested = bool(re.search(reference,question,re.I) or re.search(r'대비|비교|between|compared|versus|ratio',question,re.I))
    for item in sorted(found,key=lambda a:(a['start'],a['end'],a['dimension'],a['value'])):
        if item['dimension']=='comparison' and item['value'] in products() and not comparison_requested:
            continue
        identity=(item['dimension'],item['value'],item['start'],item['end'])
        if identity in unique:continue
        earlier=list(re.finditer(re.escape(item['quote']),question[:item['start']]))
        item={**item,'anchor':{'quote':item['quote'],'occurrence':len(earlier)}}
        unique[identity]=item
    return {f'Q{n+1}':{**item,'id':f'Q{n+1}'} for n,item in enumerate(unique.values())}


def schema(question,understanding_schema):
    entries=catalog(question)
    # An empty inventory still supports exact unknown request wording.
    ids={'type':'string','enum':list(entries) or ['__no_known_anchor__']}
    has_statistics=any(a['dimension']=='statistic' for a in entries.values())
    fields=[i for i,a in entries.items() if a['dimension'] in (('statistic','attribute') if has_statistics else ('endpoint','attribute'))]
    variants=[]
    for dimension in sorted({a['dimension'] for a in entries.values()}):
        if dimension in ('statistic','attribute'):continue
        choices=[i for i,a in entries.items() if a['dimension']==dimension]
        operators=['ordered_pair','unordered_pair'] if dimension=='comparison' else ['range'] if dimension=='weight' else ['eq','not_eq','one_of']
        bounds=(2,2) if dimension in ('comparison','weight') else (1,1)
        variants.append(obj({'operator':{'type':'string','enum':operators},
            'anchor_ids':{'type':'array','items':{'type':'string','enum':choices},'minItems':bounds[0],'maxItems':bounds[1]}}))
    if not variants:
        variants=[obj({'operator':{'type':'string','enum':['eq']},'anchor_ids':{'type':'array','items':ids,'minItems':1,'maxItems':1}})]
    condition={'anyOf':variants}
    shared=[v for v in variants if 'ordered_pair' not in v['properties']['operator']['enum']]
    shared_condition={'anyOf':shared or variants}
    has_conditions=any(a['dimension'] not in ('statistic','attribute') for a in entries.values())
    has_shared=any(a['dimension'] not in ('statistic','attribute','comparison') for a in entries.values())
    # Code owns occurrence indices for registered anchors. Unknown roles must
    # quote the first occurrence, extending the quote if needed for uniqueness.
    # No fake selectable field ID is offered when only unknown wording exists.
    literal=obj({'quote':{'type':'string'},'occurrence':{'type':'integer','enum':[0]}})
    group=obj({
        'id':{'type':'string'},'field_ids':{'type':'array','items':{'type':'string','enum':fields or ['__no_known_field__']},'maxItems':12},
        'literal_fields':{'type':'array','items':literal,'maxItems':8},
        'conditions':{'type':'array','items':shared_condition,'maxItems':16 if has_shared else 0},
        'literal_conditions':{'type':'array','items':literal,'maxItems':8},
        'axes':{'type':'array','maxItems':8 if has_conditions else 0,'items':obj({'options':{'type':'array','minItems':1,'maxItems':8,'items':condition}})}
    })
    axis_variants=[]
    for variant in variants:
        choices=variant['properties']['anchor_ids']['items']['enum']
        if not all(i in entries for i in choices):continue
        dimension=entries[choices[0]]['dimension']
        values={entries[i]['value'] for i in choices}
        if dimension!='comparison' and len(values)<=1:continue
        maximum=len(values) if dimension not in ('comparison','weight') else len(values)*(len(values)-1)//2
        axis_variants.append(obj({'options':{'type':'array','minItems':1,'maxItems':max(1,maximum),'items':variant}}))
    group['properties']['axes']['maxItems']=min(8,len(axis_variants))
    if axis_variants:group['properties']['axes']['items']={'anyOf':axis_variants}
    shared_dimensions={a['dimension'] for a in entries.values() if a['dimension'] not in ('statistic','attribute','comparison')}
    group['properties']['conditions']['maxItems']=len(shared_dimensions)
    def family(a):
        if a['dimension']=='attribute' and a['value']=='margin':return 'criterion'
        return a['dimension']
    groups=[]
    for kind in sorted({family(entries[i]) for i in fields}):
        variant=deepcopy(group)
        members=[i for i in fields if family(entries[i])==kind]
        variant['properties']['field_ids'].update(minItems=1,maxItems=len({entries[i]['value'] for i in members}),
            items={'type':'string','enum':members})
        variant['properties']['literal_fields']['maxItems']=0
        groups.append(variant)
    unknown=deepcopy(group)
    unknown['properties']['field_ids']['maxItems']=0
    unknown['properties']['literal_fields']['minItems']=1
    groups.append(unknown)
    requirements=obj({'request_groups':{'type':'array','minItems':1,'maxItems':16,'items':{'anyOf':groups}},
        'non_request_mentions':{'type':'array','items':obj({'anchor_id':ids,'role':{'type':'string','enum':['context','excluded']}})}})
    understanding=deepcopy(understanding_schema)
    understanding['properties']['jurisdictions']['items']['enum']=['EU','US','CA']
    understanding['properties']['jurisdiction_mentions']['items']['properties']['jurisdiction']['enum']=['EU','US','CA']
    return {'name':'query_understanding_with_anchor_ids','schema':obj({'understanding':understanding,'requirements':requirements})}


def expand(question,requirements):
    entries=catalog(question)
    def entry(identifier):
        if identifier not in entries:raise ValueError('question anchor ID is not in original question inventory')
        return entries[identifier]
    def field(identifier):
        a=entry(identifier)
        if a['dimension'] not in ('statistic','endpoint','attribute'):raise ValueError('condition anchor cannot be a requested field')
        return {'dimension':a['dimension'],'value':a['value'],'anchor':deepcopy(a['anchor'])}
    def condition(c):
        values=[entry(i) for i in c['anchor_ids']]
        dimensions={a['dimension'] for a in values}
        if len(dimensions)!=1:raise ValueError('one condition must contain one dimension')
        dimension=next(iter(dimensions))
        if dimension=='comparison' and c['operator'] not in ('ordered_pair','unordered_pair'):
            raise ValueError('comparison requires two explicit question arms')
        return {'dimension':dimension,'operator':c['operator'],
                'values':[{'value':a['value'],'anchor':deepcopy(a['anchor'])} for a in values]}
    groups=[]
    for g in requirements['request_groups']:
        known=[field(i) for i in g['field_ids']]
        # Parenthesized aliases and reused source IDs describe one obligation.
        known=list({(f['dimension'],f['value']):f for f in known}.values())
        families={('criterion' if f['dimension']=='attribute' and f['value']=='margin' else f['dimension']) for f in known}
        if len(families)>1 or known and g['literal_fields']:
            raise ValueError('distinct field families require separate scoped groups')
        if any(f['dimension']=='endpoint' for f in known) and any(a['dimension']=='statistic' for a in entries.values()):
            raise ValueError('statistical requests require endpoints as conditions, not duplicate endpoint fields')
        literals=[a for a in g['literal_fields'] if not any(a['quote']==f['anchor']['quote'] for f in known)]
        groups.append({'id':g['id'],'fields':known+[
            {'dimension':'literal_request','value':a['quote'],'anchor':deepcopy(a)} for a in literals],
            'conditions':[condition(c) for c in g['conditions']]+[
                {'dimension':'literal_condition','operator':'eq','values':[{'value':a['quote'],'anchor':deepcopy(a)}]} for a in g['literal_conditions']],
            'axes':[{'options':[condition(c) for c in axis['options']]} for axis in g['axes']]})
    mentions=[]
    for m in requirements['non_request_mentions']:
        a=entry(m['anchor_id'])
        mentions.append({'term':{'dimension':a['dimension'],'value':a['value'],'anchor':deepcopy(a['anchor'])},'role':m['role']})
    return {'request_groups':groups,'non_request_mentions':mentions}


RULES = '''Choose ONLY original-question anchor IDs from the inventory. Code copies
their exact quotations, occurrence indices and canonical labels. Reuse the SAME
ID in every group that shares a condition; never create new occurrence numbers.
Each group has field_ids, shared AND conditions, axes, literal_fields and literal_conditions.
The schema separates statistical measurements, criteria/margins, other attributes,
and unknown literal fields into different groups. Do not mix these field families.
Use a separate criterion group; copy only conditions that actually apply to it.
Comparisons ALWAYS belong in an axis, including a single comparison option.
An axis contains alternatives of ONE dimension only. When only one endpoint,
visit, analysis or study is mentioned, use its shared condition, not an axis.
Do not repeat field or condition IDs. Include every requested field family in
its own group, with its own applicable conditions.
An unknown-only group has field_ids=[]; there is no selectable placeholder ID.
literal_fields/literal_conditions copy exact original wording with occurrence=0.
If that wording repeats, extend the quote to identify its first occurrence clearly.
Use ONE compact group for independent endpoint x dose x statistic combinations.
Fields are requested statistics/attributes; endpoints are conditions when a
statistic is requested. An axis contains every requested option of ONE dimension.
Do not put alternative doses/endpoints as simultaneous eq conditions. Use separate
groups only for explicitly correlated demands. one_of means any alternative is
acceptable; it does not answer an explicit request for ALL values.
Shared conditions apply ONLY to that group's fields, never to every field in the
question. Separate a requested result's analysis/timepoint from an independently
requested criterion or margin unless the original question applies them to both.
Keep primary/supportive/sensitivity analysis roles distinct. A confidence interval
and a decision margin are distinct fields. Do not attach a result's confidence
level as a condition of the requested margin. Do not infer comparison direction.
GMR and its Korean parenthesized synonym are ONE field. Include population and route
qualifiers. A product or reference in a requested comparison is a condition, not
a context mention. comparison always has two anchor IDs: ordered_pair for an
explicit numerator/denominator, unordered_pair for a comparison without direction.
Include EVERY separately requested comparison, including reference-versus-reference.
For 'X 대비' include the questioned product and X. A product mentioned in a study
identifier can identify that comparison's product. A coordinated EU/US reference
lists two different arms. Separate references are never document-source regulators.
Unknown demands/conditions use literal_fields/literal_conditions with exact original
wording; do not force them into a known role. non_request_mentions is ONLY for
actually contextual or excluded mentions. Empty optional lists must be [].
An endpoint/dose axis contains single-ID eq options. A weight range contains two
weight IDs with range, NEVER ordered_pair. A comparison contains two comparison
IDs, NEVER a single product ID or endpoint/dose IDs. Do not duplicate known fields
in literal_fields. For a product-monograph query without requested comparisons,
the product name belongs only in understanding, not in the request conditions.
understanding sources are limited to EMA/FDA/HC (EU/US/CA); comparator sourcing
regions do not select document agencies. No documents or answer values are provided.'''
