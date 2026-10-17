"""From a chosen product to a runnable IngestionRequest (plan P3, section 7.4 plan_ingestion).

product_search.discover groups the product's licences (reading the development code from its EPAR) and finds its trials. Its output
is the tool result the request is checked against: every identifier in the request must appear in it (IngestionRequest validator).
"""
import datetime as dt
import json
import re

from pydantic import ValidationError

from ingestion.contracts import ROOT, Evidence, IngestionRequest, ProductTarget, load_targets
from ingestion.sources import norm

TARGET_FIELDS = ("product_id", "development_code", "kind", "reference_product_id", "ingredient", "ema_name", "us_brand", "ca_brand",
                 "aliases", "study_ids", "pmda_general_list_ids", "licences", "company")


class PlanError(ValueError):
    pass


def staged_products():
    path = ROOT / "product_search/data/catalog/products.jsonl"
    return {json.loads(l)["product_id"] for l in path.read_text(encoding="utf-8").splitlines() if l.strip()} if path.exists() else set()


def find_entry(candidate, result=None):
    """Keep a resolved official licence identity; use names only for code/brand-only candidates."""
    wanted = {norm(n) for n in candidate.names.values()} | {norm(e.matched_text) for e in candidate.evidence}
    if candidate.key.startswith("CODE:"):
        wanted.add(norm(candidate.key[5:]))
    if result is None:
        from product_search import discover  # heavy import; sets collect.ROOT to product_search/ for this process
        result = discover.search(candidate.ingredient)
    def matches(product):
        if norm(product.get("ingredient")) != norm(candidate.ingredient):
            return False
        licences = product.get("licences", [])
        if candidate.key.startswith("EU:"):
            return any(l["jurisdiction"] == "EU" and l.get("licence_id") == candidate.key[3:] for l in licences)
        if candidate.key.startswith("US:BLA"):
            bla = candidate.key[len("US:BLA"):]
            return any(l["jurisdiction"] == "US" and bla in re.findall(r"\d+", l.get("licence_id") or "") for l in licences)
        return bool(wanted & ({norm(l["name"]) for l in licences} | {norm(a) for a in product.get("aliases", [])}))

    entries = [p for p in result["products"] if matches(p)]
    if len(entries) != 1:
        found = ", ".join(sorted(p["product_id"] for p in entries)) or "none"
        raise PlanError(f"discover found {len(entries)} matching products for {candidate.key} ({found}); "
                        "the selected product's ingredient and licence identity must match exactly")
    return entries[0]


def target_of(entry):
    data = {k: entry.get(k) for k in TARGET_FIELDS}
    for k in ("aliases", "study_ids", "pmda_general_list_ids", "licences"):
        data[k] = data[k] or []
    data["licences"] = [{k: l.get(k) for k in ("jurisdiction", "licence_id", "name", "holder", "authorised", "primary", "collected")} for l in data["licences"]]
    data["kind"] = data["kind"] or "biosimilar"
    if data["kind"] == "biosimilar" and not data["reference_product_id"]:
        refs = [p.product_id for p in load_targets().products if p.kind == "reference" and load_targets().ingredient_of(p) == entry["ingredient"]]
        data["reference_product_id"] = refs[0] if len(refs) == 1 else None
    data["added"] = dt.date.today().isoformat()
    return ProductTarget.model_validate(data)


def brand(product):
    return next((n for n in (product.get("ema_name"), product.get("us_brand"), product.get("ca_brand")) if n), product["product_id"])


def label(product):
    return f"{product['product_id']}({brand(product)})"


def check_reference(entry, references):
    """A biosimilar is added under a registered reference product of its ingredient; that product is registered first.
    Otherwise promote would write a targets.json the contract refuses (reference_product_id must name a registered reference)."""
    if (entry.get("kind") or "biosimilar") != "biosimilar":
        return
    targets = load_targets()
    registered = [p.product_id for p in targets.products if p.kind == "reference" and norm(targets.ingredient_of(p)) == norm(entry["ingredient"])]
    ref = entry.get("reference_product_id") or (registered[0] if len(registered) == 1 else None)
    if not ref:
        found = ", ".join(label(r) for r in references) or "찾지 못함"
        raise PlanError(f"{entry['product_id']}의 대조약을 하나로 정할 수 없습니다(공식 목록의 오리지널: {found}). "
                        "대조약이 여럿인 성분은 아직 자동 적재를 지원하지 않습니다.")
    if ref not in registered:
        reference = next((r for r in references if r["product_id"] == ref), {"product_id": ref})
        raise PlanError(f"{entry['product_id']}의 대조약 {label(reference)}이(가) 아직 자료 저장소에 없습니다. "
                        f"대조약을 먼저 적재한 뒤(예: '{brand(reference)} 수집') 이 제품을 요청하세요.")


def plan(candidate, user_texts):
    from product_search import discover  # heavy import; sets collect.ROOT to product_search/ for this process
    result = discover.search(candidate.ingredient)
    entry = find_entry(candidate, result)
    if entry["product_id"] in {p.product_id for p in load_targets().products}:
        raise PlanError(f"{entry['product_id']} is already a project product; it is updated by collect.py (refresh), not added again")
    check_reference(entry, [p for p in result["products"] if p.get("kind") == "reference"])
    try:
        target = target_of(entry)
        tool_text = json.dumps(entry, ensure_ascii=False)
        today = dt.date.today().isoformat()
        provenance = {}
        if target.development_code:
            provenance[f"{target.product_id}.development_code"] = Evidence(source="EMA EPAR text via product_search.discover", url=None,
                                                                           matched_text=target.development_code, as_of=today, snapshot_sha256=None)
        if target.study_ids:
            provenance[f"{target.product_id}.study_ids"] = Evidence(source="ClinicalTrials.gov via product_search.discover", url=None,
                                                                    matched_text=" ".join(target.study_ids), as_of=today, snapshot_sha256=None)
        jurisdictions = [j for j, name in (("EU", target.ema_name), ("US", target.us_brand), ("CA", target.ca_brand)) if name]
        request = IngestionRequest.model_validate(
            {"kind": "new_product", "ingredient": entry["ingredient"], "products": [target.model_dump()], "include_reference": target.kind == "reference",
             "jurisdictions": jurisdictions, "provenance": {k: v.model_dump() for k, v in provenance.items()}},
            context={"evidence_text": [tool_text] + list(user_texts)})
    except ValidationError as exc:
        # The agent shows a PlanError as a reply; a contract failure must not end the turn with an exception.
        raise PlanError(f"{entry['product_id']} 적재 계획이 계약 검증을 통과하지 못했습니다: {exc.errors()[0]['msg']}") from exc
    return {"entry": entry, "request": request, "already_staged": target.product_id in staged_products()}
