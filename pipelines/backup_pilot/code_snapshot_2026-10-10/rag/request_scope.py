"""Bind model interpretations to explicit request and eligible-document boundaries."""
from copy import deepcopy
import re
import unicodedata

from rag import study_registry as sr
from rag.evidence import contains_quote


def named(text, identifiers):
    text = unicodedata.normalize('NFKC', text or '')
    return any(re.search(r'(?<![A-Za-z0-9])'+re.escape(unicodedata.normalize('NFKC', value))+
        r'(?![A-Za-z0-9])', text, re.I) for value in identifiers if value)


def bind_request_scope(question, interpretation, products, agencies, *,
                       selected_products=None, selected_agencies=None, document_scope=False,
                       documents=()):
    """Explicit UI scope takes precedence; identified conflicting targets stop planning.

    Registry/source names check identity only. They do not infer study roles,
    recruitment countries, analysis types or a clinical question's meaning.
    """
    out = deepcopy(interpretation)
    model = {key:list(out.get(key,[])) for key in ('products','jurisdictions')}
    allowed = {'products':list(products),'jurisdictions':list(agencies)}
    pinned = {'products':bool(selected_products) or document_scope,
              'jurisdictions':bool(selected_agencies) or document_scope}
    adjustments, conflicts = [], []
    for key in ('products','jurisdictions'):
        outside = [value for value in model[key] if value not in allowed[key]]
        for value in outside:
            explicit = False
            if key == 'products':
                entry = sr.products().get(value,{})
                explicit = named(question,[value,*entry.get('names',[]),*entry.get('ko_names',[])])
            else:
                # Only source roles verified against source identities count;
                # EU/US reference-product sourcing is not a regulator request.
                names = {d['source'] for d in documents if d.get('jurisdiction')==value}
                explicit = any(m.get('jurisdiction')==value and m.get('role')=='regulator'
                    and contains_quote(question,m.get('quote')) and named(m.get('quote'),names)
                    for m in out.get('jurisdiction_mentions',[]))
            if explicit or not pinned[key]:
                conflicts.append({'field':key,'value':value,
                    'reason':'explicit_target_outside_request' if explicit else 'inferred_target_unavailable'})
        effective = allowed[key] if pinned[key] else [v for v in model[key] if v in allowed[key]]
        if not model[key] and not pinned[key]:
            effective = allowed[key]
        if effective != model[key]:
            adjustments.append({'field':key,'model':model[key],'effective':effective})
        out[key] = effective
    # Empty eligible scopes (e.g. excluded protocol) must remain empty, even
    # when the model selects a registry product or an agency.
    if not products or not agencies:
        conflicts = []
    out['request_scope'] = {'model':model,'allowed':allowed,'pinned':pinned,
        'adjustments':adjustments,'conflicts':conflicts,
        'status':'conflict' if conflicts else 'empty' if not products or not agencies else 'applied'}
    if conflicts:
        out['scope_warnings'] = [*out.get('scope_warnings',[]),
            '질문의 제품·기관 해석과 선택한 검색 범위가 다릅니다. 검색 범위를 확인해 주세요.']
    elif adjustments:
        out['scope_warnings'] = [*out.get('scope_warnings',[]),
            '화면에서 선택한 제품·기관·문서 범위를 검색에 적용했습니다.']
    return out
