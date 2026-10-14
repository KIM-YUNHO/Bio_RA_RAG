"""Add source-table candidates matching requested endpoint/statistic headers.

This is retrieval relevance, never numerical or semantic approval. It uses the
existing closed-table bindings, scoped candidates and literal trial anchors.
No expected values, question IDs, document IDs or page numbers are consulted.
Request wording is read through closed grammatical markers (contrast/exclusion,
coordinated lists, sentence carry-over), never through particular questions.
Scope: descriptive statistics (mean, SD, median, range, geometric mean) printed
in table headers. Counts/percentages, LS-mean changes, ratios and intervals
stay on the existing retrieval path.
"""
from functools import lru_cache
import re

from rag import contract_terms, ra_quantities, ra_spans, ra_table_citations, study_registry
from rag.ra_requirements import verified_study_key

VERSION='ra-printed-statistic-table-retrieval-2-general-requests'

# Endpoint aliases the shared contract_terms inventory does not hold. That
# inventory also drives contract matching, so these stay local to retrieval.
EXTRA_ENDPOINTS=[
    ('tmax',r'(?:최고|최대)\s*혈(?:중|장|청)?\s*농도\s*도달\s*시간|time\s+to\s+(?:reach\s+)?(?:the\s+)?'
            r'(?:maximum|peak)\s+(?:observed\s+)?(?:(?:serum|plasma)\s+)?concentration'),
    ('Cmax',r'(?:maximum|peak)\s+(?:observed\s+)?(?:(?:serum|plasma)\s+)?concentration'),
    ('t1/2',r'(?<![A-Za-z])t[\s$_{]*(?:\\(?:text|mathrm)\s*\{)?1\s*/\s*2\}*|(?:(?:terminal|elimination)\s+)?'
            r'half[\s-]*life|(?:소실\s*|말기\s*)?반감기'),
    ('AUCinf',r'(?<![A-Za-z])AUC[\s$_{]*0[\s$_{}]*[-–—][\s$_{}]*(?:inf|infinity|∞)\}*(?![A-Za-z])'),
    ('AUC0-t',r'(?<![A-Za-z])AUC[\s$_{]*0[\s$_{}]*[-–—][\s$_{}]*t\}*(?![A-Za-z0-9])'),
    ('AUCtau',r'(?<![A-Za-z])AUC[\s$_{]*(?:0[\s$_{}]*[-–—][\s$_{}]*)?(?:tau|τ)\}*(?![A-Za-z])'),
    ('Ctrough',r'(?<![A-Za-z])C[\s$_{]*trough\}*(?![A-Za-z])|trough\s+(?:(?:serum|plasma)\s+)?concentrations?|트로프\s*농도'),
]
ENDPOINT_TERMS=[p for _,p in EXTRA_ENDPOINTS]+[p for d,_,p in contract_terms.RULES if d=='endpoint']
# Steady-state and dose-normalized parameters are different endpoints.
SUFFIX=re.compile(r'\s*(?:,\s*)?[_{\s]*(ss)\b|\s*/\s*(D|dose)\b',re.I)

LS_GEOMETRIC=(r'(?:LS|least[\s-]+squares?)\s+geometric\s+means?|geometric\s+(?:LS|least[\s-]+squares?)\s+means?'
              r'|기하\s*최소\s*제곱\s*평균|최소\s*제곱\s*기하\s*평균')
STATISTICS=[
    # A model-adjusted LS geometric mean is not the descriptive geometric mean.
    ('geometric_ls_mean',LS_GEOMETRIC),
    ('geometric_mean',r'기하\s*평균(?!비)|geometric\s+means?(?!\s+ratio)'
                      r'|(?<![A-Za-z])GM(?![A-Za-z])'),
    ('mean',r'산술\s*평균|평균(?!비)|(?<![A-Za-z])(?:arithmetic\s+)?mean(?![A-Za-z])|(?<![A-Za-z])average(?![A-Za-z])'),
    ('sd',r'표준\s*편차|(?<![A-Za-z])standard\s+deviations?(?![A-Za-z])|(?<![A-Za-z])S\.?D\.?(?![A-Za-z])'),
    ('median',r'중앙\s*값|중앙치|(?<![A-Za-z])median(?![A-Za-z])'),
    ('range',r'최소\s*[-–~·,/]?\s*최대|(?<![A-Za-z])min(?:imum)?\s*[-–,/]\s*max(?:imum)?(?![A-Za-z])|범위|(?<![A-Za-z])range(?![A-Za-z])'),
]
COMPARISON=(r'기하\s*평균\s*비|평균\s*비(?!교)|(?<![A-Za-z])GMRs?(?![A-Za-z])|(?<![A-Za-z])(?:geometric\s+mean\s+)?ratios?(?![A-Za-z])'
            r'|신뢰\s*구간|(?:90|95)\s*%\s*(?:CI|신뢰\s*구간)|(?<![A-Za-z])CIs?(?![A-Za-z])|confidence\s+intervals?')
# Contrasted terms: comparison first so "기하평균비" is not read as "기하평균".
TERM=('(?:'+'|'.join([COMPARISON,*(p for _,p in STATISTICS),*ENDPOINT_TERMS])+')'
      r'(?:\s*\([^()]{0,30}\))?')  # an abbreviation in parentheses: "기하평균비(GMR)"
# "X과 구분", "X이 아닌", "X 말고": the contrasted run directly precedes the marker.
KO_RUN=TERM+r'(?:\s*(?:[·/]|및|또는|혹은|이나|나)\s*'+TERM+')*'
KO_CONTRAST=re.compile(KO_RUN+r'\s*(?:값|수치|통계량)?\s*(?:'
    r'(?:과|와|하고|이랑|랑)\s*(?:는|은)?\s*(?:(?:명확히|분명히|반드시|확실히|꼭)\s*)?(?:구분|구별|분리|혼동)'
    r'|(?:이|가)?\s*(?:아닌|아니라|아니고)'
    r'|(?:은|는|을|를)?\s*(?:말고|빼고|제외|대신|필요\s*없|불필요))',re.I)
# "rather than X", "not X", "distinguish ... from X": the run follows the marker.
EN_RUN=TERM+r'(?:\s*(?:,|/|\band\b|\bor\b|\bnor\b)\s*(?:the\s+)?'+TERM+')*'
EN_CONTRAST=re.compile(r'(?:(?<![A-Za-z])not|n\'t)(?:\s+(?:include|report|use|need|want|show)\w*)?\s+(?:the\s+|a\s+|an\s+|any\s+)?'+EN_RUN+
    r'|(?<![A-Za-z])(?:rather\s+than|instead\s+of|other\s+than|as\s+opposed\s+to|excluding|except(?:\s+for)?'
    r'|(?:distinguish|separate|differentiate)\w*\s+(?:\w+\s+){0,3}?from)\s+(?:the\s+|a\s+|an\s+|any\s+)?'+EN_RUN,re.I)
# Ratios, confidence intervals, adjusted/subgroup analyses and explicit
# trial Parts need their existing richer source-scope path. A header match
# alone cannot establish those additional relationships.
COMPLEX=re.compile(COMPARISON+r'|\bANCOVA\b|사후|post.hoc|pooled|통합|하위군|subgroup|adjusted|보정|\bPart\s*\d',re.I)
MASK='\x00'
# Endpoints joined only by list punctuation share the statistics that follow.
JOIN=re.compile(r'(?:\s|\([^()]{0,40}\)|[,·/&]|및|과|와|그리고|이랑|랑|and|or|또는)*',re.I)
# "the mean and SD of t1/2": a statistic followed by of/for names a later endpoint.
FORWARD=re.compile(r'(?:\s|[,/·]|and|or|및|과|와)*(?:of|for)\s+(?:the\s+)?',re.I)
# "범위" and "range" also mean scope; they are a statistic only beside another.
BESIDE=re.compile(r'(?:\s|[·,/()\[\]&]|및|과|와|의|and|or)*',re.I)
NUMERIC_CELL=re.compile(r'[\d\s.,+−\-–()±]+')
QUALIFIERS={'analysis_set','timepoint','route','population','dose'}
SPECIAL_ANALYSES={'analysis_set','covariate','analysis_timing','analysis_model','analysis_role'}


@lru_cache(maxsize=8192)
def normal(text):
    # NFKC turns ½ into 1⁄2 (U+2044); printed tables use both forms.
    return ra_quantities.normalized(text).replace('\u2044','/')


def first_longest(found):
    kept=[]
    for start,end,value in sorted(found,key=lambda m:(m[0],m[0]-m[1])):
        if not kept or start>=kept[-1][1]:kept.append((start,end,value))
    return kept


@lru_cache(maxsize=8192)
def endpoints(text):
    """(start, end, key) endpoint mentions in normalized text."""
    found=[(a['start'],a['end'],a['value']) for a in contract_terms.atoms(text) if a['dimension']=='endpoint']
    found+=[(m.start(),m.end(),value) for value,pattern in EXTRA_ENDPOINTS for m in re.finditer(pattern,text,re.I)]
    result=[]
    for start,end,value in first_longest(found):
        suffix=SUFFIX.match(text,end)
        result.append((start,end,value.casefold()+(',ss' if suffix and suffix[1] else '/d' if suffix and suffix[2] else '')))
    return tuple(result)


def statistics(text):
    return first_longest([(m.start(),m.end(),role) for role,pattern in STATISTICS for m in re.finditer(pattern,text,re.I)])


def sentences(text):
    start=0
    for match in re.finditer(r'(?<=[.!?。])\s+|\n+',text):
        yield start,match.start();start=match.end()
    yield start,len(text)


def beside(mention,marks,text):
    before=[e for s,e in marks if e<=mention[0]]
    after=[s for s,e in marks if s>=mention[1]]
    return bool(before and BESIDE.fullmatch(text[max(before):mention[0]]) or
                after and BESIDE.fullmatch(text[mention[1]:min(after)]))


def requested(text):
    """{(endpoint, statistic)} the request asks for; empty means not handled here."""
    text=normal(text)
    for pattern in (KO_CONTRAST,EN_CONTRAST):
        text=pattern.sub(lambda m:MASK*len(m[0]),text)
    if COMPLEX.search(text):return set()
    found=endpoints(text);stats=statistics(text)
    marks=[(s,e) for s,e,_ in [*found,*stats]]
    stats=[m for m in stats if not re.fullmatch(r'범위|range',text[m[0]:m[1]],re.I) or
           beside(m,[x for x in marks if x!=(m[0],m[1])],text)]
    bare=''.join(' ' if any(s<=n<e for s,e,_ in stats) else ch for n,ch in enumerate(text))
    wanted,carried,pending=set(),[],[]
    for start,stop in sentences(text):
        groups=[]
        for mention in (m for m in found if start<=m[0]<stop):
            if groups and JOIN.fullmatch(text[groups[-1][-1][1]:mention[0]]):groups[-1].append(mention)
            else:groups.append([mention])
        own=[m for m in stats if start<=m[0]<stop]
        if groups and pending:
            # Statistics stated before any endpoint ("평균과 SD를 알려줘. 대상은 Cmax").
            wanted.update((key,role) for _,_,role in pending for group in groups for _,_,key in group);pending=[]
        for s0,s1,role in own:
            before=[g for g in groups if g[-1][1]<=s0]
            after=[g for g in groups if g[0][0]>=s1]
            # A sentence without endpoints continues the previous endpoint sentence.
            targets=([after[0]] if after and FORWARD.fullmatch(bare[s1:after[0][0][0]]) else
                     [before[-1]] if before else [after[0]] if after else carried)
            if not targets:pending.append((s0,s1,role))
            wanted.update((key,role) for group in targets for _,_,key in group)
        if groups:carried=groups
    return wanted


def header_roles(header):
    """Roles a printed header gives its values, in the verifier's value order."""
    if re.search(r'\bMedian\b|중앙값',header,re.I):return ['median','range']
    if re.search(LS_GEOMETRIC,header,re.I):return ['geometric_ls_mean']
    if re.search(r'\bGM\b|geometric\s+mean',header,re.I):return ['geometric_mean','geo_cv']
    if re.search(r'\bMean\b|평균',header,re.I):
        return ['mean','sd'] if re.search(r'\bSD\b|표준\s*편차',header,re.I) else ['mean']
    if re.search(r'\bSD\b|표준\s*편차',header,re.I):return ['sd']
    if re.search(r'\bRange\b|\bMin(?:imum)?\b|\bMax(?:imum)?\b|범위',header,re.I):return ['range']
    return []


@lru_cache(maxsize=4096)
def signature(text):
    block={'document_id':'source-table','version_id':'source-table','text':text}
    spans,_=ra_spans.catalog({'E1':block})
    relations=ra_table_citations.bindings({'E1':block},spans)
    result=set()
    for alias,relation in relations.items():
        raw=ra_quantities.normalized(spans[alias]['quote']).strip()
        if not NUMERIC_CELL.fullmatch(raw):continue
        header=normal('\n'.join(spans[a]['quote'] for a in
            relation['column_header_span_ids']+relation['row_label_span_ids']))
        found=endpoints(header)
        if not found:continue
        # Endpoint names ("C max", "Tmax") must not read as Max/Min headers.
        roles=header_roles(''.join(' ' if any(s<=n<e for s,e,_ in found) else ch for n,ch in enumerate(header)))
        # Relevance does not need the verifier's unit parser; printed values
        # still pass their own unit, role and group checks later.
        count=len(ra_quantities.NUMBER.findall(raw))
        if roles:result.update((key,roles[min(n,len(roles)-1)]) for _,_,key in found for n in range(count))
    return frozenset(result)


def qualifiers(text):
    found={}
    for atom in contract_terms.atoms(text):
        dimension=atom['dimension']
        if dimension in QUALIFIERS or dimension in SPECIAL_ANALYSES and atom['value']!='primary':
            found.setdefault(dimension,set()).add(contract_terms.key(atom['value']))
    return found


def table_context(chunk):
    text=chunk['text'];opening=text.lower().find('<table')
    headers=[c['value'] for layout in ra_table_citations.layouts(text) for c in layout['headers']]
    return normal(' '.join([chunk.get('structure',{}).get('caption') or '',text[:max(opening,0)],*headers]))


def fit(asked,printed):
    """(conflicting, matching, unrequested-special) qualifier counts of one table."""
    conflicts=sum(1 for d in QUALIFIERS if asked.get(d) and printed.get(d) and not asked[d]&printed[d])
    matching=sum(1 for d in asked if asked[d]&printed.get(d,set()))
    return conflicts,matching,sum(1 for d in printed if d in SPECIAL_ANALYSES and d not in asked)


def hits(request, idx, chunks, limit=5, relevance=None):
    """Matching scoped tables, best cover of the requested endpoints first.

    Every scoped table is compared before any is chosen. Tables printing a
    population/timepoint/analysis set/route/dose other than the requested one
    are not added; ties fall to matching qualifiers, then to tables without an
    unrequested special analysis (ADA subgroup, covariate-adjusted, post hoc),
    then to relevance(i), the caller's own query similarity (else scan order).
    """
    wanted=requested(request)
    if not wanted:return []
    roles={}
    for key,role in wanted:roles.setdefault(key,set()).add(role)
    asked=qualifiers(normal(request))
    candidates=[]
    for order,i in enumerate(idx):
        chunk=chunks[int(i)]
        if chunk.get('structure',{}).get('kind')!='table_candidate':continue
        if not roles.keys()&{key for _,_,key in endpoints(normal(chunk['text']))}:continue
        product=chunk.get('product_id')
        studies={study_registry.top(k) for _,_,k in study_registry.finditer(request,product)}
        if studies:
            printed={study_registry.top(k) for _,_,k in study_registry.finditer(chunk['text'],product)}
            verified=verified_study_key(chunk)
            if printed:
                if not printed<=studies:continue
            elif verified is None or study_registry.top(verified[-1]) not in studies:
                continue
        signed=signature(chunk['text'])
        # A table answers an endpoint only when it prints every statistic
        # requested for it; a lone SD row cannot stand in for Mean and SD.
        covered=frozenset(k for k,r in roles.items() if all((k,x) in signed for x in r))
        if not covered:continue
        conflicts,matching,special=fit(asked,qualifiers(table_context(chunk)))
        # A header match never promotes a table printed for another population,
        # timepoint, analysis set, route or dose; hybrid ranking still sees it.
        if conflicts:continue
        candidates.append((int(i),covered,-matching,special,relevance(int(i)) if relevance else -order))
    ordered,left=[],set(roles)
    while candidates and len(ordered)<limit:
        best=min(candidates,key=lambda c:(-len(c[1]&left),-len(c[1]),c[2],c[3],-c[4]))
        candidates.remove(best);ordered.append(best);left-=best[1]
    return [(float(sum(len(roles[k]) for k in c[1])),c[0]) for c in ordered]
