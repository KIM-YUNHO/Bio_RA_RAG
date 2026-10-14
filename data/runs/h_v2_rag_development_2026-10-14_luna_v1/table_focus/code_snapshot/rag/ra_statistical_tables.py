"""Add source-table candidates matching requested endpoint/statistic headers.

This is retrieval relevance, never numerical or semantic approval. It uses the
existing closed-table bindings, scoped candidates and literal trial anchors.
No expected values, question IDs, document IDs or page numbers are consulted.
"""
from functools import lru_cache
import re

from rag import ra_quantities, ra_spans, ra_table_citations, study_registry
from rag.ra_requirements import verified_study_key

VERSION='ra-printed-statistic-table-retrieval-1'
ENDPOINT=re.compile(r'C\s*max|T\s*max|t\s*1/2|AUC\s*(?:inf|last)',re.I)


def clean(text):
    text=ra_quantities.normalized(text)
    return re.sub(r'[{}_]', '', text).replace('½','1/2')


def endpoint_key(text):return re.sub(r'\s+','',text).casefold()


def requested(text):
    # A later sentence saying "distinguish from geometric mean" must not turn
    # the requested arithmetic values into geometric-mean retrieval targets.
    main=re.split(r'(?<=[.!?])\s+',text.strip(),maxsplit=1)[0]
    main=re.sub(r'(?:중앙값\s*(?:이나|또는)\s*)?(?:기하\s*평균|산술\s*평균|중앙값)'
                r'\s*(?:과|와)는?\s*(?:명확히\s*)?구분.*$', '', main)
    # Ratios, confidence intervals, adjusted/subgroup analyses and explicit
    # trial Parts need their existing richer source-scope path. A header match
    # alone cannot establish those additional relationships.
    if re.search(r'기하평균비|평균비|\bGMR\b|\bratio\b|신뢰구간|confidence|\bANCOVA\b|'
                 r'사후|post.hoc|pooled|통합|하위군|subgroup|adjusted|보정|\bPart\s*\d',main,re.I):
        return set()
    main=clean(main);mentions=list(ENDPOINT.finditer(main));wanted=set()
    for n,match in enumerate(mentions):
        stop=mentions[n+1].start() if n+1<len(mentions) else len(main)
        tail=main[match.end():stop];roles=[]
        if re.search(r'기하\s*평균|geometric\s+mean|\bGM\b',tail,re.I):roles.append('geometric_mean')
        elif re.search(r'산술\s*평균|평균|\bmean\b',tail,re.I):roles.append('mean')
        if re.search(r'표준\s*편차|standard deviation|\bSD\b',tail,re.I):roles.append('sd')
        if re.search(r'중앙값|\bmedian\b',tail,re.I):roles.append('median')
        if re.search(r'범위|\brange\b',tail,re.I):roles.append('range')
        wanted.update((endpoint_key(match[0]),role) for role in roles)
    return wanted


@lru_cache(maxsize=2048)
def signature(text):
    block={'document_id':'source-table','version_id':'source-table','text':text}
    spans,_=ra_spans.catalog({'E1':block})
    relations=ra_table_citations.bindings({'E1':block},spans)
    result=set()
    for alias,relation in relations.items():
        header=clean('\n'.join(spans[a]['quote'] for a in
            relation['column_header_span_ids']+relation['row_label_span_ids']))
        endpoints={endpoint_key(m[0]) for m in ENDPOINT.finditer(header)}
        for role in ('mean','sd','median','range','geometric_mean'):
            if endpoints and ra_table_citations.binding_quantities(alias,relation,spans,role=role):
                result.update((endpoint,role) for endpoint in endpoints)
    return frozenset(result)


def hits(request, idx, chunks, limit=5):
    wanted=requested(request)
    if not wanted:return []
    result=[]
    for i in idx:
        chunk=chunks[int(i)]
        if chunk.get('structure',{}).get('kind')!='table_candidate':continue
        product=chunk.get('product_id')
        studies={study_registry.top(k) for _,_,k in study_registry.finditer(request,product)}
        if studies:
            printed={study_registry.top(k) for _,_,k in study_registry.finditer(chunk['text'],product)}
            verified=verified_study_key(chunk)
            if printed:
                if not printed<=studies:continue
            elif verified is None or study_registry.top(verified[-1]) not in studies:
                continue
        if wanted<=signature(chunk['text']):result.append((float(len(wanted)),int(i)))
        if len(result)>=limit:break
    return result
