"""Task-level intent: planner definitions, literal checks, synthesis guidance.

A question may carry several requests with different intents. The planner
splits them into tasks (1-4) and gives EACH task one intent plus a verbatim
quote of the original-question span that the task answers. Deterministic checks
then (1) record whether the quote occurs in the original question and (2) report
original request segments that no task quote covers (a request the plan dropped).
The intent itself is the planner's semantic reading; no keyword rule overrides it. Intent decides how a task's answer is
organized; it never adds sources, values, products or agencies, and it does not
tune retrieval.
"""
import re

from rag.request_scope import named

VERSION = 'ra-intent-5-planner-intent'
INTENTS = ['fact_lookup', 'compare_products', 'compare_sources', 'regulatory_reasoning', 'document_info', 'out_of_scope']

PLANNER_PROMPT = """
Scope: set in_scope=false only when the whole question is unrelated to the
registered products, their regulatory/clinical-trial documents or biosimilar
guidance. A question only about an active substance or product outside the
registry (e.g. another molecule's biosimilars), or about market, sales, price or
company business data, is out of scope. Then still return one task restating
the question. When only PART of an in-scope question is out of scope, keep
in_scope=true and give that part its own task with intent out_of_scope; it is
reported to the user, not searched.

Intents are per TASK. Split the question into one task per distinct request;
a question asking for a comparison AND an agency's reasoning gets two tasks.
Requests with the same intent and the same targets stay in one task. Each task
gets intent.request_quote: copy verbatim the shortest original-question span
that this task answers (exact characters, no paraphrase). Write task.request as
a self-contained request: restore the products, studies and sources that the
original question applies to it but omits in that clause (e.g. "FDA가 면역원성을
어떻게 평가했는지" -> "FDA가 SB17과 ABP654의 면역원성을 어떻게 평가했는지").
Choose each task's intent, deciding in this order:
1. compare_products: two or more products set against each other (차이, 비교,
   다른 점, 공통점, vs). Naming several products for one shared fact without
   asking how they differ is fact_lookup.
2. compare_sources: one product or topic across two or more sources: agencies
   (EMA/FDA/Health Canada) or an agency versus a trial protocol/SAP, asking how
   their documents, conclusions or planned-versus-assessed details differ.
3. regulatory_reasoning: what an agency concluded, weighed, questioned or
   commented on, why, what a guidance recommends or requires, or whether an
   approach is acceptable. "RA/기관의 코멘트·평가·판단·우려" belongs here.
4. document_info: about a document itself (title, date, version, status,
   scope, which documents exist), not its clinical content.
5. fact_lookup: specific facts or values (dose, route, design, population,
   endpoints, results, planned protocol details, approval status) without
   comparison or reasoning.
6. out_of_scope: a part of the question outside the registry and its documents
   (market share, sales, prices, unregistered molecules).
The intent describes the request, never the expected answer.
Example: "SB17과 ABP654의 PK 시험 설계를 비교하고, FDA가 면역원성을 어떻게
평가했는지 알려줘" -> T1 compare_products quote "SB17과 ABP654의 PK 시험 설계를
비교하고"; T2 regulatory_reasoning quote "FDA가 면역원성을 어떻게 평가했는지".
"""


GUIDANCE = {
    'compare_products': 'Organize this task by comparison axis. For each axis state each product\'s own '
        'sourced fact with its own citation, then the difference. If one product\'s sources lack an axis, '
        'say so for that product as a gap; never infer sameness or difference from missing evidence.',
    'compare_sources': 'Organize this task by source (agency document or trial protocol/SAP). For each point state '
        'each source\'s own statement with its own citation, then the difference. A protocol/SAP states the '
        'sponsor\'s plan, not actual conduct or an agency conclusion. A point absent from one source is a gap '
        'for that source, not a disagreement.',
    'regulatory_reasoning': 'Separate the regulator\'s own assessment, reasoning or recommendation from the '
        'applicant\'s data or proposal, and cite the regulator\'s text for each conclusion.',
    'document_info': 'Answer from document identity and printed status/scope passages; do not summarize '
        'clinical content that was not asked.',
    'fact_lookup': 'Answer the requested facts directly with their conditions; add no background. A trial '
        'protocol/SAP gives planned details only; label them as planned.',
}


def segments(question):
    """Original request segments, split at sentence ends and clause-final connectives."""
    parts = re.split(r'(?<=[.?!。])\s+|(?<=하고)\s*,?\s+|(?<=하며)\s*,?\s+|(?<=고)\s*,\s*|\s*;\s*', (question or '').strip())
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def check_task(question, task):
    """Check one task's intent; returns (final intent, record)."""
    from rag.evidence import contains_quote
    chosen = task.get('intent') or {}
    intent, quote = chosen.get('intent'), chosen.get('request_quote', '')
    record = {'model': intent, 'final': intent, 'quote': quote, 'quote_verified': False, 'rule': None}
    grounded = bool(quote.strip()) and contains_quote(question, quote)
    record['quote_verified'] = grounded
    # An unverified quote cannot narrow the check to a span: fall back to the task request.
    span = quote if grounded else task.get('request', '')
    # The planner's intent is its semantic reading of the request; no keyword rule overrides it.
    # Proper-noun fact only: two different names of ONE registry product are not a product comparison.
    if record['final'] == 'compare_products':
        from rag import study_registry
        hits = {(pid, name.lower()) for pid, meta in study_registry.products().items()
                for name in [pid, *meta.get('names', []), *meta.get('ko_names', [])] if named(span, [name])}
        if len({pid for pid, _ in hits}) == 1 and len(hits) >= 2:
            record.update(final='fact_lookup', rule='same_product_aliases_are_not_a_comparison')
    return record['final'], record


def check(question, value):
    """Apply task checks; split out-of-scope parts; derive question-level intent and uncovered segments.

    Out-of-scope tasks of an in-scope question move to value['out_of_scope_tasks']: they are
    reported, never searched. If every task is out of scope the whole question is.
    """
    for task in value['tasks']:
        final, record = check_task(question, task)
        task['intent'] = {**task['intent'], 'intent': final}
        task['intent_check'] = record
    all_tasks = value['tasks']
    outside = [t for t in all_tasks if t['intent']['intent'] == 'out_of_scope']
    if outside and len(outside) == len(all_tasks):
        value['in_scope'] = False
    elif outside and value.get('in_scope', True):
        value['out_of_scope_tasks'] = outside
        value['tasks'] = [t for t in all_tasks if t['intent']['intent'] != 'out_of_scope']
    finals = [t['intent']['intent'] for t in value['tasks']]
    quotes = [t['intent_check']['quote'] for t in all_tasks if t['intent_check']['quote_verified']]
    uncovered = [s for s in segments(question) if not any(q in s or s in q or _overlap(q, s) for q in quotes)]
    value['intents'] = finals
    value['intent'] = ('out_of_scope' if not value.get('in_scope', True) else
                       finals[0] if len(set(finals)) == 1 else 'multi_intent')
    value['intent_check'] = {'version': VERSION, 'tasks': [t['intent_check'] for t in all_tasks],
                             'uncovered_request_segments': uncovered}
    return value


def _overlap(quote, segment):
    """A quote covers a segment when they share a long literal run (>= 60% of the shorter)."""
    a, b = re.sub(r'\s+', '', quote), re.sub(r'\s+', '', segment)
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    need = max(4, int(len(short) * .6))
    return any(short[i:i + need] in long_ for i in range(0, len(short) - need + 1))
