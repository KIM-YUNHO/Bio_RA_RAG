"""Standalone question obligations with exact original-language anchors.

This module does not call a model, retrieve documents or change the live pipeline.
Proposals remain semantic candidates even when their quotations are valid.
"""
from copy import deepcopy
import hashlib
import re
from rag.chunk_schema import obj, validate
from rag.contract_terms import atoms, grounded_value, key

VERSION = "request-contract-3"
STR = {"type": "string"}
ANCHOR = obj({"quote": STR, "occurrence": {"type": "integer", "minimum": 0}})
TERM = obj({"dimension": STR, "value": STR, "anchor": ANCHOR})
CONDITION = obj({"dimension": STR, "operator": {"type": "string", "enum": ["eq", "not_eq", "one_of", "ordered_pair", "unordered_pair", "range"]},
                 "values": {"type": "array", "items": obj({"value": STR, "anchor": ANCHOR}), "minItems": 1}})
PROPOSAL_SCHEMA = obj({
    "requests": {"type": "array", "minItems": 1, "items": obj({"id": STR, "field": TERM,
        "conditions": {"type": "array", "items": CONDITION}})},
    "non_request_mentions": {"type": "array", "items": obj({"term": TERM,
        "role": {"type": "string", "enum": ["context", "excluded"]}})},
})
PROMPT = """Return question obligations only, without reading documents or answering.
Keep every requested field and all conjunctive conditions. Separate alternative
values (one_of) from AND conditions (separate conditions). Preserve comparison
numerator/denominator order with ordered_pair. Use unordered_pair if the question
only says 'between' without specifying direction; never invent orientation.
Quote the Korean original for each
field and each condition value; occurrence is a zero-based literal occurrence.
Use context/excluded mentions only when the question actually gives that role.
Do not replace AUClast with AUCinf, or infer units/statistics not requested.
Quotation validation is not semantic approval. Ambiguity requires later review.
"""


def locate(question, anchor):
    quote = anchor["quote"]
    if not quote.strip():
        raise ValueError("empty question anchor")
    starts = []; position = 0
    while True:
        position = question.find(quote, position)
        if position < 0:
            break
        starts.append(position); position += 1
    occurrence = anchor["occurrence"]
    if occurrence >= len(starts):
        raise ValueError("question anchor is not a literal occurrence")
    start = starts[occurrence]
    return {**anchor, "start": start, "end": start + len(quote)}


def ground(question, proposal, normalizer=None):
    validate(proposal, PROPOSAL_SCHEMA, "request_proposal")
    result = deepcopy(proposal); terms = []; ids = set(); warnings = []
    known = atoms(question)
    for request in result["requests"]:
        if not request["id"].strip() or request["id"] in ids:
            raise ValueError("request IDs must be nonempty and unique")
        ids.add(request["id"]); terms.append(request["field"])
        request["condition_logic"] = "AND"
        for condition in request["conditions"]:
            n = len(condition["values"])
            if (condition["operator"] in ("eq", "not_eq") and n != 1) or (condition["operator"] in ("ordered_pair", "unordered_pair", "range") and n != 2):
                raise ValueError("operator/value arity mismatch")
            for value in condition["values"]:
                terms.append({"dimension": condition["dimension"], **value})
    terms.extend(m["term"] for m in result["non_request_mentions"])
    # Revisit actual nested objects; condition term wrappers share anchor objects.
    for term in terms:
        resolved = locate(question, term["anchor"])
        term["anchor"].update(resolved)
        verified = grounded_value(term["dimension"], term["value"], resolved["quote"]) or bool(normalizer and normalizer(term["dimension"], term["value"], resolved["quote"]))
        local = [a for a in atoms(resolved["quote"]) if a["dimension"] == term["dimension"] and key(a["value"]) == key(term["value"])]
        # Literal substring matching cannot turn the 'mean' inside GMR into an
        # arithmetic-mean request. Verify role boundaries in the WHOLE question.
        conflict = bool(local) and not any(a["dimension"] == term["dimension"] and key(a["value"]) == key(term["value"])
            and resolved["start"] <= a["start"] and a["end"] <= resolved["end"] for a in known)
        term["normalization_status"] = "grounded" if verified and not conflict else "review"
        if term["normalization_status"] == "review":
            warnings.append({"reason": "question_anchor_role_conflict" if conflict else "unverified_term_normalization",
                "dimension": term["dimension"], "value": term["value"], "source_range": [resolved["start"], resolved["end"]]})
    for request in result["requests"]:
        own = [request["field"]]
        for c in request["conditions"]:
            for v in c["values"]:
                t = next(t for t in terms if t["dimension"] == c["dimension"] and t["value"] == v["value"] and t["anchor"] == v["anchor"])
                v["normalization_status"] = t["normalization_status"]; own.append(t)
        request["normalization_status"] = "review" if any(t["normalization_status"] == "review" for t in own) else "grounded"
    missing = []
    def covered(atom):
        return any(t["normalization_status"] == "grounded" and t["dimension"] == atom["dimension"] and key(t["value"]) == key(atom["value"]) and
                      t["anchor"]["start"] <= atom["start"] and atom["end"] <= t["anchor"]["end"] for t in terms)
    for atom in known:
        present = covered(atom)
        # Adjacent parenthesised aliases name the same occurrence, not a second
        # request. Repetitions elsewhere remain separate coverage alarms.
        if not present:
            for other in known:
                if (other["dimension"],key(other["value"])) != (atom["dimension"],key(atom["value"])) or not covered(other):
                    continue
                first, second = sorted([atom,other],key=lambda a:a["start"])
                if re.fullmatch(r"\s*\(\s*",question[first["end"]:second["start"]]) and re.match(r"\s*\)",question[second["end"]:]):
                    present = True; break
        if not present:
            missing.append(atom)
    return {"version": VERSION, "question": question, "question_sha256": hashlib.sha256(question.encode()).hexdigest(),
            **result, "missing_known_mentions": missing, "warnings": warnings,
            "status": "incomplete" if missing else "normalization_review" if warnings else "anchored_candidate",
            "semantic_approval": False, "coverage_is_exhaustive": False}


def translation_omissions(question, english_query):
    """Diagnostic coverage alarm, independent of the generated English query."""
    translated = {(a["dimension"], key(a["value"])) for a in atoms(english_query)}
    return [a for a in atoms(question) if (a["dimension"], key(a["value"])) not in translated]
