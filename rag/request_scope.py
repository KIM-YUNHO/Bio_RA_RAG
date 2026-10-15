"""Bind model interpretations to explicit request and eligible-document boundaries."""
from copy import deepcopy
from functools import lru_cache
import re
import unicodedata

from rag import study_registry as sr


def named(text, identifiers):
    text = unicodedata.normalize('NFKC', text or '')
    # Registry names may carry a market qualifier ("Wezlana (US, Canada)"); users write the bare name.
    values = {v for value in identifiers if value
              for v in (value, re.sub(r'\s*\([^()]*\)\s*$', '', value)) if v.strip()}
    return any(re.search(r'(?<![A-Za-z0-9])'+re.escape(unicodedata.normalize('NFKC', value))+
        r'(?![A-Za-z0-9])', text, re.I) for value in values)


AGENCY_NAMES = {'EU': ['EMA', 'European Medicines Agency', '유럽의약품청'],
                'US': ['FDA', 'Food and Drug Administration', '미국 식품의약국'],
                'CA': ['Health Canada', '캐나다 보건부']}
# Agency or region names: the question must name the jurisdiction somewhere (no invented agency).
JURISDICTION_NAMES = {'EU': [*AGENCY_NAMES['EU'], 'EU', '유럽', 'Europe', 'European'],
                      'US': [*AGENCY_NAMES['US'], 'US', 'U.S.', 'USA', '미국', 'United States'],
                      'CA': [*AGENCY_NAMES['CA'], '캐나다', 'Canada']}


@lru_cache(maxsize=1)
def reference_names():
    """Reference (non-biosimilar) product proper nouns from the collection targets (not the shared ingredient)."""
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'configs/collection/targets.json'
    targets = json.loads(path.read_text(encoding='utf-8'))
    references = {p.get('reference_product_id') for p in targets.get('products', []) if p.get('reference_product_id')}
    names = []
    for p in targets.get('products', []):
        if p.get('product_id') in references:
            names += [p.get('ema_name'), p.get('us_brand'), p.get('ca_brand'), *p.get('aliases', [])]
    return sorted({n for n in names if n})


def regulator_mention(question, mention):
    """The planner's regulator reading stands unless a proper noun in its quote contradicts it.

    The question must name the jurisdiction somewhere (agency or region name), so an
    invented agency cannot narrow the scope. Contradictions: the quote names a reference
    product ("EU Stelara" is a comparator's origin) or names only another agency ("EMA ..."
    labelled US). A quote that is not verbatim (stitched or paraphrased) is not a
    contradiction; ra_tasks records it.
    """
    if not isinstance(mention, dict) or mention.get('role') != 'regulator':
        return False
    quote, jurisdiction = mention.get('quote') or '', mention.get('jurisdiction')
    if jurisdiction not in AGENCY_NAMES:
        return False  # 'sponsor' (trial registry) is a source type, added only for protocol/SAP requests
    if not named(question, JURISDICTION_NAMES[jurisdiction]):
        return False
    if named(quote, reference_names()):
        return False
    others = [j for j, names in AGENCY_NAMES.items() if j != jurisdiction and named(quote, names)]
    return not others or named(quote, AGENCY_NAMES.get(jurisdiction, []))


def bind_request_scope(question, interpretation, products, agencies, *,
                       selected_products=None, selected_agencies=None, document_scope=False,
                       documents=()):
    """The UI selection is the outer boundary; inside it the question's named targets narrow the scope.

    A product or regulator narrows the selection only when the question itself
    names it, so a model's guessed product never replaces the selection. A named
    target outside the selection stops planning with a conflict.
    Registry/source names check identity only. They do not infer study roles,
    recruitment countries, analysis types or a clinical question's meaning.
    """
    out = deepcopy(interpretation)
    model = {key:list(out.get(key,[])) for key in ('products','jurisdictions')}
    allowed = {'products':list(products),'jurisdictions':list(agencies)}
    pinned = {'products':bool(selected_products) or document_scope,
              'jurisdictions':bool(selected_agencies) or document_scope}

    def explicit(key, value):
        if key == 'products':
            entry = sr.products().get(value,{})
            return named(question,[value,*entry.get('names',[]),*entry.get('ko_names',[])])
        # A regulator mention counts unless a proper noun contradicts it;
        # EU/US reference-product sourcing ("EU Stelara") is not a regulator request.
        return any(m.get('jurisdiction')==value and regulator_mention(question, m)
            for m in out.get('jurisdiction_mentions',[]))

    adjustments, conflicts, narrowed = [], [], {}
    for key in ('products','jurisdictions'):
        for value in [v for v in model[key] if v not in allowed[key]]:
            named_here = explicit(key, value)
            if named_here or not pinned[key]:
                conflicts.append({'field':key,'value':value,
                    'reason':'explicit_target_outside_request' if named_here else 'inferred_target_unavailable'})
        if pinned[key]:
            inside = [v for v in model[key] if v in allowed[key] and explicit(key, v)]
            effective = inside or allowed[key]
            if inside and inside != allowed[key]: narrowed[key] = inside
        else:
            effective = [v for v in model[key] if v in allowed[key]] or (allowed[key] if not model[key] else [])
        if effective != model[key]:
            adjustments.append({'field':key,'model':model[key],'effective':effective})
        out[key] = effective
    # Empty eligible scopes (e.g. excluded protocol) must remain empty, even
    # when the model selects a registry product or an agency.
    if not products or not agencies:
        conflicts = []
    out['request_scope'] = {'model':model,'allowed':allowed,'pinned':pinned,
        'adjustments':adjustments,'conflicts':conflicts,'narrowed_by_question':narrowed,
        'status':'conflict' if conflicts else 'empty' if not products or not agencies else 'applied'}
    if conflicts:
        out['scope_warnings'] = [*out.get('scope_warnings',[]),
            '질문의 제품·기관 해석과 선택한 검색 범위가 다릅니다. 검색 범위를 확인해 주세요.']
    elif narrowed:
        labels = {'EU':'EMA','US':'FDA','CA':'Health Canada'}
        named_scope = ' · '.join(', '.join(labels.get(v,v) for v in narrowed[k]) for k in ('products','jurisdictions') if k in narrowed)
        out['scope_warnings'] = [*out.get('scope_warnings',[]),
            f'질문에 적힌 대상({named_scope})으로 화면 선택 범위를 좁혀 검색했습니다.']
    elif adjustments:
        out['scope_warnings'] = [*out.get('scope_warnings',[]),
            '화면에서 선택한 제품·기관·문서 범위를 검색에 적용했습니다.']
    return out
