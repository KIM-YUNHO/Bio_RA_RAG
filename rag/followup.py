"""Follow-up turns: offer the previous turn's scope to the planner for a short follow-up question.

The previous turn's products and source agencies (as its plan settled them) are offered
to the planner, which decides from the follow-up whether to keep, replace or widen them
and writes the standalone question. Code checks proper nouns only: no product outside the
registry names of the follow-up and the previous scope, and (in ra_tasks) no agency that
is neither previous scope nor named in the user's follow-up. A turn's answer still comes
from the ordinary single-question pipeline, so every answer stays citable on its own.
"""
import re

from rag import study_registry as registry
from rag.request_scope import named

VERSION = 'ra-followup-2-planner-scope'
# Display labels for the fallback standalone sentence (output wording, not recognition).
AGENCY_LABEL = {'EU': 'EMA', 'US': 'FDA', 'CA': 'Health Canada'}
SUMMARY_LIMIT = 600


def product_names(product_id):
    entry = registry.products().get(product_id, {})
    return [product_id, *[re.sub(r'\s*\(.*?\)', '', n) for n in entry.get('names', [])], *entry.get('ko_names', [])]


def named_products(text):
    return [p for p in registry.products() if named(text, product_names(p))]


def answer_summary(answer_ko):
    text = re.split(r'\n\n(?:추가 확인이 필요한 요청|인용으로 완전히 확인하지 못한 요청|확인하지 못한 항목)', answer_ko or '')[0]
    text = re.sub(r'\s*\[(?:E\d+)(?:\s*[,·]\s*E\d+)*\]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:SUMMARY_LIMIT] + ('…' if len(text) > SUMMARY_LIMIT else '')


def context_from_report(report):
    """What a follow-up may use: the previous standalone question, the scope its plan settled and a short answer."""
    from rag.ra_models import REGULATORS
    question = report.get('question') or ''
    results = report.get('results') or [{}]
    understanding = report.get('understanding') or {}
    return {'question': question, 'products': named_products(question),
            'jurisdictions': [j for j in understanding.get('jurisdictions', []) if j in REGULATORS],
            'answer_summary': answer_summary(results[0].get('answer_ko'))}


def resolve(question, context):
    """The previous scope offered to the planner; whether to keep it is the planner's reading."""
    own = {'products': named_products(question)}
    if not context:
        return {'version': VERSION, 'turn_kind': 'first', 'own': own, 'inherited': {}, 'rules': ['first_turn']}
    offered = {k: list(context[k]) for k in ('products', 'jurisdictions') if context.get(k)}
    return {'version': VERSION, 'turn_kind': 'follow_up', 'own': own, 'inherited': offered,
            'rules': ['previous_scope_offered_to_planner']}


def scope_prefix(inherited):
    agencies = [AGENCY_LABEL[j] for j in inherited.get('jurisdictions', [])]
    products = inherited.get('products', [])
    parts = []
    if agencies: parts.append(f"{'·'.join(agencies)} 문서에서")
    if products: parts.append(f"{'·'.join(products)}에 대해")
    return ' '.join(parts)


def check_standalone(candidate, question, resolution):
    """Accept the model's standalone question unless it names a product outside the offered and named scope."""
    inherited = resolution['inherited']
    fallback = (scope_prefix(inherited) + ' ' + question).strip() if inherited else question
    if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > 2000:
        return fallback, 'fallback_invalid_standalone'
    candidate = candidate.strip()
    allowed = set(resolution['own']['products']) | set(inherited.get('products', []))
    if set(named_products(candidate)) - allowed:
        return fallback, 'fallback_new_products_in_standalone'
    return candidate, 'model_standalone_accepted'
