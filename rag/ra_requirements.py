"""Conservative request gates, not a general clinical meaning classifier."""
from copy import deepcopy
import re

from rag import ra_quantities, study_registry
from rag.request_scope import named

VERSION = 'ra-original-request-gates-2-temporal-regimen'
ROUTES = {'IV': r'\bintravenous\b|(?<![A-Za-z0-9])(?:IV(?![A-Za-z0-9])|I\.V\.)|정맥',
          'SC': r'\bsubcutaneous\b|(?<![A-Za-z0-9])SC(?![A-Za-z0-9])|피하'}
FACETS = {
    'dose': r'용량|투여량|\bdos(?:e|ing)\b',
    'route': r'투여\s*경로|용량\s*(?:[·ㆍ/]|과|및)\s*경로|\broute\b',
    'comparator': r'대조약|대조군|비교약|\bcomparator\b|reference (?:medicinal )?product|Stelara|스텔라라|compared\s+with\s+(?:ustekinumab|reference)',
    'document_status': r'문서\s*상태|채택|초안|시행|발효|document status|\badopt(?:ion|ed)\b|\bdraft\b|coming into effect',
    'applicability': r'적용\s*범위|적용\s*대상|적용된다|다루(?:며|는|고)|\bscope\b|\bapplicab(?:le|ility)\b|\baddresses\b',
    'bridging': r'브리징|브리지|\bbridg(?:e|ing)\b|과학적\s*연관성',
    'healthy_population': r'건강인|건강한\s*(?:피험자|지원자|대상자|성인)|\bhealthy\s+(?:adult\s+)?(?:subjects?|volunteers?)',
    'pk_study': r'(?<![A-Za-z])PK(?![A-Za-z])|pharmacokinetic',
    'design': r'설계|study design|trial design',
    'follow_up': r'추적\s*(?:기간|시점)|follow.up|sampling\s*(?:period|time)',
    'eligibility': r'선정\s*기준|포함\s*기준|eligibility|inclusion criteria',
    'table_statistics': r'산술평균|표준편차|중앙값|기하평균|\bmean\b|\bmedian\b|\bSD\b',
    'dosing_schedule': r'투여\s*(?:일정|시점|간격)|유지\s*(?:일정|간격)|dosing\s*(?:schedule|interval)|maintenance\s*(?:schedule|interval)',
}


def verified_study_key(block):
    from rag.source_study_scope import heading_conflict
    if heading_conflict(block):
        return None
    for anchor in reversed(block.get('structure', {}).get('heading_anchors', [])):
        if anchor.get('kind') != 'study' or not anchor.get('heading_location', {}).get('block_id'):
            continue
        keys = {key for _, _, key in study_registry.finditer(anchor.get('title', ''), block.get('product_id'))}
        if len(keys) == 1:
            return (block.get('document_id'), block.get('version_id'), next(iter(keys)))
    return None


def fully_checkable_request(text):
    """Close only a completely recognized dose/route question, never broad RA prose."""
    wanted = conditions(text)
    if not ({'dose', 'route'} & set(wanted['facets']) or wanted['routes']):
        return False
    if set(wanted['facets']) - {'dose', 'route', 'healthy_population', 'pk_study'}:
        return False
    remaining = text
    # A literal registered study identity requests a source boundary, not an
    # additional clinical value. Remove complete identities before product aliases.
    study_spans = {(a,b) for product in study_registry.products() for a,b,_ in study_registry.finditer(remaining,product)}
    for a,b in sorted(study_spans,reverse=True): remaining=remaining[:a]+' '+remaining[b:]
    aliases = [alias for product, meta in study_registry.products().items()
               for alias in [product, *meta.get('names', []), *meta.get('ko_names', [])]]
    for alias in sorted(aliases, key=len, reverse=True):
        remaining = re.sub(r'(?<![A-Za-z0-9])' + re.escape(alias) + r'(?![A-Za-z0-9])', ' ', remaining, flags=re.I)
    remaining = re.sub(r'\d+(?:\.\d+)?\s*(?:mg|µg|ug|g|mL|%)', ' ', remaining, flags=re.I)
    terms = r'알려주세요|알려줘|보여주세요|보여줘|정리해주세요|정리해줘|확인해주세요|확인해줘|설명해주세요|설명해줘|심사보고서|문서에서|문서|원문으로|원문|사용한|실제|투여량|투여|용량|경로|건강인\s*대상|건강인|건강한|피험자|지원자|시험|연구|제품|제제'
    remaining = re.sub(terms, ' ', remaining)
    remaining = re.sub(r'(?<![A-Za-z0-9])(?:EMA|FDA|HC|EU|US|CA|PK|SC|IV|healthy|adult|subjects?|volunteers?|pharmacokinetics?|study|trial|actual|dose|dosage|dosing|route|administration|what|was|is|the|and|of|in|for|please|tell|me)(?![A-Za-z0-9])', ' ', remaining, flags=re.I)
    remaining = re.sub(r'(?<![가-힣A-Za-z])(?:에서|으로|의|은|는|이|가|을|를|과|와|및)(?![가-힣A-Za-z])', ' ', remaining)
    return not re.sub(r'[\s,.?!·ㆍ/()\[\]:;-]', '', remaining)


def named_products(text):
    return [p for p, meta in study_registry.products().items()
            if named(text, [p, *meta.get('names', []), *meta.get('ko_names', [])])]


def conditions(text):
    return {'quantities': [{'value': str(v), 'unit': u} for v, u in sorted(ra_quantities.quantities(text))],
            'routes': [route for route, pattern in ROUTES.items() if re.search(pattern, text, re.I)],
            'facets': [facet for facet, pattern in FACETS.items() if re.search(pattern, text, re.I)]}


def route_task(question, task):
    """Reading boundaries from the planner's source roles; no keyword routing.

    A guideline role the planner chose must be cited for the task to complete. A task
    reading ONLY guidance keeps product precedents out of its context. Search queries
    are retained.
    """
    out = deepcopy(task)
    text = task['request']
    if 'guideline' in out['source_roles']:
        out['required_source_roles'] = ['guideline']
        if out['source_roles'] == ['guideline']:
            out['reading_source_roles'] = ['guideline']
    printed = conditions(question if not named_products(text) else text)
    if 'answer_facets' in task:
        # Printed doses and routes stay literal requirements; what kinds of facts were
        # asked is the planner's reading (answer_facets), not keyword patterns.
        out['requested_conditions'] = {'source': 'planner', 'quantities': printed['quantities'],
            'routes': printed['routes'], 'facets': list(dict.fromkeys(
                f['facet'] for f in task['answer_facets'] if f['facet'] != 'calculation')),
            'endpoints': list(dict.fromkeys(f['detail'] for f in task['answer_facets']
                                            if f['facet'] == 'table_statistics' and f.get('detail')))}
    else:
        out['requested_conditions'] = printed
        # Keep explicit trailing requests even if the planner omits that sentence.
        original = conditions(question)
        for facet in ('document_status', 'applicability'):
            if facet in original['facets'] and facet not in out['requested_conditions']['facets']:
                out['requested_conditions']['facets'].append(facet)
    from rag.ra_tasks import SCOPE_NOTE
    # A pasted scope note is not a request; keep it out of unresolved-request lists.
    out['original_request_segments'] = [s.strip() for s in re.split(r'(?<=[?!。])\s*|(?<=\.)\s+', question)
                                        if s.strip() and not SCOPE_NOTE.search(s)]
    return out


def study_identity(block):
    from rag.source_study_scope import heading_conflict
    if heading_conflict(block):
        return None
    anchors = [h for h in block.get('structure', {}).get('heading_anchors', []) if h.get('kind') == 'study']
    if not anchors:
        return None
    return (block.get('document_id'), block.get('version_id'), anchors[-1].get('section_id'))


def study_conflict(claim, evidence):
    """One factual assertion must not silently join distinct study parents."""
    if claim.get('claim_basis') == 'comparison_of_sources':
        return None
    def logical_study(block):
        anchors = [h for h in block.get('structure', {}).get('heading_anchors', []) if h.get('kind') == 'study']
        if not anchors:
            return None
        # Distinct design/discussion headings can identify the same trial.
        # Retain a part identifier rather than collapsing all parts to a trial.
        keys = {k for _, _, k in study_registry.finditer(anchors[-1].get('title', ''), block.get('product_id'))}
        if len(keys) != 1:
            return None
        return (block.get('document_id'), block.get('version_id'), next(iter(keys)))
    for product in claim.get('products', []):
        identities = {logical_study(evidence[c['evidence']]) for c in claim['sources']
                      if evidence[c['evidence']].get('product_id') == product}
        identities.discard(None)
        if len(identities) > 1:
            return 'single_assertion_citations_cross_study_boundaries'
    return None


# Planner facets whose answer is a printed value code can compare with the cited source.
VALUE_FACETS = {'dose_route', 'dosing_schedule', 'table_statistics'}
TIME_VALUE = r'\d+\s*(?:주|일|개월|weeks?|days?|months?)(?![A-Za-z])|every\s+(?:\d+|eight|twelve)\s+weeks'


def planner_facet_found(wanted, view, endpoints):
    """A printed value for a value facet in both the claim and its cited quote; other facets need a claim."""
    from rag.ra_quantities import numbers
    both = (view['text'], view['quoted'])
    if wanted == 'dose_route':
        return all(any(q['unit'] in ('mg', 'ug', 'g') for q in conditions(t)['quantities']) or conditions(t)['routes']
                   for t in both)
    if wanted == 'dosing_schedule':
        return all(re.search(TIME_VALUE, t, re.I) for t in both)
    if wanted == 'table_statistics':
        return all(numbers(t) for t in both) and all(
            any(e.casefold() in t.casefold() for e in endpoints) for t in both if endpoints)
    return True


def coverage(question, understanding, evidence, claims):
    """Minimum observable coverage checks; a pass is not semantic approval.

    Conditions must occur in a kept claim AND that claim's own citations. No
    number from a different claim, product or uncited surrounding block counts.
    """
    rows = []
    from rag.ra_regimen import completion_view
    for task in understanding['tasks']:
        own = [c for c in claims if task['id'] in c['task_ids']]
        request = task.get('requested_conditions') or conditions(task['request'])
        planned = request.get('source') == 'planner'
        targets = named_products(task['request']) or named_products(question)
        # Planning labels are not proof boundaries. An independently checked
        # dose assigned to a comparison task can answer a dose task too, while
        # retaining THAT claim's own product/study/agency/source quotations.
        if any(request.get(k) for k in ('quantities','routes','facets')):
            # Planned tasks: the source agencies already bound retrieval (understanding scope).
            agencies = [] if planned else [agency for agency, pattern in {'EU':r'\bEMA\b|유럽의약품청',
                'US':r'\bFDA\b|미국\s*식품의약국', 'CA':r'Health Canada|캐나다\s*보건부'}.items()
                if re.search(pattern, task['request'], re.I)]
            def scoped(claim):
                blocks = [evidence[s['evidence']] for s in claim['sources']]
                if agencies and not set(agencies) <= {b.get('jurisdiction') for b in blocks}:
                    return False
                for product in targets:
                    identifier_text = re.sub(r'(?<=[A-Za-z0-9])(?=[가-힣])', ' ', task['request'])
                    studies = {k for _,_,k in study_registry.finditer(identifier_text, product)}
                    if not studies or product not in claim['products']: continue
                    cited = set()
                    for b in blocks:
                        if b.get('product_id') != product: continue
                        for a in b.get('structure',{}).get('heading_anchors',[]):
                            if a.get('kind')=='study':
                                cited.update(k for _,_,k in study_registry.finditer(a.get('title',''),product))
                    if not studies <= cited: return False
                return True
            own = [c for c in claims if scoped(c)]
        if not targets:
            targets = [None]
        checks = []
        for role in task.get('required_source_roles', []):
            ids = [c['id'] for c in own if any(
                (evidence[s['evidence']].get('doc_group') or evidence[s['evidence']].get('role')) == role
                for s in c['sources'])]
            checks.append({'kind': 'required_source_role', 'requested': role, 'claim_ids': ids, 'satisfied': bool(ids)})
        for product in targets:
            applicable = [c for c in own if product is None or product in c['products']]
            if product is not None:
                checks.append({'kind': 'product', 'requested': product, 'claim_ids': [c['id'] for c in applicable],
                               'satisfied': bool(applicable)})
            for kind, wanted in [('quantity', q) for q in request['quantities']] + [('route', r) for r in request['routes']] + [('facet', f) for f in request['facets']]:
                matching = []
                for claim in applicable:
                    cited = '\n'.join(s['quote'] for s in claim['sources']
                        if product is None or evidence[s['evidence']].get('product_id') in (product, None))
                    view = completion_view(claim, evidence, product)
                    observed, printed = conditions(view['text']), conditions(view['quoted'])
                    if kind == 'quantity':
                        found = wanted in observed['quantities'] and wanted in printed['quantities']
                    elif kind == 'route':
                        found = wanted in observed['routes'] and wanted in printed['routes']
                    elif planned:
                        found = planner_facet_found(wanted, view, request.get('endpoints', []))
                    else:
                        # Dose/route values answer a facet even when its noun is
                        # omitted in concise Korean. Other facts cannot count.
                        def has(value):
                            return wanted in value['facets'] or (wanted == 'dose' and any(q['unit'] in ('mg', 'ug', 'g') for q in value['quantities'])) or (wanted == 'route' and bool(value['routes']))
                        found = has(observed) and has(printed)
                    if found:
                        matching.append(claim['id'])
                checks.append({'kind': kind, 'requested': wanted, 'product_id': product,
                               'claim_ids': matching, 'satisfied': bool(matching)})
        # Completion is closed only when every requested facet has a code-checkable value.
        checkable = targets != [None] and (
            bool(request['facets']) and set(request['facets']) <= VALUE_FACETS if planned
            else fully_checkable_request(task['request']))
        # One-study coherence applies to study questions; a label's weight-band regimen has no study.
        study_task = not planned or bool(set(task.get('topics', [])) & {'pk_study', 'design', 'clinical_program'})
        if checkable and study_task:
            by_id = {c['id']: c for c in own}
            for product in targets:
                relevant = [c for c in checks if c.get('product_id') == product and c['kind'] in ('facet', 'quantity', 'route')]
                witnesses = []
                for check in relevant:
                    keys = {verified_study_key(evidence[s['evidence']]) for cid in check['claim_ids']
                            for s in by_id[cid]['sources'] if evidence[s['evidence']].get('product_id') == product}
                    keys.discard(None)
                    witnesses.append(keys)
                coherent = bool(witnesses) and bool(set.intersection(*witnesses))
                checks.append({'kind': 'same_verified_study', 'product_id': product,
                    'requested': 'one coherent dose/route study', 'claim_ids': [], 'satisfied': coherent})
                views = [(c, completion_view(c, evidence, product)) for c in own if product in c['products']]
                doses = {q for c, view in views for q in ra_quantities.quantities(view['text'])
                         if q[1] in ('mg', 'ug', 'g')}
                routes = {r for c, view in views for r in conditions(view['text'])['routes']}
                checks.append({'kind': 'unambiguous_regimen', 'product_id': product,
                    'requested': 'one observed dose and route', 'claim_ids': [],
                    'observed_quantities': [{'value': str(v), 'unit': u} for v, u in sorted(doses)],
                    'observed_routes': sorted(routes),
                    'historical_exclusions': [{'claim_id': c['id'], **part} for c, view in views
                                              for part in view['excluded_historical_clauses']],
                    'satisfied': len(doses) <= 1 and len(routes) <= 1})
        rows.append({'task_id': task['id'], 'request': task['request'], 'requested_conditions': request,
                     'checks': checks, 'completion_checkable': checkable,
                     'minimum_requirements_met': bool(own) and all(c['satisfied'] for c in checks)})
    return {'version': VERSION, 'tasks': rows, 'semantic_accuracy_certified': False}
