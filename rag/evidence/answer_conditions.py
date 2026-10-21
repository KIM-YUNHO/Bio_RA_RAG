"""Literal condition fields with independent citations and explicit display labels."""
from copy import deepcopy
from rag.evidence.evidence import contains_quote, contains_value, normalize
from rag.evidence.table_evidence import contains_cell_value, cells

DIMENSIONS = {'study':'시험', 'arm':'투여군', 'comparison':'비교(원문)', 'analysis':'분석',
              'population':'분석군', 'endpoint':'평가변수', 'statistic':'통계량',
              'timepoint':'시점', 'dose':'용량', 'eligibility':'적격 조건', 'unit':'단위',
              'row_header':'행 제목(원문)','column_header':'열 제목(원문)',
              'route':'투여 경로','dosing_interval':'투여 간격','formulation':'제형','weight_point':'체중 조건'}


def schema(base):
    result = deepcopy(base)
    item = result['schema']['properties']['items']['items']
    item['properties']['conditions'] = {'type':'array','items':{'type':'object','properties':{
        'dimension':{'type':'string','enum':list(DIMENSIONS)},'value':{'type':'string'},
        'quote':{'type':'string'},'evidence':{'type':'string'}},
        'required':['dimension','value','quote','evidence'],'additionalProperties':False}}
    item['required'].append('conditions')
    return result


def quote_schema(base):
    result=schema(base)
    condition=result['schema']['properties']['items']['items']['properties']['conditions']['items']
    del condition['properties']['value'];condition['required'].remove('value')
    return result


RULES = '''
Every answer item also carries conditions, an array of literal source fields.
Include the arm, comparison, analysis, population, endpoint, statistic, timepoint,
dose and eligibility conditions needed to distinguish the requested fact. Omit
unknown dimensions. Each condition has dimension, value, quote and evidence (one
block ID). Copy each value and quote literally from its cited block. Use a printed
comparison phrase in its original order (e.g. SB17/EU), never translate or reverse
its direction. Include eligibility qualifications for conditional treatment entry.
Conditions may cite a header or nearby prose in a different supplied block. They
are source candidates, not approved semantic assignments. Keep quotes short.
'''

QUOTE_RULES = '''
Every answer item also carries conditions, an array of source citations.
Each condition contains ONLY dimension, quote and evidence (ONE block ID).
For each study-specific item, use dimension study to quote the requested
study's literal identifier from the supplied source text or source_heading block.
Registry keys such as ST_* are metadata, NEVER a source quotation. Copy the
printed study name, including its punctuation, from a supplied literal block.
The study citation must belong to the value's section or its source heading,
never an unrelated trial on another page. Metadata alone is insufficient.
For GMR questions, identify the original analysis. Report primary results first;
do not substitute post-hoc or weight-adjusted results for the primary analysis.
Use dimensions arm, comparison, analysis, population, endpoint, statistic,
timepoint, dose, eligibility and unit when needed to distinguish the requested
fact. Copy a SHORT contiguous SOURCE QUOTE EXACTLY. Do not translate, paraphrase
or invent a condition value. The application displays the source quote itself.
For comparison, quote the printed comparison phrase in its original order.
For conditional treatment entry, quote the COMPLETE eligibility qualification.
Omit unknown conditions. A condition can cite a supplied header or nearby prose
in another block. Never include a column-heading unit in the numeric cell value:
return the cell value alone and cite the unit as a separate condition.
For dosing questions, provide separate source values for every requested dose,
initial/maintenance interval and route. Cite route and dosing_interval conditions
with their complete printed qualifications. A dose alone does not answer a
question asking for the dosing schedule. If a question's stated dose or analysis
contradicts the source, explain that premise with a source-supported condition;
do not substitute a neighbouring result as an exact answer.
'''


def validate(items, evidence):
    rejected = []
    for item in items:
        kept = []
        for condition in item.get('conditions',[]):
            key = condition.get('evidence')
            if (condition.get('dimension') not in DIMENSIONS or key not in evidence
                    or not contains_quote(evidence[key]['text'],condition.get('quote'))
                    or '[UNSUPPLIED SOURCE GAP]' in condition.get('quote','')
                    or ('value' in condition and not (contains_value(condition.get('quote'),condition.get('value'))
                        or contains_cell_value(condition.get('quote'),condition.get('value'))))):
                rejected.append(condition)
            else:
                if 'value' not in condition:
                    quote=condition['quote'];parsed=cells(quote)
                    value=parsed[0]['value'] if len(parsed)==1 and quote.strip()==quote[parsed[0]['start']:parsed[0]['end']].strip() else normalize(quote)
                    condition={**condition,'value':value,'value_basis':'literal_source_quote','semantic_approval':False}
                kept.append(condition)
        item['conditions'] = kept
        item['condition_evidence'] = list(dict.fromkeys(c['evidence'] for c in kept))
    return rejected


def label(item):
    import re
    fields = item.get('conditions',[])
    unresolved=item.get('unresolved_conditions',[])
    if item.get('source_only_rendering'):
        title=item['source_label']
        if unresolved:title+=' ('+', '.join(DIMENSIONS.get(k,'시험' if k=='local_trial' else k) for k in unresolved)+' 조건 미확정)'
        fields=item.get('source_display_conditions',[])
        pieces=[DIMENSIONS[c['dimension']]+': '+' '.join(c['value'].split()) for c in fields]
        intro=item.get('source_list_intro')
        if intro:pieces.append('목록 설명(원문): '+' '.join(intro['quote'].split()))
        caption=item.get('source_trial_caption')
        if caption:pieces.append('시험 설명(원문): '+' '.join(caption['quote'].split()))
        return ' · '.join([title,*pieces])
    if unresolved:return '인용값 ('+', '.join('비교' if k=='comparison' else DIMENSIONS.get(k,'시험' if k=='local_trial' else k) for k in unresolved)+' 조건 미확정)'
    if fields:
        grouped={}
        for c in fields:
            value=c['value'];dimension=c['dimension']
            if item.get('source_fact_role'):
                pattern={'population':r'(?:patients?|subjects?) weighing (?:more than |less than |over |under )?\d+(?:\.\d+)?\s*kg(?: or (?:less|more))?',
                    'dose':r'\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug)\b',
                    'route':r'subcutaneous(?:\s*\(SC\))?|intravenous(?:\s*\(IV\))?|intramuscular|SC injection|IV infusion',
                    'dosing_interval':r'initially.*?weeks?\s+later|every\s+\d+\s+weeks?'}.get(dimension)
                match=re.search(pattern,value,re.I|re.S) if pattern else None
                if dimension=='dosing_interval':
                    intervals=[m[0] for m in re.finditer(pattern,value,re.I|re.S)]
                    # The full source sentence may qualify both initial and
                    # maintenance doses. Retain both, instead of assigning the
                    # first interval to every fact from that sentence.
                    if intervals:value='; '.join(intervals)
                elif match:value=match[0]
            value=' '.join(value.split());grouped.setdefault(value,[])
            if DIMENSIONS[dimension] not in grouped[value]:grouped[value].append(DIMENSIONS[dimension])
        context=' · '.join('/'.join(dimensions)+': '+value for value,dimensions in grouped.items())
        title={'dose':'투여 용량','route':'투여 경로','dosing_interval':'투여 간격'}.get(item.get('source_fact_role'),item['source_label'])
        return context if any(c['dimension'] in ('comparison','row_header','column_header') for c in fields) else title+' · '+context
    return item['source_label']


def missing(items,question,understood):
    import re
    text=question+' '+understood.get('english_query','')
    required=set()
    if re.search(r'군별|각\s*군|per (?:treatment )?arm|each (?:treatment )?group',text,re.I):required.add('arm')
    gmr=understood.get('topic')=='gmr' or bool(re.search(r'\bGMR\b|geometric mean ratio|기하평균비',text,re.I))
    adjusted=bool(re.search(r'ANCOVA|ad[ -]?hoc|post[ -]?hoc|weight.*(?:adjust|covariat)|사후|공변량',text,re.I))
    if gmr:required.update(['comparison','endpoint','analysis'])
    if re.search(r'weight.adjust|체중.*보정|ad.hoc|ANCOVA|post.hoc|primary.*analys|주\s*분석|일차\s*분석',text,re.I):required.add('analysis')
    if understood.get('studies'):required.add('study')
    if re.search(r'Day\s*\d+|Week\s*\d+|\d+\s*(?:일|주)차',text,re.I):required.add('timepoint')
    if re.search(r'\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|ug)\b',text,re.I):required.add('dose')
    if re.search(r'투여\s*경로|route of administration|administration route',text,re.I):required.add('route')
    if re.search(r'투여\s*간격|dosing interval|dose interval|dosing schedule|frequency',text,re.I):required.add('dosing_interval')
    if not gmr and re.search(r'median|mean|중앙값|평균',text,re.I) and re.search(r'Cmax|C_max|최대',text,re.I):required.add('statistic')
    if re.search(r'weighing|\d+\s*kg.*(?:less|over|more|이하|초과)',text,re.I):required.add('population')
    result=[]
    for i,item in enumerate(items):
        item_required=set(required)
        if item.get('independent_request_audit'):
            # The injected selector checks each field's conditions against its
            # own source. Do not broadcast another field's analysis/timepoint.
            # Independent study/source attribution remains checked below.
            item_required=set()
        numeric=item.get('numeric_question_role_contract')
        if numeric is not None:
            item_required.discard('dose')
            if numeric['administered_doses']:item_required.add('dose')
        role=item.get('source_fact_role')
        if role:
            # Global route/schedule coverage is checked separately across facts;
            # each literal only needs its own distinguishing qualifiers.
            item_required.discard('route');item_required.discard('dosing_interval')
            if role=='route':item_required.discard('population')
            elif role=='dosing_interval':item_required.add('dosing_interval')
        dimensions={c['dimension'] for c in item.get('conditions',[])}
        if gmr and not adjusted and not any(c['dimension']=='analysis' and re.search(r'primary|일차|주\s*분석',c['quote'],re.I) for c in item.get('conditions',[])):
            primary_requested=bool(re.search(r'primary\s+(?:(?:PK|pharmacokinetic|statistical)\s+)?analysis|주\s*분석|일차\s*분석',question,re.I))
            proof=item.get('own_analysis_binding',{})
            own_caption=proof.get('basis')=='literal_own_table_analysis_caption' and any(c['dimension']=='analysis' and
                c['quote']==proof.get('quote') and c['evidence']==proof.get('evidence') for c in item.get('conditions',[]))
            # Default primary guidance is not a literal requirement added to
            # the Korean question. An opt-in, source-bound caption supplies the
            # actual analysis without claiming that it is primary.
            if primary_requested or not own_caption:dimensions.discard('analysis')
        if 'row_header' in dimensions:dimensions.add('arm')
        absent=item_required-dimensions
        if absent:result.append({'item_index':i,'dimensions':sorted(absent)})
    return result


def unresolved_scopes(missing):
    """Missing distinguishing conditions must not leave another group's value on screen."""
    critical={'study','comparison','endpoint','analysis','dose','statistic','population'}
    return [m for m in missing if critical.intersection(m['dimensions'])]


def attribution_missing(items,evidence,understood,missing):
    """A study quotation must be connected to the value's actual source section.

    This checks source linkage, not clinical semantics. A study ID quoted from
    an unrelated page cannot make a different trial's dose an exact answer.
    """
    from rag.retrieval import study_registry as sr
    requested=set(understood.get('studies',[]))
    result={m['item_index']:set(m['dimensions']) for m in missing}
    for index,item in enumerate(items):
        if item.get('source_context_conflicts'):result.setdefault(index,set()).add('comparison')
        targets=requested or ({item.get('study')} if item.get('study') not in (None,'none') else set())
        if not targets:continue
        if item.get('source_component_scope'):
            from rag.evidence.component_scope import validate as validate_component
            if item['study'] in targets and validate_component(item,evidence,item['study']):
                result.setdefault(index,set()).discard('study')
                if not result[index]:del result[index]
                continue
        matched=False
        values=[evidence[key] for key in item['evidence'] if key in evidence]
        contradicted=any((printed:={k for _,_,k in sr.finditer(block['text'],block.get('product_id'))}) and not printed.intersection(targets) for block in values)
        if contradicted:
            result.setdefault(index,set()).add('study')
            continue
        for condition in item.get('conditions',[]):
            if condition['dimension']!='study':continue
            context=evidence[condition['evidence']]
            keys={k for _,_,k in sr.finditer(condition['quote'],context.get('product_id'))}
            if item['study'] not in targets or item['study'] not in keys:continue
            for block in values:
                section=block['structure'].get('section_id')
                heading_ids={h['heading_location'].get('block_id') for h in block['structure'].get('heading_anchors',[])}
                context_ids={s['unit_id'] for s in context.get('source_spans',[])}
                if (block is context or section and section==context['structure'].get('section_id')
                    or context.get('retrieval_role')=='source_heading' and heading_ids & context_ids):matched=True
        if not matched:result.setdefault(index,set()).add('study')
    return [{'item_index':i,'dimensions':sorted(v)} for i,v in sorted(result.items()) if v]


def explicit_conflicts(items,question,understood):
    """Check only explicit literal dose/analysis constraints, not clinical inference."""
    import re
    text=question+' '+understood.get('english_query','')
    doses=lambda value:{(m[0],m[1].casefold()) for m in re.findall(r'(?<![\w.])(\d+(?:\.\d+)?)\s*(mg|mcg|µg|ug)\b',value,re.I)}
    requested=doses(text)
    primary=bool(re.search(r'primary\s+(?:pk\s+)?analys|주\s*분석|일차\s*분석|\bGMR\b|geometric mean ratio|기하평균비',text,re.I))
    adjusted=bool(re.search(r'ANCOVA|post[ -]?hoc|ad[ -]?hoc|weight[ -]?adjust|체중.*보정|사후|공변량',text,re.I))
    result=[]
    for index,item in enumerate(items):
        if item.get('independent_request_audit'):continue
        numeric=item.get('numeric_question_role_contract')
        own_requested={(a['number'],a['unit']) for a in numeric['administered_doses']} if numeric is not None else requested
        for condition in item.get('conditions',[]):
            conflict=False
            if condition['dimension']=='dose' and own_requested:
                source=doses(condition['quote'])
                conflict=bool(source and source.isdisjoint(own_requested))
            elif condition['dimension']=='analysis' and primary and not adjusted:
                conflict=bool(re.search(r'post[ -]?hoc|ad[ -]?hoc|weight[ -]?adjust',condition['quote'],re.I))
            if conflict:result.append({'item_index':index,'dimension':condition['dimension'],
                'source_quote':condition['quote'],'evidence':condition['evidence'],
                'reason':'explicit_requested_condition_differs_from_source'})
    return result
