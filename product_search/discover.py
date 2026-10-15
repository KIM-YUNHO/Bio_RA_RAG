"""Search an ingredient or a product name and build, for each biosimilar found, the per-product entry collect.py needs.

Sources: EMA medicine and EPAR lists, the Purple Book CSV, FDA GSRS names, ClinicalTrials.gov search, Health Canada DPD.
Downloads go through collect.Collector.fetch into product_search/data/ (same layout as the project's data/).
Usage: uv run python -m product_search.discover yesintek
"""
import csv
import datetime as dt
import io
import json
import re
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

import fitz

from collection import collect
from collection.collect import Links, norm

HERE = Path(__file__).resolve().parent
PROJECT_CFG = json.loads((collect.ROOT / "configs/collection/targets.json").read_text(encoding="utf-8"))
collect.ROOT = HERE  # every collect.py path (data/raw, data/catalog, ...) now points into product_search/data
SOURCES = PROJECT_CFG["sources"]
DAY = 86400

_collector = None


def get(url, kind, source, max_age=DAY):
    """Body of url, reused from product_search/data/raw if fetched within max_age seconds (None = forever)."""
    global _collector
    _collector = _collector or collect.Collector()
    cached = _collector.cache.get(url)
    if cached and (HERE / cached["raw_path"]).exists():
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(cached["retrieved_at"])).total_seconds()
        if max_age is None or age < max_age:
            return (HERE / cached["raw_path"]).read_bytes()
    return _collector.fetch(url, kind, source)[0]


def get_json(url, source, max_age=DAY):
    return json.loads(get(url, "json", source, max_age))


# ---- source lists -------------------------------------------------------------------------------

def ema_biosimilars():
    data = get_json(SOURCES["ema_products"], "EMA")["data"]
    return [p for p in data if p.get("category") == "Human" and p.get("biosimilar") == "Yes" and p.get("medicine_status") == "Authorised"]


def purple_rows():
    # Same link rule as Collector.purple: the newest monthly CSV, then its second ("full") section.
    page = get(SOURCES["purple_downloads"], "html", "Purple Book").decode("utf-8", errors="replace")
    parser = Links(); parser.feed(page)
    months = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
    dated = [(int(m[1]), months[m[2].lower()], urllib.parse.urljoin(SOURCES["purple_downloads"], href))
             for href in parser.links
             if (m := re.search(r"/PurpleBook/(\d{4})/purplebook-search-([A-Za-z]+)-data-download\.csv", href, re.I)) and m[2].lower() in months]
    rows = list(csv.reader(io.StringIO(get(max(dated)[2], "csv", "Purple Book").decode("utf-8-sig"))))
    start = [i for i, r in enumerate(rows) if any(norm(c) == "blanumber" for c in r)][-1]
    header = rows[start]
    return [dict(zip(header, r)) for r in rows[start + 1:] if len(r) == len(header)]


def gsrs_names(ingredient):
    """Proper name (e.g. ustekinumab-kfce) -> GSRS names (brands, development codes)."""
    base = "https://gsrs.ncats.nih.gov/api/v1/"
    q = urllib.parse.urlencode({"q": f'root_names_name:"{ingredient.upper()}"', "top": 50})
    out = {}
    for hit in get_json(base + "substances/search?" + q, "GSRS", 30 * DAY)["content"]:
        full = get_json(base + f"substances({hit['uuid']})?view=full", "GSRS", 30 * DAY)
        out[hit["_name"].lower()] = [n["name"] for n in full.get("names", [])]
    return out


# ---- per-product identifiers --------------------------------------------------------------------

CODE = re.compile(r"(?<![\w-])([A-Za-z]{1,6}(?:-[A-Za-z])?[- ]?\d{2,5}[A-Za-z]?)(?![\w-])")
NOT_CODES = {
    "pasi", "acr", "cdai", "easi", "das", "bsa", "dlqi", "haq", "vas", "iga",
    "il", "cd", "covid", "iso", "ich", "eu", "ec", "ema", "emea", "nct", "ps", "ic",
    "uv", "nrs", "pfs", "dp", "cmco", "sd", "cv", "q", "w", "week", "day", "month", "year",
    "and", "the", "of", "or", "in", "to", "mg", "ml", "kda", "kg", "lot", "batch",
    "table", "figure", "page", "study", "section", "version", "article", "annex",
    "directive", "regulation", "g", "nhv", "pso", "bm", "who", "usp", "ph"
}


def development_code(text, min_count=10):
    """Most frequent product-code-like token (SB17, ABP 654, CT-P43, Bmab1200, BAT2206 ...)."""
    counts, spelling = Counter(), {}
    for token in CODE.findall(text):
        m = re.match(r"[A-Za-z]+", token)
        if not m:
            continue
        alpha = m[0].lower()
        if alpha in NOT_CODES:
            continue
        clean = re.sub(r"[^A-Za-z0-9]", "", token).lower()
        if len(clean) < 3:
            continue
        counts[clean] += 1
        spelling.setdefault(clean, Counter())[token] += 1
    if not counts or counts.most_common(1)[0][1] < min_count:
        return None
    top_clean = counts.most_common(1)[0][0]
    return spelling[top_clean].most_common(1)[0][0]


def code_variants(code):
    if not code:
        return []
    m = re.match(r"([A-Za-z-]+?)[- ]?(\d.*)", code)
    if not m:
        return [code]
    letters, digits = m.groups()
    return list(dict.fromkeys([code, f"{letters} {digits}", f"{letters}-{digits}", f"{letters}{digits}"]))


def epar(ema_product_number, documents):
    # collect.py's ema() needs exactly one initial public assessment report; the same rule picks it here.
    reports = [d for d in documents if d.get("ema_product_number") == ema_product_number and d.get("type") == "assessment-report"
               and "public assessment report" in d.get("name", "").lower()]
    return reports[0] if len(reports) == 1 else None


def epar_code(report):
    body = get(report["document_url"], "pdf", "EMA", max_age=None)
    with fitz.open(stream=body, filetype="pdf") as pdf:
        return development_code("".join(p.get_text() for p in pdf))


def trials(aliases):
    """ClinicalTrials.gov studies whose title/interventions/description name an alias (collect.py checks the same text)."""
    found = {}
    valid_aliases = [a for a in aliases if norm(a) and norm(a) not in NOT_CODES]
    for alias in valid_aliases:
        term = " OR ".join(f'"{v}"' for v in (code_variants(alias) if re.search(r"\d", alias) else [alias]))
        url = "https://clinicaltrials.gov/api/v2/studies?" + urllib.parse.urlencode({"query.term": term, "pageSize": 100})
        for study in get_json(url, "ClinicalTrials.gov")["studies"]:
            ps = study["protocolSection"]
            ident = ps["identificationModule"]
            text = json.dumps({"identification": ident, "interventions": ps.get("armsInterventionsModule"), "description": ps.get("descriptionModule")}, ensure_ascii=False)
            if any(norm(a) in norm(text) for a in valid_aliases):
                found[ident["nctId"]] = {"nct_id": ident["nctId"], "title": ident.get("briefTitle"), "phases": ps.get("designModule", {}).get("phases", []),
                                         "sponsor": ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "")}
    return sorted(found.values(), key=lambda s: s["nct_id"])


def canada_brand(brands):
    for brand in brands:
        url = f"{SOURCES['hc_dpd_api']}?brandname={urllib.parse.quote(brand.lower())}&type=json"
        if any(norm(brand) in norm(p["brand_name"]) for p in get_json(url, "Health Canada")):
            return brand
    return None


# Legal-form and filler words that differ between a company's EU and US entities (from idmp_spike/discover_ingredient.py).
FILLER = set("""gmbh inc ltd limited llc bv kft co corp corporation plc ag sa sl slu ks sp holding holdings ireland deutschland hungary
nl uk usa us europe research healthcare pharma pharmaceuticals pharmaceutical laboratories laboratory biologics biosciences arzneimittel
the and of dr technology solutions""".split())


def company_key(name):
    return frozenset(w for w in re.findall(r"[a-z0-9]+", name.lower().replace("'s", "")) if w not in FILLER and len(w) > 1)


# ---- discovery ----------------------------------------------------------------------------------

def discover(ingredient):
    """All authorised EU biosimilars and US 351(k) products of an ingredient, EU and US entries of one product joined."""
    documents = get_json(SOURCES["ema_documents"], "EMA")["data"]
    eu = [p for p in ema_biosimilars() if norm(p["active_substance"]) == norm(ingredient)]
    us = {}
    for r in purple_rows():
        if r.get("Proper Name", "").strip().lower().startswith(ingredient.lower() + "-") and "351(k)" in r.get("License Type", ""):
            u = us.setdefault(r["Proprietary Name"].strip(), {"brand": r["Proprietary Name"].strip(), "proper": r["Proper Name"].strip().lower(), "company": r["Applicant"].strip(), "bla": set()})
            u["bla"].add(r["BLA Number"].strip())
    # Originator-only ingredients need no US biosimilar alias lookup.
    names = gsrs_names(ingredient) if us else {}
    gsrs = {u["brand"]: names.get(u["proper"], []) for u in us.values()}
    gsrs_code = {b: next((n for n in ns if CODE.fullmatch(n) and re.search(r"\d", n)), None) for b, ns in gsrs.items()}

    products, used_us = [], set()
    for p in eu:
        report = epar(p["ema_product_number"], documents)
        code = epar_code(report) if report else None
        aliases_tentative = list(dict.fromkeys((code_variants(code) if code else []) + [p["name_of_medicine"]]))
        studies = trials(aliases_tentative)
        companies = company_key(p["marketing_authorisation_developer_applicant_holder"]).union(*(company_key(s["sponsor"]) for s in studies))
        # EU -> US: same brand, else the US product's GSRS names list the EU brand or code, else one US applicant shares a company word.
        match = [b for b in us if norm(b) == norm(p["name_of_medicine"])] \
            or [b for b in us if {norm(p["name_of_medicine"]), norm(code or "-")} & {norm(n) for n in gsrs[b]}] \
            or [b for b in us if company_key(us[b]["company"]) & companies]
        us_brand = match[0] if len(match) == 1 else None
        code = code or (gsrs_code.get(us_brand) if us_brand else None)
        if code and not any(norm(code) in norm(s["title"] + s.get("brief_title", "")) for s in studies):
            extra_studies = trials(code_variants(code))
            for s in extra_studies:
                if not any(x["nct_id"] == s["nct_id"] for x in studies):
                    studies.append(s)
        e = entry(ingredient, code, p["name_of_medicine"], us_brand, p["marketing_authorisation_developer_applicant_holder"],
                  p["ema_product_number"], bool(report), studies, us_candidates=[] if us_brand else match)
        e["ema_authorised"] = iso_date(p.get("marketing_authorisation_date"))
        products.append(e)
        used_us.add(us_brand)
    for brand, u in us.items():
        if brand not in used_us:
            products.append(entry(ingredient, gsrs_code[brand], None, brand, u["company"], None, False, None))
    biosimilars = merge_licences(products, us)
    references = reference_products(ingredient, documents)
    for p in biosimilars:
        p["kind"] = "biosimilar"
        # The reference product the biosimilar was compared with; one originator per ingredient is the usual case.
        p["reference_product_id"] = references[0]["product_id"] if len(references) == 1 else None
    return references + biosimilars


def reference_products(ingredient, documents):
    """The originator of the ingredient: authorised in the EU without the biosimilar flag, or licensed in the US under 351(a) with the
    bare proper name (no four-letter suffix). Preserve a registered development code when available; otherwise use the brand
    as product_id. A frequent code in an originator EPAR may be a trial number, not a product development code.
    Its own trials are not searched: the evidence the biosimilar pathway relies on is in its review documents."""
    eu = [p for p in get_json(SOURCES["ema_products"], "EMA")["data"] if p.get("category") == "Human" and p.get("biosimilar") != "Yes"
          and p.get("medicine_status") == "Authorised" and norm(p.get("active_substance")) == norm(ingredient)]
    us = {}
    for r in purple_rows():
        if r.get("Proper Name", "").strip().lower() == ingredient.lower() and "351(a)" in r.get("License Type", ""):
            u = us.setdefault(r["Proprietary Name"].strip(), {"brand": r["Proprietary Name"].strip(), "company": r["Applicant"].strip(), "bla": set()})
            u["bla"].add(r["BLA Number"].strip())
    entries, used = [], set()
    for p in eu:
        report = epar(p["ema_product_number"], documents)
        registered = next((t for t in PROJECT_CFG["products"] if t.get("kind") == "reference"
                           and norm(t.get("ingredient") or PROJECT_CFG["ingredient"]) == norm(ingredient)
                           and norm(t.get("ema_name")) == norm(p["name_of_medicine"])), None)
        code = registered.get("development_code") if registered else None
        us_brand = next((b for b in us if norm(b) == norm(p["name_of_medicine"])), None)
        e = entry(ingredient, code, p["name_of_medicine"], us_brand, p["marketing_authorisation_developer_applicant_holder"], p["ema_product_number"], bool(report), [])
        e["ema_authorised"] = iso_date(p.get("marketing_authorisation_date"))
        entries.append(e)
        used.add(us_brand)
    for brand, u in us.items():
        if brand not in used:
            entries.append(entry(ingredient, None, None, brand, u["company"], None, False, []))
    references = merge_licences(entries, us)
    for p in references:
        p["kind"], p["reference_product_id"] = "reference", None
    return references


def iso_date(value):
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", (value or "").strip())
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


def merge_licences(entries, us):
    """One product per development code. The EMA list has one row per marketing authorisation, so a product licensed twice in the EU
    (Yesintek and Usrenty, Uzpruvo and Usgena, Otulfi and Fymskina) arrives as two entries; they become one product whose
    licences are listed, so product_id (the key documents, chunks and later database rows hang on) never depends on list order.

    Primary EU licence (its documents are collected; the others are recorded only): the earliest EU authorisation, which is the
    original application; on the same date, the one named like the US or Canadian product."""
    groups = {}
    for e in entries:
        key = norm(e["development_code"]) if e["development_code"] else "brand:" + norm(e["us_brand"] or e["ema_name"])
        groups.setdefault(key, []).append(e)
    products = []
    for members in groups.values():
        eu = [m for m in members if m["ema_name"]]
        rank = lambda m: (m.get("ema_authorised") or "9999", norm(m["ema_name"]) not in {norm(m["us_brand"] or ""), norm(m["ca_brand"] or "")}, m["ema_product_number"] or "")
        primary = min(eu, key=rank) if eu else members[0]
        p = dict(primary)
        p["us_brand"] = next((m["us_brand"] for m in [primary] + members if m["us_brand"]), None)
        p["ca_brand"] = next((m["ca_brand"] for m in [primary] + members if m["ca_brand"]), None)
        p["aliases"] = list(dict.fromkeys(a for m in [primary] + members for a in m["aliases"]))
        studies = {s["nct_id"]: s for m in members for s in m["studies"]}
        p["studies"], p["study_ids"] = sorted(studies.values(), key=lambda s: s["nct_id"]), sorted(studies)
        p["licences"] = [{"jurisdiction": "EU", "licence_id": m["ema_product_number"], "name": m["ema_name"], "holder": m["company"],
                          "authorised": m.get("ema_authorised"), "primary": m is primary, "collected": m is primary} for m in eu]
        if p["us_brand"]:
            u = us.get(p["us_brand"], {})
            p["licences"].append({"jurisdiction": "US", "licence_id": "BLA " + "/".join(sorted(u.get("bla", []))), "name": p["us_brand"],
                                  "holder": u.get("company"), "primary": True, "collected": True})
        if p["ca_brand"]:
            p["licences"].append({"jurisdiction": "CA", "licence_id": None, "name": p["ca_brand"], "holder": None, "primary": True, "collected": True})
        products.append(p)
    # Two codes normalising to the same id would mean the code reading went wrong; keep them apart and visible.
    seen = Counter(p["product_id"] for p in products)
    for p in products:
        if seen[p["product_id"]] > 1:
            p["product_id"] += "_" + norm(p["ema_name"] or p["us_brand"]).upper()
            p["id_conflict"] = True
    return products


def entry(ingredient, code, ema_name, us_brand, company, ema_number, has_epar, studies, us_candidates=()):
    brands = [b for b in dict.fromkeys([ema_name, us_brand]) if b]
    aliases = (code_variants(code) if code else []) + brands
    if studies is None:
        studies = trials(aliases)
    return {"product_id": norm(code).upper() if code else norm(brands[0]).upper(), "ingredient": ingredient, "development_code": code,
            "ema_name": ema_name, "ema_product_number": ema_number, "ema_epar": has_epar, "us_brand": us_brand, "us_candidates": list(us_candidates),
            "company": company, "aliases": aliases, "study_ids": [s["nct_id"] for s in studies], "studies": studies,
            "ca_brand": canada_brand(brands), "pmda_general_list_ids": []}


def search(query):
    """Official ingredient -> its products; official brand -> that product only.

    Ingredient discovery also accepts originators without authorised biosimilars.
    Unknown names do not expand to other products of an inferred ingredient.
    """
    q = norm(query)
    if not q:
        return {"query": query, "kind": None, "ingredient": None, "products": [], "message": "제품명 또는 성분명을 입력해 주세요."}
    everything = [p for p in get_json(SOURCES["ema_products"], "EMA")["data"]
                  if p.get("category") == "Human" and p.get("medicine_status") == "Authorised"]
    ingredient = next((p["active_substance"].strip().lower() for p in everything if norm(p.get("active_substance")) == q), None)
    # US-only ingredients are discoverable too; remove only a 351(k) suffix.
    def us_ingredient(row):
        proper = row.get("Proper Name", "").strip().lower()
        return re.sub(r"-[a-z]{4}$", "", proper) if "351(k)" in row.get("License Type", "") else proper

    us = None
    if not ingredient:
        us = purple_rows()
        ingredient = next((us_ingredient(r) for r in us if norm(us_ingredient(r)) == q
                           and any(t in r.get("License Type", "") for t in ("351(a)", "351(k)"))), None)
    kind = "ingredient"
    if not ingredient:
        kind = "product"
        ingredient = next((p["active_substance"].strip().lower() for p in everything if norm(p["name_of_medicine"]) == q), None) \
            or next((us_ingredient(r) for r in us if norm(r.get("Proprietary Name", "")) == q
                     and any(t in r.get("License Type", "") for t in ("351(a)", "351(k)"))), None)
    if not ingredient:
        return {"query": query, "kind": None, "ingredient": None, "products": [], "message": "EMA 허가 의약품 또는 Purple Book 목록에서 제품·성분을 찾지 못했습니다."}
    products = discover(ingredient)
    if kind == "product":
        # Never return unrelated products when a recognised brand could not be grouped.
        products = [p for p in products if q in {norm(v or "") for v in
                    [l["name"] for l in p["licences"]] + p["aliases"]}]
    collected = {p["product_id"] for p in read_jsonl(HERE / "data/catalog/products.jsonl")}
    for p in products:
        p["collected"] = p["product_id"] in collected
    return {"query": query, "kind": kind, "ingredient": ingredient, "products": products}


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()] if path.exists() else []


if __name__ == "__main__":
    assert development_code("SB17 " * 12 + "IL-23 " * 30 + "PS80") == "SB17"
    assert development_code("ABP 654 " * 3) is None
    assert code_variants("ABP 654") == ["ABP 654", "ABP-654", "ABP654"]
    assert code_variants("CT-P43") == ["CT-P43", "CT-P 43", "CT-P-43"]
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(search(" ".join(sys.argv[1:]) or "ustekinumab"), ensure_ascii=False, indent=2))
