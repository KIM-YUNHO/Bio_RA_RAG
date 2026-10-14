"""Follow-up turns: carry the previous turn's named scope into a short follow-up question.

Rules decide what is inherited; the planner model only writes the standalone
sentence, and code checks it. A turn's answer still comes from the ordinary
single-question pipeline, so every answer stays citable on its own.
"""
import re

from rag import study_registry as registry
from rag.request_scope import named

VERSION = 'ra-followup-1-named-scope'
AGENCY_NAMES = {'EU': ['EMA', '유럽의약품청', '유럽 규제기관'],
                'US': ['FDA', '미국 식품의약국', '미국 규제기관'],
                'CA': ['Health Canada', 'HC', '헬스캐나다', '캐나다 보건부', '캐나다 규제기관']}
AGENCY_LABEL = {'EU': 'EMA', 'US': 'FDA', 'CA': 'Health Canada'}
RESET_PRODUCTS = re.compile(r'(?:전체|모든|다른|각)\s*(?:제품|바이오시밀러|상품)|(?:제품|상품)\s*(?:전체|모두|별로)')
RESET_AGENCIES = re.compile(r'(?:전체|모든|다른|각|세)\s*(?:규제)?기관|기관\s*(?:전체|모두|별로)')
SUMMARY_LIMIT = 600


def product_names(product_id):
    entry = registry.products().get(product_id, {})
    return [product_id, *[re.sub(r'\s*\(.*?\)', '', n) for n in entry.get('names', [])], *entry.get('ko_names', [])]


def named_products(text):
    return [p for p in registry.products() if named(text, product_names(p))]


def named_agencies(text):
    return [j for j, names in AGENCY_NAMES.items() if named(text, names)]


def answer_summary(answer_ko):
    text = (answer_ko or '').split('\n\n추가 확인이 필요한 요청')[0]
    text = re.sub(r'\s*\[(?:E\d+)(?:\s*[,·]\s*E\d+)*\]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:SUMMARY_LIMIT] + ('…' if len(text) > SUMMARY_LIMIT else '')


def context_from_report(report):
    """What a follow-up may use: the previous standalone question, its named scope and a short answer."""
    question = report.get('question') or ''
    results = report.get('results') or [{}]
    return {'question': question, 'products': named_products(question), 'jurisdictions': named_agencies(question),
            'answer_summary': answer_summary(results[0].get('answer_ko'))}


def resolve(question, context):
    """Which scope a follow-up inherits, and the rule that decided it."""
    own = {'products': named_products(question), 'jurisdictions': named_agencies(question)}
    if not context:
        return {'version': VERSION, 'turn_kind': 'first', 'own': own, 'inherited': {}, 'rules': ['first_turn']}
    inherited, rules = {}, []
    for key, reset in (('products', RESET_PRODUCTS), ('jurisdictions', RESET_AGENCIES)):
        if own[key]:
            rules.append(f'{key}_from_question')
        elif reset.search(question):
            rules.append(f'{key}_reset_by_question')
        elif context.get(key):
            inherited[key] = list(context[key]); rules.append(f'{key}_inherited')
        else:
            rules.append(f'{key}_none')
    return {'version': VERSION, 'turn_kind': 'follow_up', 'own': own, 'inherited': inherited, 'rules': rules}


def scope_prefix(inherited):
    agencies = [AGENCY_LABEL[j] for j in inherited.get('jurisdictions', [])]
    products = inherited.get('products', [])
    parts = []
    if agencies: parts.append(f"{'·'.join(agencies)} 문서에서")
    if products: parts.append(f"{'·'.join(products)}에 대해")
    return ' '.join(parts)


def check_standalone(candidate, question, resolution):
    """Accept the model's standalone question only within the inherited and named scope."""
    inherited = resolution['inherited']
    fallback = (scope_prefix(inherited) + ' ' + question).strip() if inherited else question
    if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > 2000:
        return fallback, 'fallback_invalid_standalone'
    candidate = candidate.strip()
    for key, finder in (('products', named_products), ('jurisdictions', named_agencies)):
        allowed = set(resolution['own'][key]) | set(inherited.get(key, []))
        if set(finder(candidate)) - allowed:
            return fallback, f'fallback_new_{key}_in_standalone'
    missing = {k: [v for v in vals if v not in (named_products if k == 'products' else named_agencies)(candidate)]
               for k, vals in inherited.items()}
    missing = {k: v for k, v in missing.items() if v}
    if missing:
        return (scope_prefix(missing) + ' ' + candidate).strip(), 'standalone_with_inherited_names_added'
    return candidate, 'model_standalone_accepted'
