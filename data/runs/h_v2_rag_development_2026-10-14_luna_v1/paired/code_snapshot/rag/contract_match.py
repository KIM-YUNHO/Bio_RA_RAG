"""Tri-state matching of question obligations against locally linked roles.

A structural match never certifies semantic truth. Unsupported conditions remain
unknown; a value from another endpoint can be a clear mismatch. No answer editing.
"""
from rag.contract_terms import key

VERSION = "contract-match-7-printed-prose-bundles"


def condition(fact, dimension, operator, values):
    if dimension in ("comparison", "weight") and operator in ("ordered_pair", "unordered_pair", "range"):
        from rag.contract_relations import relation_condition
        return relation_condition(fact, dimension, operator, values)
    if fact is None or fact["relationship_status"] != "structural_candidate":
        return {"dimension": dimension, "status": "unknown", "reason": "unresolved_source_relationship"}
    if dimension == "statistic" and fact.get("value_binding_status") == "subcell_unresolved":
        return {"dimension": dimension, "status": "unknown", "reason": "subcell_statistic_requires_binding"}
    if dimension == "attribute" and values == ["unit"]:
        observed = {key(a["value"]) for a in fact["observations"] if a["dimension"] == "unit"}
        return {"dimension": dimension, "status": "match" if observed else "unknown",
                "reason": "unit_is_connected_header_metadata" if observed else "unit_not_locally_resolved", "observed": sorted(observed)}
    observed = {key(a["value"]) for a in fact["observations"] if a["dimension"] == dimension}
    expected = [key(v) for v in values]
    if dimension=='attribute' and operator=='eq' and values==['margin'] and observed.intersection(expected):
        from rag.margin_scope import predicate
        if fact.get('value_binding_status')=='whole_source_text' and not predicate(fact.get('source_quote','')):
            return {'dimension':dimension,'status':'unknown','reason':'criterion_requires_whole_quantified_predicate'}
    if dimension=='statistic' and operator=='eq' and len(expected)==1 and expected[0] in ('ci90','ci95','ci99') and \
            fact.get('value_binding_status')=='whole_cell' and not fact.get('compound_statistic_binding'):
        import re
        number=r'[+−–-]?\d+(?:\.\d+)?'
        bounds=bool(re.fullmatch(r'\s*[([]?\s*'+number+r'\s*[,;]\s*'+number+r'\s*[)\]]?\s*',fact['cell']['value']))
        levels=observed.intersection({'ci90','ci95','ci99'})
        if levels and not bounds:
            return {'dimension':dimension,'status':'unknown','reason':'ci_requires_complete_bound_pair_cell'}
        if levels==set(expected):
            return {'dimension':dimension,'operator':operator,'expected':values,'observed':sorted(observed),
                    'status':'match','reason':'whole_bound_pair_and_own_explicit_confidence_level'}
    bundle=fact.get('compound_statistic_binding')
    if dimension=='statistic' and operator=='eq' and fact.get('value_binding_status')=='whole_source_text' and bundle and \
            observed=={key(v) for v in bundle['roles']} and set(expected)<=observed:
        return {'dimension':dimension,'operator':operator,'expected':values,'observed':sorted(observed),
            'status':'match','reason':'whole_prose_single_printed_measure_and_ci_bundle','basis':bundle}
    if dimension=='statistic' and operator=='eq' and fact.get('value_binding_status')=='whole_cell' and bundle and \
            observed=={key(v) for v in bundle['roles']}=={'gmr','ci90'} and set(expected)<=observed:
        return {'dimension':dimension,'operator':operator,'expected':values,'observed':sorted(observed),
            'status':'match','reason':'whole_cell_explicit_estimate_ci_bundle','basis':bundle}
    if not observed:
        status, reason = "unknown", "role_not_locally_resolved"
    elif operator in ("ordered_pair", "range"):
        # A set cannot prove ordering or numerical range membership.
        status, reason = "unknown", "operator_requires_explicit_relationship"
    elif dimension == "analysis_timing" and operator == "eq" and observed == {"ad_hoc"} and expected == ["post_hoc"]:
        status, reason = "unknown", "ad_hoc_does_not_certify_post_hoc_timing"
    elif operator in ("eq", "one_of"):
        if observed.intersection(expected):
            status, reason = ("unknown", "conflicting_local_roles") if observed - set(expected) and operator == "eq" and dimension not in ("attribute", "covariate") else ("match", "own_context_role")
        else:
            status, reason = "mismatch", "different_local_role"
    elif operator == "not_eq":
        # Not seeing a label does not prove an exclusion; positive observation does.
        status, reason = ("mismatch", "excluded_local_role") if observed.intersection(expected) else ("unknown", "negative_requires_explicit_evidence")
    else:
        status, reason = "unknown", "unsupported_operator"
    return {"dimension": dimension, "operator": operator, "expected": values, "observed": sorted(observed), "status": status, "reason": reason}


def match_request(contract, request, fact):
    field = request["field"]
    checks = [{"dimension":field["dimension"],"status":"unknown","reason":"question_field_role_unverified"}]
    if field.get("normalization_status", "grounded") == "grounded":
        checks = [condition(fact, field["dimension"], "eq", [field["value"]])]
    for c in request["conditions"]:
        if any(v.get("normalization_status","grounded") != "grounded" for v in c["values"]):
            checks.append({"dimension":c["dimension"],"status":"unknown","reason":"question_condition_normalization_unverified"})
        elif c["dimension"] == "weight" and c["operator"] == "range" and c.get("unit") != "kg":
            checks.append({"dimension":"weight","status":"unknown","reason":"question_range_unit_not_grounded"})
        else:
            checks.append(condition(fact,c["dimension"],c["operator"],[v["value"] for v in c["values"]]))
    constraints = {"comparison","weight","dose","population","route","covariate","analysis_set","analysis_model","analysis_timing"}
    for dimension in sorted({a["dimension"] for a in contract.get("missing_known_mentions",[]) if a["dimension"] in constraints}):
        checks.append({"dimension":dimension,"status":"unknown","reason":"question_requirement_omitted_by_model"})
    if fact and fact['relationship_status']=='structural_candidate' and field['dimension']=='statistic' and field['value'] in ('GMR','CI90'):
        requested={c['dimension'] for c in request['conditions']}
        for dimension,roles in [('analysis_set',{'ADA_negative','ADA_positive'}),('covariate',{'protein_adjusted'})]:
            narrower={a['value'] for a in fact['observations'] if a['dimension']==dimension and a['value'] in roles}
            if narrower and dimension not in requested:
                checks.append({'dimension':dimension,'status':'mismatch','observed':sorted(narrower),'reason':'unrequested_narrow_source_context'})
        if 'weight' not in requested and any(p['dimension']=='weight' for p in fact.get('explicit_relations',[])):
            checks.append({'dimension':'weight','status':'mismatch','reason':'unrequested_weight_subgroup'})
        if not requested & {'analysis_set','covariate','weight','analysis_model','analysis_timing'}:
            timing={a['value'] for a in fact['observations'] if a['dimension']=='analysis_timing' and a['value'] in ('ad_hoc','post_hoc')}
            if timing:checks.append({'dimension':'analysis_timing','status':'mismatch','observed':sorted(timing),'reason':'unrequested_secondary_analysis'})
    statuses = {c["status"] for c in checks}
    status = "mismatch" if "mismatch" in statuses else "unknown" if "unknown" in statuses else "match"
    if request.get("normalization_status", "grounded" if contract["status"] == "anchored_candidate" else "review") != "grounded" and status == "match":
        status = "unknown"
    if fact and fact.get("value_binding_status") == "subcell_unresolved" and status == "match":
        status = "unknown"
    return {"version": VERSION, "request_id": request["id"], "status": status, "checks": checks,
            "semantic_approval": False, "answer_value_unchanged": True}
