"""Opt-in single-call query adapter. No default provider or live wiring.

The original question and contract remain separate from the English search text.
Offline callers use saved JSON or a fake injected client. Production/API use is
a subsequent evaluation step, not enabled by importing this module.
"""
from copy import deepcopy
from itertools import product
import json
import re
from rag.chunk_schema import obj, validate
from rag.contract_terms import atoms
from rag.request_contract import PROPOSAL_SCHEMA, PROMPT, ground, translation_omissions

VERSION = "contract-query-8-compact-typed-roles"
DIMENSIONS = ["endpoint", "statistic", "unit", "dose", "study", "comparison", "weight", "analysis_set",
              "covariate", "analysis_model", "analysis_timing", "analysis_role", "timepoint", "attribute", "population", "route", "literal_condition"]
FIELD_VALUES = {"statistic": ["GMR", "CI90", "CI95", "CI99", "difference", "mean", "median"],
                "endpoint": ["AUCinf", "AUClast", "Cmax", "tmax", "CL/F", "Vz/F", "V2/F", "V/F", "PASI"],
                "attribute": ["unit", "phase", "randomization", "blinding", "arms", "assay", "margin"]}


def field_schema(question=None):
    field = PROPOSAL_SCHEMA["properties"]["requests"]["items"]["properties"]["field"]
    variants = []
    known = atoms(question) if question is not None else []
    statistics = {a["value"] for a in known if a["dimension"] == "statistic"}
    attributes = {a["value"] for a in known if a["dimension"] == "attribute"}
    for dimension, values in FIELD_VALUES.items():
        if dimension == "statistic" and statistics:
            values = [v for v in values if v in statistics]
        if dimension == "endpoint" and statistics:
            continue  # Requested PK statistics have endpoints as conditions.
        if dimension == "attribute" and attributes:
            values = [v for v in values if v in attributes]
        variant = deepcopy(field); variant["properties"]["dimension"] = {"type": "string", "enum": [dimension]}
        variant["properties"]["value"] = {"type": "string", "enum": values}; variants.append(variant)
    other = deepcopy(field); other["properties"]["dimension"] = {"type": "string", "enum": ["literal_request"]}; variants.append(other)
    return {"anyOf": variants}


def condition_schema(question=None):
    base = PROPOSAL_SCHEMA["properties"]["requests"]["items"]["properties"]["conditions"]["items"]
    variants = []
    for operators, minimum, maximum in [(["eq", "not_eq"], 1, 1),
                                        (["ordered_pair", "unordered_pair"], 2, 2), (["range"], 2, 2), (["one_of"], 1, None)]:
        variant = deepcopy(base)
        dimensions = ["comparison"] if "ordered_pair" in operators else [d for d in DIMENSIONS if d != "comparison"]
        if question is not None and any(a["dimension"] == "attribute" and a["value"] == "unit" for a in atoms(question)) and not any(a["dimension"] == "unit" for a in atoms(question)):
            dimensions = [d for d in dimensions if d != "unit"]
        variant["properties"]["dimension"] = {"type": "string", "enum": dimensions}
        variant["properties"]["operator"] = {"type": "string", "enum": operators}
        variant["properties"]["values"]["minItems"] = minimum
        if maximum is not None:
            variant["properties"]["values"]["maxItems"] = maximum
        variants.append(variant)
    return {"anyOf": variants}


def schema(question=None):
    from rag.understand_query import SCHEMA
    if question is not None:
        from rag.question_anchors import schema as anchor_schema
        return anchor_schema(question, SCHEMA['schema'])
    requirements = obj({"request_groups": {"type": "array", "minItems": 1, "items": obj({
        "id": {"type": "string"}, "fields": {"type": "array", "minItems": 1, "items": field_schema(question)},
        "conditions": {"type": "array", "items": condition_schema(question)},
        "axes": {"type": "array", "items": obj({"options": {"type": "array", "minItems": 1,
                                                                   "items": condition_schema(question)}})}})},
        "non_request_mentions": deepcopy(PROPOSAL_SCHEMA["properties"]["non_request_mentions"])})
    return {"name": "query_understanding_with_contract", "schema": obj({
        "understanding": deepcopy(SCHEMA["schema"]), "requirements": requirements})}


def expand_groups(proposal):
    """Expand only declared question axes; never derive demands from documents."""
    validate(proposal, schema()["schema"]["properties"]["requirements"], "request_groups")
    requests = []; ids = set()
    for group in proposal["request_groups"]:
        gid = group["id"]
        if not gid.strip() or gid in ids:
            raise ValueError("group IDs must be nonempty and unique")
        ids.add(gid); dimensions = set()
        for dimension in ('endpoint','dose'):
            exclusive = {v['value'] for c in group['conditions'] if c['dimension']==dimension and c['operator']=='eq' for v in c['values']}
            if len(exclusive)>1:
                raise ValueError('distinct '+dimension+' values require an axis or correlated groups, not shared AND eq conditions')
        comparisons = {tuple(v['value'] for v in c['values']) for c in group['conditions'] if c['dimension']=='comparison'}
        if len(comparisons)>1:
            raise ValueError('distinct comparison pairs require an axis or separate groups, not shared AND conditions')
        for axis in group["axes"]:
            dims = {c["dimension"] for c in axis["options"]}
            if len(dims) != 1 or next(iter(dims)) in dimensions:
                raise ValueError("request axis must have one distinct dimension")
            dimensions.update(dims)
            if any(c["dimension"] in dimensions for c in group["conditions"]):
                raise ValueError("shared condition conflicts with a requested axis")
        choices = product(*(a["options"] for a in group["axes"]))
        for combination_no, combination in enumerate(choices):
            for field_no, field in enumerate(group["fields"]):
                requests.append({"id": f"{gid}:{combination_no+1}:{field_no+1}", "field": deepcopy(field),
                                 "conditions": deepcopy([*group["conditions"], *combination])})
                if len(requests) > 64:
                    raise ValueError("question contract exceeds 64 expanded requests")
    result = {"requests": requests, "non_request_mentions": deepcopy(proposal["non_request_mentions"])}
    validate(result, PROPOSAL_SCHEMA, "expanded_request_proposal")
    # The dependency-free validator does not implement nested anyOf.
    for r in requests:
        for c in r["conditions"]:
            n = len(c["values"])
            if c["dimension"] not in DIMENSIONS or c["operator"] not in ("eq", "not_eq", "one_of", "ordered_pair", "unordered_pair", "range"):
                raise ValueError("unsupported condition dimension or operator")
            if c["operator"] in ("eq", "not_eq") and n != 1 or c["operator"] in ("ordered_pair", "unordered_pair", "range") and n != 2:
                raise ValueError("operator/value arity mismatch")
    return result


def resolve(question, response):
    from rag.understand_query import resolve as resolve_scope
    proposal = response.get("requirements", {})
    anchor_id_format = bool(proposal.get('request_groups') and 'field_ids' in proposal['request_groups'][0])
    if anchor_id_format:
        from rag.question_anchors import expand
        validate(response, schema(question)['schema'], 'question_anchor_response')
        proposal = expand(question, proposal)
        response = {**response, 'requirements':proposal}
    grouped = "request_groups" in proposal
    if grouped:
        validate(response, schema()["schema"], "query_contract_response")
        proposal = expand_groups(proposal)
    else:
        # Historical atomic proposals stay readable for saved-response replay.
        from rag.understand_query import SCHEMA
        validate(response, obj({"understanding": SCHEMA["schema"], "requirements": PROPOSAL_SCHEMA}), "query_contract_response")
    # The dependency-free general validator does not implement anyOf; enforce
    # the field variants here as well as in the provider's strict schema.
    for request in proposal["requests"]:
        dimension = request["field"]["dimension"]; value = request["field"]["value"]
        if dimension not in FIELD_VALUES and dimension != "literal_request":
            raise ValueError("unsupported request field dimension")
        if dimension in FIELD_VALUES and value not in FIELD_VALUES[dimension]:
            raise ValueError("unsupported canonical request field value")
    understanding = resolve_scope(question, response["understanding"])
    from rag.contract_relations import grounded_term, ranges, arm
    from rag.request_contract import locate
    normalizations = []; grounded_weight_terms = set()
    # Range values are numerical bounds; units remain in their original anchors.
    # Strip a printed kg only if both anchors belong to the SAME explicit range.
    proposal = deepcopy(proposal)
    for request in proposal["requests"]:
        for c in request["conditions"]:
            if c["dimension"] != "weight" or c["operator"] != "range":
                continue
            parsed = [re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:kg)?\s*",v["value"],re.I) for v in c["values"]]
            if not all(parsed):
                continue
            numeric = [m[1] for m in parsed]; anchors = [locate(question,v["anchor"]) for v in c["values"]]
            if any(r["values"] == numeric and all(r["start"] <= a["start"] < a["end"] <= r["end"] for a in anchors) for r in ranges(question)):
                for v,number in zip(c["values"],numeric):
                    grounded_weight_terms.add((number,v["anchor"]["quote"]))
                    if v["value"] != number:
                        normalizations.append({"request_id":request["id"],"original":v["value"],"normalized":number,"basis":"same_explicit_question_kg_range"})
                        v["value"] = number
    from rag.question_anchors import catalog as anchor_catalog
    question_anchor_terms={(a['dimension'],a['value'],a['quote']) for a in anchor_catalog(question).values()}
    contract = ground(question, proposal, normalizer=lambda d,v,q: (d == "weight" and (v,q) in grounded_weight_terms)
                      or (anchor_id_format and (d,v,q) in question_anchor_terms) or grounded_term(d,v,q))
    for request in contract["requests"]:
        for condition in request["conditions"]:
            if condition["dimension"] == "weight" and condition["operator"] == "range":
                valid = any(r["values"] == [v["value"] for v in condition["values"]] and all(
                    r["start"] <= v["anchor"]["start"] < v["anchor"]["end"] <= r["end"] for v in condition["values"])
                    for r in ranges(question))
                condition["unit"] = "kg" if valid else None
                if not valid:
                    request["normalization_status"] = "review"
                    contract["warnings"].append({"reason": "weight_range_unit_not_grounded", "request_id": request["id"]})
                    contract["status"] = "normalization_review"
    references = list(re.finditer(r"\b(?:EU|US)[-\s]*(?:(?:approved|licensed)[-\s]+)?Stelara\b|(?:유럽|미국)\s*(?:(?:허가|승인)\s*)?(?:대조약|스텔라라)",question,re.I))
    comparison_terms = [(v,c) for r in contract["requests"] for c in r["conditions"] if c["dimension"] == "comparison" for v in c["values"]]
    for mention in references:
        value = arm(mention.group())
        coordinated = [a for a in anchor_catalog(question).values() if a.get('alias_basis')=='explicit_coordinated_Stelara_noun'
                       and a['value']==value and a['alias_source_range'][0]<=mention.start() and mention.end()<=a['alias_source_range'][1]]
        literal_match = any(arm(v["value"]) == value and v["anchor"]["start"] <= mention.start() and mention.end() <= v["anchor"]["end"] for v,_ in comparison_terms)
        alias_match = anchor_id_format and any(v['value']==value and any(v['anchor']['start']==a['start'] and v['anchor']['end']==a['end'] for a in coordinated) for v,_ in comparison_terms)
        if not literal_match and not alias_match:
            contract["missing_known_mentions"].append({"dimension":"comparison","value":value,"quote":mention.group(),"start":mention.start(),"end":mention.end()})
    for v,c in comparison_terms:
        if c["operator"] not in ("ordered_pair","unordered_pair"):
            v["normalization_status"] = "review"
            contract["warnings"].append({"reason":"comparison_requires_explicit_pair","dimension":"comparison","value":v["value"]})
    # Preserve incomplete independently from normalization problems; one must
    # not overwrite the other and make missing question conditions disappear.
    contract["status"] = "incomplete" if contract["missing_known_mentions"] else "normalization_review" if contract["warnings"] else "anchored_candidate"
    omissions = translation_omissions(question, understanding["english_query"])
    # Preserve grounded requested role names even if the English translation drops them.
    required_terms = [a["value"] for a in atoms(question)]
    return {"version": VERSION, "original_question": question, "understanding": understanding, "requirements": contract,
            "model_requirement_proposal": deepcopy(response["requirements"]), "groups_expanded": grouped,
            "normalizations": normalizations,
            "translation_omissions": omissions, "search_terms": " ".join(dict.fromkeys([
                understanding["english_query"], *understanding["keywords"], *required_terms])),
            "requires_review": contract["status"] != "anchored_candidate" or bool(omissions),
            "semantic_approval": False}


def messages(question):
    from rag.understand_query import SYSTEM, protected_terms, glossary_hits, load_glossary
    hits = glossary_hits(question, load_glossary())
    glossary = "\n".join(f"{h['ko']} = {h['en_canonical']} ({h['en_variants']})" for h in hits)
    conventions = '''Return compact request_groups. Each group has fields,
shared AND conditions, and axes of explicitly requested options. Code creates
one request per field and every Cartesian combination of the group's axes.
Separate groups express correlated requests (e.g. dose A median only, dose B mean
only); never expand combinations that the question does not request.
For PK results use field dimension statistic, values GMR, CI90, mean, median;
requested endpoints are separate eq options of one axis. Two endpoints and two
statistics mean four mandatory requests. Requested doses or comparisons are
further axes. Every option has its exact original anchors. one_of means that any
alternative is acceptable, NOT that all options must be answered. eq/not_eq require
exactly 1 value; pairs/ranges exactly 2. For requested units use field attribute/unit plus
endpoint and dose conditions. Known dimensions: endpoint, statistic, unit, dose,
study, comparison, weight, analysis_set, covariate, analysis_model, analysis_timing,
attribute, population, route. Study values use existing registry keys; comparisons
use product codes or EU-Stelara/US-Stelara with their original Korean anchors.
Protein adjustment uses covariate/protein_adjusted; ADA negativity uses
analysis_set/ADA_negative; body weight as covariate uses covariate/body_weight;
ANCOVA uses analysis_model/ANCOVA; post-hoc uses
analysis_timing/post_hoc. A body-weight interval uses weight/range, two numerical
bounds anchored separately (kg is part of the original condition). AUCinf and Cmax
are independent endpoints. Do not merge their values or stats. Design attributes
use phase, randomization, blinding, arms; assay queries use attribute/assay.
Complete shape example (question only, no document answer):
'피하 투여 후 CL/F와 Vz/F의 평균' becomes
{"request_groups":[{"id":"pk","fields":[{"dimension":"statistic","value":"mean","anchor":{"quote":"평균","occurrence":0}}],"conditions":[{"dimension":"route","operator":"eq","values":[{"value":"SC","anchor":{"quote":"피하","occurrence":0}}]}],"axes":[{"options":[{"dimension":"endpoint","operator":"eq","values":[{"value":"CL/F","anchor":{"quote":"CL/F","occurrence":0}}]},{"dimension":"endpoint","operator":"eq","values":[{"value":"Vz/F","anchor":{"quote":"Vz/F","occurrence":0}}]}]}]}],"non_request_mentions":[]}.
An axis lists ALL requested options of ONE dimension. A dimension present in an
axis MUST NOT also occur in shared conditions. An endpoint in conditions AND a
different endpoint in an axis is contradictory; put both in that axis instead.
Never use Cmax or AUCinf as a dimension name. Anchor the statistic to its statistic
word, not to the endpoint. All requested study, population, route and AND analysis
conditions belong in the group's shared conditions. Requested comparisons belong
in a comparison axis. Code copies these to every affected scalar request.
Refer to registry study IDs only in
study conditions; other conditions never replace the endpoint/comparison.
Non-request mentions retain context/excluded roles. Any unknown normalization
uses the exact quoted wording as its value, rather than inventing a paraphrase.'''
    from rag.question_anchors import catalog as anchor_catalog, RULES as anchor_rules
    entries=anchor_catalog(question)
    compact=[{'id':i,'dimension':a['dimension'],'value':a['value'],'quote':a['quote'],
              'occurrence':a['anchor']['occurrence']} for i,a in entries.items()]
    families={}
    has_statistics=any(a['dimension']=='statistic' for a in entries.values())
    for i,a in entries.items():
        if a['dimension'] not in ('statistic','attribute','endpoint'):continue
        if a['dimension']=='endpoint' and has_statistics:continue
        kind='criterion' if a['dimension']=='attribute' and a['value']=='margin' else a['dimension']
        families.setdefault(kind,[]).append(i)
    return [{"role": "system", "content": SYSTEM + "\n\n" + anchor_rules +
                 "\nReturn understanding and requirements as separate objects in the supplied schema."},
                {"role": "user", "content": question + "\nCopy exactly: " + ", ".join(protected_terms(question)) + "\nGlossary:\n" + glossary
                 + '\nOriginal-question anchor inventory (IDs can be reused; no document facts): '
                 + json.dumps(compact,ensure_ascii=False,separators=(',',':'))
                 + '\nField families: use separate scoped groups for every requested family; explain only actual context/exclusions: '
                 + json.dumps(families,ensure_ascii=False,separators=(',',':'))}]


def interpret(question, *, chat_client):
    if chat_client is None:
        raise ValueError("an explicit injected client is required; no default model")
    response = chat_client(messages(question), json_schema=schema(question))
    return {**resolve(question, json.loads(response["text"])), "model_calls": 1,
            "model": response.get("model_reported"), "prompt_tokens": response.get("prompt_tokens"),
            "completion_tokens": response.get("completion_tokens")}


def understand(question, glossary=None, provider=None, chat_client=None):
    """One injected question-only call for the opt-in runtime pipeline.

    The model does not see retrieval context or gold. Incomplete/unknown roles
    remain in the contract and cannot become a complete answer downstream.
    No provider fallback or automatic query-repair call is made here.
    """
    from rag.literal_requests import compile_requests,automatic_roles_complete
    compiled=compile_requests(question)
    method='model_original_question_groups'
    if compiled is not None and automatic_roles_complete(question):
        # Unambiguous enumerated PK roles already have a question-only grammar.
        # Keep the model for intent/scope/search, not Cartesian field arithmetic.
        # Do not invoke the legacy interpreter's optional second model attempt.
        from rag.understand_query import SYSTEM,SCHEMA,resolve as resolve_scope,protected_terms,glossary_hits,load_glossary
        from rag.question_anchors import catalog
        if chat_client is None:raise ValueError('an explicit injected client is required')
        hits=glossary_hits(question,glossary if glossary is not None else load_glossary())
        msgs=[{'role':'system','content':SYSTEM}, {'role':'user','content':
            'Question: '+question+'\nCopy exactly: '+', '.join(protected_terms(question))+'\nGlossary:\n'+
            '\n'.join(h['ko']+' = '+h['en_canonical']+' ('+h['en_variants']+')' for h in hits)}]
        reply=chat_client(msgs,json_schema=SCHEMA,provider=provider)
        raw=json.loads(reply['text']);validate(raw,SCHEMA['schema'],'bounded_query_understanding')
        u=resolve_scope(question,raw)
        roles={(a['dimension'],a['value'],a['quote']) for a in catalog(question).values()}
        contract=ground(question,{'requests':compiled['requests'],'non_request_mentions':[]},
                        normalizer=lambda d,v,q:(d,v,q) in roles)
        omissions=translation_omissions(question,u['english_query'])
        expansion=[h['en_canonical']+' '+h['en_variants'].replace(';',' ') for h in hits]
        result={'understanding':u,'requirements':contract,'model_requirement_proposal':None,
            'search_terms':' '.join([u['english_query'],*u['keywords'],*expansion,
                *[a['quote'] for a in catalog(question).values()]]),
            'requires_review':contract['status']!='anchored_candidate' or bool(omissions),
            'translation_omissions':omissions,'model':reply.get('model_reported'),
            'prompt_tokens':reply.get('prompt_tokens'),'completion_tokens':reply.get('completion_tokens')}
        method='bounded_original_question_grammar'
    else:result=interpret(question,chat_client=chat_client)
    from rag.understand_query import protected_terms
    return {**result['understanding'],'source_question':question,'source':question,
            'search_terms':result['search_terms'],'protected':protected_terms(question),
            'request_contract':deepcopy(result['requirements']),
            'request_contract_version':VERSION,'request_contract_model_proposal':result['model_requirement_proposal'],
            'request_contract_method':method,
            'request_contract_requires_review':result['requires_review'],
            'translation_omissions':result['translation_omissions'],'attempts':1,
            'model':result.get('model'),'prompt_tokens':result.get('prompt_tokens') or 0,
            'completion_tokens':result.get('completion_tokens') or 0,
            'prompt_version':VERSION,'semantic_approval':False}


def repair(question, prior_response, *, chat_client):
    """One opt-in question-only repair. No source text, gold or recursive calls."""
    if chat_client is None:
        raise ValueError("an explicit repair client is required")
    try:
        prior = resolve(question,prior_response)
        feedback = {"status":prior["requirements"]["status"],"missing":prior["requirements"]["missing_known_mentions"],
                    "warnings":prior["requirements"]["warnings"]}
    except ValueError as exc:
        feedback = {"invalid_proposal":str(exc)}
    feedback["original_known_roles"] = atoms(question)
    from rag.contract_relations import ranges
    feedback["original_explicit_weight_ranges"] = ranges(question)
    msgs = messages(question)
    msgs.append({"role":"user","content":"Repair the prior question interpretation using ONLY the original question. "
        "The checker feedback contains question roles, not document facts. Include every requested condition in each affected group. "
        "Do not invent arithmetic mean when 평균 is inside 기하평균비. Requested units are attribute/unit fields, not a condition unit/unit. "
        "Comparison options require both numerator/denominator (ordered_pair) or both named arms (unordered_pair). "
        "Keep range bounds numeric while retaining kg in their original quotations. Do not silently omit unknown roles.\n"
        +json.dumps({"prior":prior_response,"feedback":feedback},ensure_ascii=False)})
    reply = chat_client(msgs,json_schema=schema(question))
    return {**resolve(question,json.loads(reply["text"])),"model_calls":1,"repair_calls":1,"recursive_repair":False,
            "model":reply.get("model_reported"),"prompt_tokens":reply.get("prompt_tokens"),"completion_tokens":reply.get("completion_tokens")}


def interpret_checked(question, *, chat_client, allow_repair=False):
    """Opt-in bounded query workflow; invalid contracts never proceed as ready.

    One initial call; at most one explicitly enabled logical repair. Provider
    failures propagate without retries. Readiness is structural, not clinical
    semantic approval. This function is not wired into the live service.
    """
    if chat_client is None:
        raise ValueError("an explicit injected client is required")
    reply=chat_client(messages(question),json_schema=schema(question))
    raw=None; interpretation=None; error=None
    try:
        raw=json.loads(reply["text"]);interpretation=resolve(question,raw)
    except ValueError as exc:
        error=str(exc)
    calls=1
    needs_repair=error is not None or interpretation["requirements"]["status"]!="anchored_candidate"
    if allow_repair and needs_repair and isinstance(raw,dict):
        calls+=1
        try:
            interpretation=repair(question,raw,chat_client=chat_client);error=None
        except ValueError as exc:
            error=str(exc)
    ready=error is None and interpretation is not None and interpretation["requirements"]["status"]=="anchored_candidate"
    return {"state":"contract_candidate_ready" if ready else "question_review_required",
            "original_question":question,"interpretation":interpretation,"error":error,"model_calls":calls,
            "repair_allowed":allow_repair,"repair_limit":1,"semantic_approval":False}
