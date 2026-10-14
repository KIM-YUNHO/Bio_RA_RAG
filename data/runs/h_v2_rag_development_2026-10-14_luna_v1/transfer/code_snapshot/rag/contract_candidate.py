"""Candidate source selection + request validation, isolated from the live server.

The selector returns immutable source IDs. Values are never rewritten. Unknown
relationships keep related evidence; only clear contradictions are rejected.
"""
from copy import deepcopy
import json
import re
from rag import source_selection
from rag.contract_match import match_request
from rag.contract_relations import table_fact, prose_fact
from rag.grounded_table_graph import build
from rag.request_coverage import classify, coverage

VERSION = "contract-candidate-7-asserted-physical-scope"
DIMENSION_LABELS = {"statistic":"통계량","endpoint":"평가변수","unit":"단위","study":"시험 범위",
    "comparison":"비교 대상·방향","weight":"체중 범위","dose":"용량","population":"대상 집단","route":"투여 경로",
    "covariate":"보정·공변량","analysis_set":"분석군","analysis_model":"분석 방법","analysis_timing":"분석 시점",
    "attribute":"요청 항목", "analysis_role":"분석 역할", "timepoint":"평가 시점"}


def request_facet(request, default):
    field = request["field"]
    if field["dimension"] == "attribute":
        if field["value"] == "assay":
            return "assay"
        if field["value"] in ("phase", "randomization", "blinding", "arms"):
            return "design"
    if field["dimension"] in ("endpoint", "statistic"):
        return "pk_results"
    return default


def catalog(question, evidence):
    """Keep legacy filters, adding complete cells with an explicit GMR header."""
    selected = source_selection.catalog(evidence, question)
    if not re.search(r"GMR|geometric.*ratio|기하평균비", question, re.I):
        return selected
    raw = source_selection.catalog(evidence)
    from rag.contract_terms import atoms
    for cid, c in raw.items():
        if cid in selected or c["kind"] != "table_cell" or c.get("source_header") or not re.match(r"^\s*[($+−–-]*\s*\d",c["value"]):
            continue
        headers = [raw[i]["value"] for i in c.get("context_ids", [])]
        if any(a["dimension"] == "statistic" and a["value"] == "GMR" for h in headers for a in atoms(h)):
            from rag.regulatory_scope import eligible
            from rag.source_context import comparison_conflict
            if not eligible(c, raw, evidence, question) or comparison_conflict(c, raw, question):
                continue
            # This supplements a spelling omission in the legacy Ratio/CI filter;
            # the independent request/scope checker still validates every value.
            selected[cid] = deepcopy(c)
            for parent in c.get("context_ids", []):
                selected.setdefault(parent, deepcopy(raw[parent]))
    return selected


def selector_prompt(question, understanding, unit, evidence, candidates=None):
    messages = source_selection.prompt(question, understanding, unit, evidence)
    candidates = candidates or catalog(question, evidence)
    old = source_selection.catalog(evidence, question)
    extra = [c for cid,c in candidates.items() if cid not in old]
    if extra:
        messages[1]["content"] += "\nAdditional printed GMR header candidates: " + json.dumps(extra, ensure_ascii=False)
    return messages


def relationship_hints(audits):
    """Positive partial evidence helps selection without upgrading uncertainty."""
    hints = []
    for cid,audit in audits.items():
        if audit["status"] == "mismatch":
            hints.append({"source_id":cid,"status":"mismatch","requests":[]});continue
        requests = []
        for check in audit["requests"]:
            if check["status"] == "mismatch" or check["checks"][0]["status"] != "match":
                continue
            matched = sorted({c["dimension"] for c in check["checks"] if c["status"] == "match"})
            unknown = sorted({c["dimension"] for c in check["checks"] if c["status"] == "unknown"})
            requests.append({"request_id":check["request_id"],"status":check["status"],
                             "matched_dimensions":matched,"unresolved_dimensions":unknown})
        if requests:
            hints.append({"source_id":cid,"status":audit["status"],"requests":requests})
    return hints


def inventory(question, interpretation, unit, evidence, *, candidates=None):
    candidates = catalog(question, evidence) if candidates is None else candidates
    graphs = {eid: build(block) for eid, block in evidence.items()}
    contract = interpretation["requirements"]; u = interpretation["understanding"]
    physical=None
    # A single still-unknown request must not lose explicit, unambiguous
    # physical constraints from the original question. This is a negative
    # guard, not semantic approval or a global analysis condition broadcast.
    if len(contract['requests'])==1 and contract['requests'][0]['field']['dimension']=='literal_request':
        from rag.known_source_constraints import requested as original_constraints
        original=original_constraints(question)
        physical={**original,'constraints':[c for c in original['constraints']
                  if c['dimension'] in ('route','formulation','weight_point','comparison')]}
    audits = {}
    for cid in source_selection.value_ids(candidates):
        candidate = candidates[cid]
        facts = {}; checks = []
        for request in contract["requests"]:
            requested = [v["value"] for c in request["conditions"] if c["dimension"] == "study" and c["operator"] == "eq" for v in c["values"]] or u["studies"]
            facet = request_facet(request, u["topic"]); context = (facet, tuple(requested))
            if context not in facts:
                facts[context] = table_fact(candidate, evidence, requested, facet, graphs[candidate["evidence"]])
                if facts[context] is None:
                    facts[context] = prose_fact(candidate, evidence, requested, facet)
            fact = facts[context]; check = match_request(contract, request, fact)
            if physical and physical['constraints']:
                from rag.known_source_constraints import check as physical_check
                guard=physical_check(candidate,candidates,evidence,physical,graphs)
                check['checks'].extend({**c,'basis':'unambiguous_original_constraint_of_single_unresolved_request'} for c in guard['checks'])
                unresolved=[c['dimension'] for c in guard['checks'] if c['dimension'] in ('formulation','weight_point') and c['status']=='unknown']
                if physical['requires_asserted_quote'] and unresolved:
                    candidate['value_eligible']=False
                    candidate['selection_exclusion']='requested_asserted_quote_physical_scope_unresolved'
                    candidate['asserted_quote_unresolved_physical_dimensions']=unresolved
                if guard['status']=='mismatch':check['status']='mismatch'
                elif guard['status']=='unknown' and check['status']=='match':check['status']='unknown'
            if requested:
                scopes = (fact or {}).get("scope_audit", {}).get("requested", [])
                statuses = {s["status"] for s in scopes}
                guard = "match" if scopes and statuses == {"explicit_candidate"} else "mismatch" if scopes and statuses == {"mismatch"} else "unknown"
                check["checks"].append({"dimension": "study", "status": guard, "reason": "independent_requested_study_scope", "source_scope": scopes})
                if guard == "mismatch":
                    check["status"] = "mismatch"
                elif guard == "unknown" and check["status"] == "match":
                    check["status"] = "unknown"
            checks.append(check)
        fact = next(iter(facts.values()), None)
        audits[cid] = {"fact": fact, "requests": checks,
                       "status": classify(checks)["status"]}
    return candidates, audits


def prompt(question, interpretation, unit, evidence, candidates=None, audits=None):
    if candidates is None or audits is None:
        candidates, audits = inventory(question, interpretation, unit, evidence)
    messages = selector_prompt(question, interpretation["understanding"], unit, evidence, candidates)
    messages[0]["content"] += '''\nUse the independent original-question obligations below, even
if the English query omits a field. All conditions inside one request are AND.
Printed source relations are candidates, never gold or clinical certification.
Do not use a value rejected for every request. Unknown scope remains related
evidence, not proof of absence. A programme-wide assay is related unless its
application to the requested trial is explicitly stated. Shared parent-study
design can supply a component when the same source explicitly names that part;
part-specific counts/outcomes cannot inherit from a parent. Units are metadata
from the selected value's own row/column, not a new numeric result item.
When selecting ratios, report the printed numerator/denominator; never invert a
number to fit an unstated orientation. Preserve complete mean/SD and CI cells.'''
    obligations = [{"id": r["id"], "field": [r["field"]["dimension"], r["field"]["value"]],
                    "AND": [[c["dimension"], c["operator"], [v["value"] for v in c["values"]]] for c in r["conditions"]]}
                   for r in interpretation["requirements"]["requests"]]
    # Unknown timing need not hide the source that satisfies the other AND
    # conditions. Partial hints are selection signals, never completion proof.
    hints = relationship_hints(audits)
    messages[1]["content"] += "\nOriginal-question obligations: " + json.dumps(obligations, ensure_ascii=False, separators=(",", ":"))
    messages[1]["content"] += "\nLocal source relationship candidates (partial matches remain unconfirmed; prefer the most fully connected requested conditions and preserve unresolved conditions): " + json.dumps(hints, ensure_ascii=False, separators=(",", ":"))
    return messages


def validate_selection(raw, candidates, audits, evidence, interpretation, question):
    output = source_selection.resolve(raw, candidates, evidence, question, interpretation["understanding"])
    accepted = []; related = []; rejected = []; seen = set()
    for item in output["items"]:
        cid = item["source_selection"]["value_id"]; audit = audits[cid]
        if cid in seen:
            continue
        seen.add(cid)
        classification = classify(audit["requests"])
        if classification["status"] == "mismatch":
            rejected.append({"item": item, "request_checks": audit["requests"]}); continue
        item = deepcopy(item); item["contract_audit"] = audit["requests"]
        item["exact"] = classification["status"] == "supported"
        fact = audit["fact"]
        item["source_relations"] = {"scope": fact.get("scope_audit") if fact else None,
            "explicit": fact.get("explicit_relations", []) if fact else [], "semantic_approval": False}
        source_units = sorted({a["value"] for a in fact["observations"] if a["dimension"] == "unit"}) if fact else []
        item["endpoint_units"] = source_units if fact and fact.get("ratio_statistic") else []
        item["printed_units"] = [] if fact and fact.get("ratio_statistic") else source_units
        item["ratio_statistic"] = bool(fact and fact.get("ratio_statistic"))
        matched = [c["request_id"] for c in classification["supported"]]
        item["matched_requests"] = matched
        viable = [c for c in classification["viable"] if c["checks"][0]["status"] == "match"]
        requests = {r["id"]:r for r in interpretation["requirements"]["requests"]}
        fields = list(dict.fromkeys(requests[c["request_id"]]["field"]["value"] for c in viable))
        own_roles = list(dict.fromkeys(a["value"] for a in (fact or {}).get("observations",[]) if a["dimension"] in ("endpoint","dose")))
        pairs = {tuple(p["values"]) for p in (fact or {}).get("explicit_relations",[]) if p["dimension"] == "comparison" and p.get("operator") == "ordered_pair"}
        comparison = ["/".join(next(iter(pairs)))] if len(pairs) == 1 else []
        item["verified_role_label"] = " · ".join([*own_roles,*fields,*comparison])
        item["unresolved_dimensions"] = sorted({c["dimension"] for check in viable for c in check["checks"] if c["status"] == "unknown"})
        # A prose quotation retains the selected context; it cannot create numbers
        # or certify every field merely because the model labelled it exact.
        if item["exact"]:
            accepted.append(item)
        else:
            related.append(item)
    contract = interpretation["requirements"]
    common_coverage = coverage(contract, {item["source_selection"]["value_id"]: item["contract_audit"]
                                          for item in accepted + related})
    covered = set(common_coverage["covered"])
    missing = common_coverage["missing"]
    complete = not missing and contract["status"] == "anchored_candidate"
    status = "answered" if complete and accepted else "partial" if accepted or related else "not_found"
    lines = []
    if contract["status"] != "anchored_candidate":
        lines.append("질문의 요청 항목·조건 해석에 미확정 내용이 있습니다.")
    if missing:
        lines.append("확정하지 못한 요청 항목이 있습니다. 아래 관련 근거의 조건을 함께 확인해야 합니다.")
    for item in accepted + related:
        label = item["verified_role_label"] or "관련 근거"
        unresolved = " · 조건 미확정: " + ", ".join(DIMENSION_LABELS.get(d,d) for d in item["unresolved_dimensions"]) if item["unresolved_dimensions"] else ""
        if not item["exact"] and not unresolved:
            unresolved = " · 조건 미확정"
        unit = " · 단위: " + ", ".join(item["printed_units"]) if item["printed_units"] else ""
        lines.append(f"{label}: {item['value']}{unit}{unresolved} [{', '.join(item['evidence'])}]")
    if not lines:
        lines.append("검색된 근거에서 요청 조건에 맞는 값을 확인하지 못했습니다.")
    return {"version": VERSION, "status": status, "items": accepted, "related_items": related,
            "rejected_items": rejected, "answer_ko": "\n".join(lines), "request_coverage": {
                "requested": len(contract["requests"]), "covered": sorted(covered), "missing": missing,
                "contract_status": contract["status"], "kernel_version": common_coverage["version"],
                "source_support": common_coverage["source_support"]}, "semantic_approval": False,
            "values_rewritten": False, "corpus_absence_claimed": False}


def answer(question, interpretation, unit, evidence, *, chat_client):
    if chat_client is None:
        raise ValueError("candidate requires an explicit injected client")
    candidates, audits = inventory(question, interpretation, unit, evidence)
    response = chat_client(prompt(question, interpretation, unit, evidence, candidates, audits),
                           json_schema=source_selection.schema(candidates))
    raw = json.loads(response["text"])
    return {"raw_selection": raw, "validated": validate_selection(raw, candidates, audits, evidence, interpretation, question),
            "model": response.get("model_reported"), "model_calls": 1}
