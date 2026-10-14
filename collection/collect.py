#!/usr/bin/env python3
"""Small reproducible regulatory-document collection pilot. No LLM/API key needed."""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import html
import io
import json
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path
import fitz
import pdfplumber
from collection import text_layer

ROOT = Path(__file__).resolve().parents[1]  # data root; product_search/ points it at its own folder
PROJECT = Path(__file__).resolve().parents[1]  # project root; shared rule files are in configs/collection/
SCHEMA_VERSION = "pilot-1"
# Collection rules: docs/collection/INGESTION_PIPELINE.md

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def write_jsonl(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in items), encoding="utf-8")

def norm(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []; self.anchors = []; self.active = None
    def handle_starttag(self, tag, attrs):
        if tag == "a" and dict(attrs).get("href"):
            self.links.append(dict(attrs)["href"])
            self.active = {"href": dict(attrs)["href"], "title": dict(attrs).get("title", ""), "text": ""}
    def handle_data(self, data):
        if self.active is not None:
            self.active["text"] += data
    def handle_endtag(self, tag):
        if tag == "a" and self.active is not None:
            self.anchors.append(self.active); self.active = None

class Sections(HTMLParser):
    """Split a web page's <main> content into titled sections, one per <h1>/<h2>/<summary>."""
    def __init__(self):
        super().__init__(); self.sections = []; self.main = 0; self.skip = 0; self.head = None
    def add(self, text):
        if not self.sections:
            self.sections.append({"title": "", "text": ""})
        self.sections[-1]["text"] += text
    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.main += 1
        if not self.main:
            return
        if tag in ("script", "style", "nav", "form"):
            self.skip += 1
        elif tag in ("h1", "h2", "summary"):
            self.head = ""
        elif tag in ("td", "th"):
            self.add(" | ")
        elif tag in ("p", "li", "tr", "br", "div", "h3", "h4", "dt", "dd"):
            self.add("\n")
    def handle_endtag(self, tag):
        if tag == "main":
            self.main -= 1
        elif tag in ("script", "style", "nav", "form") and self.skip:
            self.skip -= 1
        elif tag in ("h1", "h2", "summary") and self.head is not None:
            title = " ".join(self.head.split())
            self.sections.append({"title": title, "text": title + "\n"}); self.head = None
    def handle_data(self, data):
        if not self.main or self.skip:
            return
        if self.head is not None:
            self.head += data
        else:
            self.add(data)

# Collection steps in run order, and which step owns the documents of each source (used by --only).
STEPS = ["ema", "ema_documents", "purple", "fda", "clinical_trials", "fda_reviews", "fda_letters", "pmda", "health_canada", "guidelines"]
STEP_OF_SOURCE = {"EMA": "ema", "ClinicalTrials.gov": "clinical_trials", "FDA": "fda_reviews", "PMDA": "pmda", "Health Canada": "health_canada"}

def step_of(doc):
    # EMA documents come from two steps, so those from ema_documents carry their step explicitly.
    return doc.get("step") or STEP_OF_SOURCE[doc["source"]]

def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()] if path.exists() else []

def plain(fragment):
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())

KINDS = json.loads((PROJECT / "configs/collection/document_kinds.json").read_text(encoding="utf-8"))
PURPLE_FIELDS = ["BLA Number", "License Type", "Submission Type", "Supplement Number", "Approval Date", "Inter. Approval Date",
                 "First Interchangeable Exclusivity Exp. Date", "Ref. Product Proprietary Name", "Marketing Status"]

def classify(doc):
    """What the document is, next to its role: job across agencies, who wrote it, search tier (document_kinds.json)."""
    kind = KINDS["kinds"].get(doc["source"], {}).get(doc["document_type"])
    family, author, tier = kind or ("unclassified", "unknown", "record")
    doc.update(family=family, author=author, tier=tier, default_search=KINDS["tiers"][tier]["default_search"])
    return doc

def landing_status(seed, page):
    """What a guideline's landing page says about its status and dates; fields the page does not show are left None."""
    text = plain(page)
    if seed["source"] == "FDA":
        issued = re.search(r"([A-Z][a-z]+ \d{4}) Download the (?:Draft |Final )?Guidance", text) or re.search(r"Guidance for Industry\s+([A-Z][a-z]+ \d{4})", text)
        docket = re.search(r"Docket Number:\s*(\S+)", text)
        current = re.search(r"Content current as of:\s*([\d/]+)", text)
        return {"status": "draft (not for implementation)" if "Not for implementation" in text else "final",
                "issued": issued and issued[1], "docket": docket and docket[1], "page_current_as_of": current and current[1]}
    if seed["source"] == "EMA":
        # A landing page lists every version (concept paper, draft, adopted). The collected PDF's entry reads
        # "<status> Reference Number: <ref> English (EN) (.. PDF) First published: <date>", then a document-history link to the same PDF.
        at = page.find(seed["url"].split("ema.europa.eu", 1)[1])
        if at < 0:
            return {"status": None, "note": "collected PDF not linked from the landing page"}
        before, after = plain(page[max(0, at - 4000):at]), plain(page[at:at + 1500])
        refs = list(re.finditer(r"(Adopted|Draft[^:]{0,40}?|Superseded|Withdrawn)?\s*Reference Number:\s*(.+?)(?=\s+(?:English|Legal effective date|Summary|First published)\b|$)", before))
        published = (re.findall(r"First published:\s*([\d/]+)", before) or re.findall(r"First published:\s*([\d/]+)", after) or [None])[-1]
        effective = (re.findall(r"Legal effective date:\s*([\d/]+)", before) or [None])[-1]
        status = refs and (refs[-1][1] or "").strip() or ("current version" if "(current version)" in after[:300] else None)
        return {"status": status, "reference_number": refs and refs[-1][2], "first_published": published, "legal_effective_date": effective}
    if seed["source"] == "Health Canada":
        issued = re.search(r'name="dcterms\.issued"[^>]*content="([\d-]+)"', page)
        modified = re.search(r'name="dcterms\.modified"[^>]*content="([\d-]+)"', page)
        return {"status": "published guidance", "page_issued": issued and issued[1], "date_modified": modified and modified[1]}
    return {}

class Collector:
    def __init__(self, offline=False, ocr=False, only=None, refresh=False, work=None):
        self.offline = offline
        # Where steps leave the lists the next step reads (EMA documents per product, Drugs@FDA rows, Purple Book rows, guideline
        # records). The project default is data/catalog; ingestion/tasks.py gives each task its own folder.
        self.work = Path(work) if work else ROOT / "data/catalog"
        self.ocr = ocr
        self.only = only
        self.refresh = refresh
        # Raw bytes of documents already in the catalog are reused, so their version_id (and the OCR, evidence units and
        # chunks keyed by it) does not change on a re-run; --refresh takes the current bytes. Lists and indexes are always fetched.
        self.kept_versions = {d["version_id"] for d in read_jsonl(ROOT / "data/catalog/documents.jsonl")}
        self.licensure = {}
        self.study_results = []
        self.review_sample_pages = {}
        self.run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.cache_file = ROOT / "data/catalog/download_cache.json"
        self.cache = json.loads(self.cache_file.read_text(encoding="utf-8")) if self.cache_file.exists() else {}
        self.downloads = []
        self.errors = []
        self.documents = []
        self.products = []
        self.studies = []
        self.pages = []
        self.sample_tables = []
        self.metrics = {"schema_version": SCHEMA_VERSION, "run_id": self.run_id, "started_at": now(), "offline": offline}

    def fetch(self, url, kind, source, parent_url=None):
        # All raw bytes are content addressed; cache is URL -> latest observed version.
        cached = self.cache.get(url)
        keep = not self.refresh and cached and cached["sha256"] in self.kept_versions and (ROOT / cached["raw_path"]).exists()
        if self.offline or keep:
            if not cached or not (ROOT / cached["raw_path"]).exists():
                raise RuntimeError(f"No offline snapshot: {url}")
            record = dict(cached, cache_reused=True, run_id=cached.get("run_id") if keep and not self.offline else self.run_id)
            body = (ROOT / record["raw_path"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != record["sha256"]:
                raise RuntimeError(f"Snapshot checksum mismatch: {url}")
        else:
            last_error = None
            for attempt in range(3):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "BiosimilarCollectionPilot/0.1 (public regulatory research)"})
                    with urllib.request.urlopen(req, timeout=40) as response:
                        body = response.read()
                        headers = dict(response.headers)
                        final_url = response.url
                        status = response.status
                    if kind == "pdf" and not body.startswith(b"%PDF-"):
                        raise ValueError("HTTP response is not a PDF")
                    if kind == "zip" and not zipfile.is_zipfile(io.BytesIO(body)):
                        raise ValueError("HTTP response is not a ZIP archive")
                    digest = hashlib.sha256(body).hexdigest()
                    ext = {"pdf":"pdf", "json":"json", "csv":"csv", "zip":"zip", "html":"html"}.get(kind, "bin")
                    rel = f"data/raw/{digest}.{ext}"
                    path = ROOT / rel
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        path.write_bytes(body)
                    record = {
                        "url": url, "final_url": final_url, "source": source, "kind": kind,
                        "parent_url": parent_url, "retrieved_at": now(), "http_status": status,
                        "content_type": headers.get("Content-Type"), "etag": headers.get("ETag"),
                        "last_modified": headers.get("Last-Modified"), "sha256": digest,
                        "bytes": len(body), "raw_path": rel, "run_id": self.run_id,
                        "cache_reused": False,
                    }
                    if source == "EMA":
                        record["reuse_policy_url"] = "https://www.ema.europa.eu/en/about-us/about-website/legal-notice"
                    elif source in {"FDA", "Purple Book"}:
                        record["reuse_policy_url"] = "https://www.fda.gov/about-fda/about-website/website-policies"
                    else:
                        record["reuse_policy_url"] = None
                    self.cache[url] = record
                    write_json(self.cache_file, self.cache)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        raise  # absent file, not a transient failure
                    last_error = exc
                    if attempt < 2:
                        time.sleep(attempt + 1)
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(attempt + 1)
            else:
                raise last_error
        self.downloads.append(record)
        return body, record

    def attempt(self, label, fn):
        try:
            return fn()
        except Exception as exc:
            error = {"label": label, "error_type": type(exc).__name__, "message": str(exc), "at": now()}
            self.errors.append(error)
            print("FAILED", label, type(exc).__name__, str(exc)[:160], flush=True)
            return None

    def ema(self, cfg):
        products_bytes, _ = self.fetch(cfg["sources"]["ema_products"], "json", "EMA")
        documents_bytes, _ = self.fetch(cfg["sources"]["ema_documents"], "json", "EMA")
        products = json.loads(products_bytes)
        documents = json.loads(documents_bytes)
        self.metrics["ema_metadata"] = {"products": products["meta"], "documents": documents["meta"]}
        biosimilars = [p for p in products["data"] if p.get("category") == "Human" and p.get("biosimilar") == "Yes" and p.get("medicine_status") == "Authorised"]
        write_jsonl(self.work / "ema_authorised_biosimilars.jsonl", biosimilars)
        self.metrics["ema_authorised_biosimilar_products"] = len({p["ema_product_number"] for p in biosimilars})
        def one(target):
            matches = [p for p in products["data"] if norm(p["name_of_medicine"]) == norm(target["ema_name"])]
            if len(matches) != 1:
                raise ValueError(f"Ambiguous EMA product identity: {target['ema_name']} ({len(matches)})")
            p = matches[0]
            if norm(p.get("active_substance")) != norm(target.get("ingredient") or cfg["ingredient"]):
                raise ValueError("Unexpected active substance")
            self.products.append(dict(target, ema_product_number=p["ema_product_number"], ema_metadata=p))
            related = [d for d in documents["data"] if d.get("ema_product_number") == p["ema_product_number"]]
            write_jsonl(self.work / f"ema_documents_{target['product_id']}.jsonl", related)
            assessments = [d for d in related if d.get("type") == "assessment-report" and "public assessment report" in d.get("name", "").lower()]
            if len(assessments) != 1:
                raise ValueError(f"Expected one initial public assessment report for {target['ema_name']}; found {len(assessments)}")
            meta = assessments[0]
            result = self.attempt(f"EMA PDF {target['product_id']}", lambda: self.fetch(meta["document_url"], "pdf", "EMA", cfg["sources"]["ema_documents"]))
            if result:
                _, raw = result
                self.documents.append({"document_id": "EMA:" + str(meta["id"]), "version_id": raw["sha256"], "product_id": target["product_id"], "source": "EMA", "role": "reference_assessment", "document_type": meta["type"], "title": meta["name"], "source_metadata": meta, **raw})
                print("EMA PDF", target["ema_name"], raw["bytes"], "bytes", flush=True)
        for target in cfg["products"]:
            if target.get("ema_name"):  # one product's failure is recorded and does not stop the others
                self.attempt(f"EMA {target['product_id']}", lambda t=target: one(t))

    def ema_documents(self, cfg):
        # The other EPAR documents of each target product (product information, post-authorisation steps, RMP, overview),
        # taken from the per-product lists that ema() writes. The initial assessment report stays with ema().
        roles = cfg["ema_document_roles"]
        for target in cfg["products"]:
            if not target.get("ema_name"):
                continue
            listed = [json.loads(line) for line in (self.work / f"ema_documents_{target['product_id']}.jsonl").read_text(encoding="utf-8").split("\n") if line.strip()]
            found = []
            for meta in listed:
                if meta.get("type") not in roles:
                    continue
                if norm(target["ema_name"]) not in norm(meta.get("name", "")):
                    raise ValueError(f"EMA document does not name the target product: {meta.get('id')}")
                result = self.attempt(f"EMA document {meta['id']}", lambda: self.fetch(meta["document_url"], "pdf", "EMA", cfg["sources"]["ema_documents"]))
                time.sleep(0.5)
                if result:
                    self.documents.append({"document_id": "EMA:" + str(meta["id"]), "version_id": result[1]["sha256"], "product_id": target["product_id"], "source": "EMA", "step": "ema_documents", "role": roles[meta["type"]], "document_type": meta["type"], "title": meta["name"], "source_metadata": meta, **result[1]})
                    found.append(meta["type"])
            print("EMA documents", target["ema_name"], found, flush=True)

    def purple(self, cfg):
        page, _ = self.fetch(cfg["sources"]["purple_downloads"], "html", "Purple Book")
        parser = Links(); parser.feed(page.decode("utf-8", errors="replace"))
        months = {m.lower(): i for i, m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"], 1)}
        dated = []
        for href in parser.links:
            match = re.search(r"/PurpleBook/(\d{4})/purplebook-search-([A-Za-z]+)-data-download\.csv", href, re.I)
            if match and match[2].lower() in months:
                dated.append((int(match[1]), months[match[2].lower()], urllib.parse.urljoin(cfg["sources"]["purple_downloads"], href)))
        if not dated:
            raise ValueError("No official monthly CSV links found")
        year, month, url = max(dated)
        data, raw = self.fetch(url, "csv", "Purple Book", cfg["sources"]["purple_downloads"])
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
        headers = [i for i, row in enumerate(rows) if any(norm(c) == "blanumber" for c in row) and any(norm(c) == "proprietaryname" for c in row)]
        if len(headers) != 2:
            raise ValueError(f"Purple Book layout changed: expected 2 section headers, got {headers}")
        def section(start, end):
            header = rows[start]
            result = []
            bla_idx = next(i for i, value in enumerate(header) if norm(value) == "blanumber")
            for row_number, row in enumerate(rows[start+1:end], start+2):
                if len(row) != len(header) or not re.fullmatch(r"\d+", row[bla_idx].strip()):
                    continue
                result.append({**dict(zip(header, row)), "source_csv_row": row_number})
            return result
        changes = section(headers[0], headers[1]); full = section(headers[1], len(rows))
        def selected(row):
            return any(t.get("us_brand") and norm(str(row.get("Proprietary Name", ""))) == norm(t["us_brand"]) for t in cfg["products"])
        # Preserve source columns; no row-level dedup of strengths/presentations.
        full_selected = [r for r in full if selected(r)]
        changes_selected = [r for r in changes if selected(r)]
        write_jsonl(self.work / "purple_full_selected.jsonl", full_selected)
        write_jsonl(self.work / "purple_changes_selected.jsonl", changes_selected)
        self.metrics["purple_book"] = {"source_month": f"{year}-{month:02}", "url": url, "sha256": raw["sha256"], "header_rows_1based": [h+1 for h in headers], "full_rows": len(full), "change_rows": len(changes), "selected_full_rows": len(full_selected), "selected_change_rows": len(changes_selected)}
        # Per product, one entry per licensure event (strength/presentation rows collapsed), kept on the product record.
        for t in cfg["products"]:
            rows = [r for r in full_selected if t.get("us_brand") and norm(r.get("Proprietary Name", "")) == norm(t["us_brand"])]
            if rows:
                events = [json.loads(e) for e in dict.fromkeys(json.dumps({k: r.get(k, "").strip() for k in PURPLE_FIELDS}) for r in rows)]
                self.licensure[t["product_id"]] = {
                    "us_licensure": {"source": "Purple Book", "source_month": f"{year}-{month:02}", "interchangeable": any("Interchangeable" in e["License Type"] for e in events), "events": events},
                    "us_proper_name": rows[0]["Proper Name"].strip().lower(),
                    # An originator (351(a)) row has no reference product columns.
                    "reference_product": {"name": rows[0]["Ref. Product Proprietary Name"].strip(), "proper_name": rows[0]["Ref. Product Proper Name"].strip().lower(), "source": "Purple Book"}
                                         if rows[0].get("Ref. Product Proprietary Name", "").strip() else None}
        print("Purple Book", f"{year}-{month:02}", "selected rows", len(full_selected), flush=True)

    def fda(self, cfg):
        page, _ = self.fetch(cfg["sources"]["fda_downloads"], "html", "FDA")
        parser = Links(); parser.feed(page.decode("utf-8", errors="replace"))
        urls = [urllib.parse.urljoin(cfg["sources"]["fda_downloads"], a["href"]) for a in parser.anchors if "drugsfdadownloadfile" in norm(a["text"]) or "drugsfdadatafile" == norm(a["title"])]
        if len(set(urls)) != 1:
            raise ValueError(f"Ambiguous Drugs@FDA ZIP link: {urls}")
        body, raw = self.fetch(urls[0], "zip", "FDA", cfg["sources"]["fda_downloads"])
        archive = zipfile.ZipFile(io.BytesIO(body))
        def read_table(name):
            filename = next(x for x in archive.namelist() if Path(x).name.lower() == name.lower() + ".txt")
            text = archive.read(filename).decode("utf-8-sig", errors="replace")
            return list(csv.DictReader(io.StringIO(text), delimiter="\t"))
        products = read_table("Products")
        selected = [p for p in products if any(t.get("us_brand") and norm(p.get("DrugName", "")) == norm(t["us_brand"]) for t in cfg["products"])]
        applications = {p["ApplNo"] for p in selected}
        docs = [d for d in read_table("ApplicationDocs") if d.get("ApplNo") in applications]
        write_jsonl(self.work / "fda_products_selected.jsonl", selected)
        write_jsonl(self.work / "fda_application_docs_selected.jsonl", docs)
        self.metrics["drugs_at_fda"] = {"zip_url": urls[0], "sha256": raw["sha256"], "selected_product_rows": len(selected), "application_numbers": sorted(applications), "document_link_rows": len(docs), "body_downloaded": False}
        print("Drugs@FDA", len(applications), "applications;", len(docs), "document-link rows", flush=True)

    def clinical_trials(self, cfg):
        for target in cfg["products"]:
            for nct in target["study_ids"]:
                self.attempt(f"CT.gov {nct}", lambda t=target, n=nct: self.clinical_trial(t, n))
        # studies.jsonl is written by finish_catalog, merged with the studies of products not collected in this run.

    def clinical_trial(self, target, nct):
        body, raw = self.fetch(f"https://clinicaltrials.gov/api/v2/studies/{nct}", "json", "ClinicalTrials.gov")
        study = json.loads(body)
        ps = study["protocolSection"]
        ident = ps["identificationModule"]
        if ident["nctId"] != nct:
            raise ValueError("ClinicalTrials.gov ID mismatch")
        # Check the actual title/interventions/summary, not a fuzzy full-record match.
        identity_text = json.dumps({"identification": ident, "interventions": ps.get("armsInterventionsModule"), "description": ps.get("descriptionModule")}, ensure_ascii=False)
        if not any(norm(alias) in norm(identity_text) for alias in target["aliases"]):
            raise ValueError(f"Study does not identify target product: {nct}")
        docs = study.get("documentSection", {}).get("largeDocumentModule", {}).get("largeDocs", [])
        row = {"product_id": target["product_id"], "nct_id": nct, "org_study_id": ident.get("orgStudyIdInfo"), "brief_title": ident.get("briefTitle"), "phases": ps.get("designModule", {}).get("phases", []), "enrollment": ps.get("designModule", {}).get("enrollmentInfo"), "large_docs": docs, "protocol_attachment_status": "listed" if any(d.get("hasProtocol") for d in docs) else "not_listed_in_api_snapshot", "has_results": bool(study.get("hasResults")), "api_source": raw}
        self.studies.append(row)
        # Posted results: one row per outcome measure, numbers as ClinicalTrials.gov gives them. Sponsor-submitted, not regulator-reviewed.
        for i, m in enumerate(study.get("resultsSection", {}).get("outcomeMeasuresModule", {}).get("outcomeMeasures", []), 1):
            self.study_results.append({"product_id": target["product_id"], "nct_id": nct, "phases": row["phases"], "outcome_index": i,
                                       "author": "sponsor", "review_status": "posted_results_not_regulator_reviewed",
                                       **{k: m.get(k) for k in ("type", "title", "description", "populationDescription", "timeFrame", "paramType", "dispersionType", "unitOfMeasure", "reportingStatus")},
                                       "groups": m.get("groups", []), "denoms": m.get("denoms", []), "classes": m.get("classes", []), "analyses": m.get("analyses", []),
                                       "source": {"url": raw["url"], "sha256": raw["sha256"], "retrieved_at": raw["retrieved_at"]}})
        for doc in docs:
            if not (doc.get("hasProtocol") or doc.get("hasSap") or "protocol" in doc.get("label", "").lower() or "statistical" in doc.get("label", "").lower()):
                continue
            filename = doc["filename"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.pdf", filename):
                raise ValueError("Unexpected attachment filename")
            # API lists filename. Resolve a candidate in the official CDN namespace;
            # accept only after HTTP/PDF validation. Resolution method is explicit.
            url = f"https://cdn.clinicaltrials.gov/large-docs/{nct[-2:]}/{nct}/{filename}"
            pdf = self.attempt(f"CT.gov PDF {nct} {filename}", lambda: self.fetch(url, "pdf", "ClinicalTrials.gov", raw["url"]))
            if pdf:
                _, pdf_raw = pdf
                has_protocol = doc.get("hasProtocol") or "protocol" in doc.get("label", "").lower()
                self.documents.append({"document_id": f"CTG:{nct}:{filename}", "version_id": pdf_raw["sha256"], "product_id": target["product_id"], "nct_id": nct, "source": "ClinicalTrials.gov", "role": "reference_protocol" if has_protocol else "reference_sap", "document_type": doc.get("label"), "title": f"{nct} — {doc.get('label',filename)}", "source_metadata": doc, "url_resolution": "api_filename_official_cdn_candidate_http_pdf_verified", **pdf_raw})
        print("CT.gov", nct, row["phases"], "attachments", len(docs), "results", row["has_results"], flush=True)

    def fda_reviews(self, cfg):
        # Review PDFs are not in the Drugs@FDA ZIP. Each approval-package page builds its links in JavaScript from a
        # base name plus a fixed suffix per document type, so every listed suffix is tried; 404 means "not in this package".
        products = [json.loads(line) for line in (self.work / "fda_products_selected.jsonl").read_text(encoding="utf-8").split("\n") if line.strip()]
        rows = [json.loads(line) for line in (self.work / "fda_application_docs_selected.jsonl").read_text(encoding="utf-8").split("\n") if line.strip()]
        target_of = {p["ApplNo"]: t for p in products for t in cfg["products"] if t.get("us_brand") and norm(p["DrugName"]) == norm(t["us_brand"])}
        roles = cfg["fda_review_roles"]
        def add(target, document_id, label, suffix, row, raw, package=None):
            self.documents.append({"document_id": document_id, "version_id": raw["sha256"], "product_id": target["product_id"], "source": "FDA", "role": roles.get(suffix, roles["default"]), "document_type": label, "title": f"{target['us_brand']} (BLA {row['ApplNo']} {row['SubmissionType'].strip()}-{row['SubmissionNo']}) — {label}", "source_metadata": {"application_doc": row, "package": package, "suffix": suffix}, **raw})
        fetched = set()  # PDF URLs taken in this step: a package file also listed as its own row (2009 SumR) is taken once
        def package(url, row, target):
            page = self.fetch(url, "html", "FDA", cfg["sources"]["fda_downloads"])
            text = page[0].decode("utf-8", errors="replace")
            title = re.search(r"<title>(.*?)</title>", text, re.S)
            if not title or norm(target["us_brand"]) not in norm(title[1]):
                raise ValueError("Package page does not identify the target product")
            folder = url.rsplit("/", 1)[0] + "/"
            base = re.search(r'pdfBaseName\s*=\s*"([^"]+)"', text)
            if base:  # current layout: base name + one fixed suffix per document type
                items = [(base[1], suffix, label, base[1] + suffix + ".pdf") for suffix, label in re.findall(r"pdfBaseName \+ '(\w+)\.pdf[^>]*>([^<]+)</a>", text)]
            else:
                # Older layout (2009-2016 packages): a pdfFiles flag per type and a pdfFilenames map; each link is built with
                # getPdfFilename('<key>', '<Suffix>.pdf') followed by the link label. A value may be a list of part files.
                flags = {k.lower(): v for k, v in re.findall(r"(\w+)\s*:\s*([01])\b", text[text.find("pdfFiles"):text.find("pdfFilenames")])}
                names = dict(re.findall(r'(\w+)\s*:\s*"([^"]+\.pdf)"', text[text.find("pdfFilenames"):]))
                names.update({k: re.findall(r'"([^"]+\.pdf)"', v) for k, v in re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]", text[text.find("pdfFilenames"):])})
                items = []
                for key, suffix, label in re.findall(r"getPdfFilename\('(\w+)',\s*'(\w+)\.pdf'\).*?>([^<]+)</a>", text, re.S):
                    files = names.get(key) or [suffix + ".pdf"]
                    if flags.get(key) != "1":
                        continue
                    for i, f in enumerate(files if isinstance(files, list) else [files]):
                        prefix = f[:f.lower().rfind(suffix.lower())].rstrip("_") if suffix.lower() in f.lower() else f[:-4]
                        items.append((prefix, suffix + (f"_Part{i + 1}" if isinstance(files, list) and len(files) > 1 else ""), label, f))
            found = []
            for prefix, suffix, label, filename in items:
                if re.sub(r"_Part\d+$", "", suffix) in cfg["fda_review_skip"] or folder + filename in fetched:
                    continue
                try:
                    _, raw = self.fetch(folder + filename, "pdf", "FDA", url)
                except Exception as exc:
                    if not (isinstance(exc, urllib.error.HTTPError) and exc.code == 404):
                        self.errors.append({"label": f"FDA review PDF {folder + filename}", "error_type": type(exc).__name__, "message": str(exc), "at": now()})
                    continue
                fetched.add(folder + filename)
                add(target, f"FDA:{prefix}:{suffix}", label.strip(), re.sub(r"_Part\d+$", "", suffix), row, raw, package=url)
                found.append(suffix)
                time.sleep(0.5)
            print("FDA package", url.rsplit("/", 1)[1], target["us_brand"], found, flush=True)
        def single(url, row, target):
            # A package published as one PDF (older supplements: 125261Orig1s103.pdf), or one review listed on its own.
            name = url.rsplit("/", 1)[1]
            _, raw = self.fetch(url, "pdf", "FDA", cfg["sources"]["fda_downloads"])
            fetched.add(url)
            suffix = (re.search(r"([A-Za-z]+\d?)\.pdf$", name) or [None, "Package"])[1]
            add(target, f"FDA:{name[:-4]}", "Review document", suffix, row, raw)
        seen = set()
        # Package pages first, so a file a package lists is taken under the package's name.
        for row in sorted(rows, key=lambda r: not r["ApplicationDocsURL"].strip().endswith(".html")):
            url = re.sub(r"^http://", "https://", row["ApplicationDocsURL"].strip())
            if "/drugsatfda_docs/nda/" not in url or url in seen or url in fetched or row["ApplNo"] not in target_of:
                continue
            seen.add(url)
            target = target_of[row["ApplNo"]]
            if url.lower().endswith(".pdf"):
                self.attempt(f"FDA review PDF {url.rsplit('/', 1)[1]}", lambda: single(url, row, target))
            else:
                self.attempt(f"FDA package {url}", lambda: package(url, row, target))

    def fda_letters(self, cfg):
        # The review package of an original submission holds its letter and label (fda_reviews). Supplements (new presentation,
        # indication, interchangeability) have only a letter and a label link, and a product without a published package has only these.
        # Rule: every letter outside a package, and the latest listed label (the current one); older supplement labels are not taken.
        products = read_jsonl(self.work / "fda_products_selected.jsonl")
        all_rows = read_jsonl(self.work / "fda_application_docs_selected.jsonl")
        submission = lambda r: (r["ApplNo"], r["SubmissionType"].strip(), r["SubmissionNo"])
        for target in cfg["products"]:
            if not target.get("us_brand"):
                continue
            appls = {p["ApplNo"] for p in products if norm(p["DrugName"]) == norm(target["us_brand"])}
            rows = [r for r in all_rows if r["ApplNo"] in appls]
            packaged = {submission(r) for r in rows if r["ApplicationDocsURL"].strip().endswith("TOC.html")}
            by_name = {}
            for r in rows:
                url = r["ApplicationDocsURL"].strip().split("#")[0].replace("http://", "https://")
                folder = "appletter" if "/appletter/" in url else "label" if "/label/" in url else None
                if folder:
                    by_name.setdefault((folder, url.rsplit("/", 1)[1]), []).append(dict(r, url=url))
            letters = [(name, rs) for (folder, name), rs in by_name.items() if folder == "appletter" and not any(submission(r) in packaged for r in rs)]
            labels = sorted(((name, rs) for (folder, name), rs in by_name.items() if folder == "label"), key=lambda x: max(r["ApplicationDocsDate"] for r in x[1]))
            for (name, rs), label in [(x, "Approval Letter") for x in letters] + ([(labels[-1], "Labeling (latest listed)")] if labels else []):
                # The same file is listed once per BLA; prefer a URL in a dated folder (one row lacks the year).
                url = sorted({r["url"] for r in rs}, key=lambda u: not re.search(r"/(appletter|label)/\d{4}/", u))[0]
                result = self.attempt(f"FDA {label} {name}", lambda: self.fetch(urllib.parse.quote(url, safe=":/;,"), "pdf", "FDA", cfg["sources"]["fda_downloads"]))
                time.sleep(0.5)
                if not result:
                    continue
                subs = sorted({(r["ApplNo"], r["SubmissionType"].strip(), r["SubmissionNo"], r["ApplicationDocsDate"][:10]) for r in rs})
                first, is_label = subs[0], label.startswith("Label")
                self.documents.append({"document_id": f"FDA:{'label' if is_label else 'letter'}:{name[:-4]}", "version_id": result[1]["sha256"],
                                       "product_id": target["product_id"], "source": "FDA", "step": "fda_letters",
                                       "role": "label" if is_label else "regulatory_correspondence", "document_type": label,
                                       "title": f"{target['us_brand']} (BLA {'/'.join(sorted({s[0] for s in subs}))} {first[1]}-{first[2]}, {first[3]}) — {label}",
                                       "source_metadata": {"submissions": [dict(zip(["ApplNo", "SubmissionType", "SubmissionNo", "ApplicationDocsDate"], s)) for s in subs]},
                                       **result[1]})
            print("FDA letters/labels", target["us_brand"], sum(d.get("step") == "fda_letters" and d["product_id"] == target["product_id"] for d in self.documents), flush=True)

    def pmda(self, cfg):
        # Product pages are fixed seeds in targets.json (like the NCT list). A review report is accepted only
        # if its text names the product's development code.
        kinds = [("review_report", "審査報告書", r"https://www\.pmda\.go\.jp/drugs/[^'\"]+\.pdf", "regulatory_review"),
                 ("package_insert", "添付文書", r"\.\./ResultDataSetPDF/\w+", "label"),
                 ("interview_form", "インタビューフォーム", r"https://www\.info\.pmda\.go\.jp/go/interview/[^'\"]+\.pdf", "label")]
        for target in cfg["products"]:
            for list_id in target.get("pmda_general_list_ids", []):
                page_url = cfg["sources"]["pmda_general_list"].format(id=list_id)
                page = self.attempt(f"PMDA product page {list_id}", lambda: self.fetch(page_url, "html", "PMDA"))
                if not page:
                    continue
                text = page[0].decode("utf-8", errors="replace")
                ingredient_ja = target.get("ingredient_ja") or cfg.get("ingredient_ja", "")
                if ingredient_ja not in text:
                    raise ValueError(f"PMDA page does not list {ingredient_ja}: {list_id}")
                for kind, label, pattern, role in kinds:
                    for href in sorted(set(re.findall(pattern, text))):
                        url = urllib.parse.urljoin(page_url, href)
                        name = re.sub(r"\.pdf$", "", url.rsplit("/", 1)[1])
                        result = self.attempt(f"PMDA {kind} {name}", lambda: self.fetch(url, "pdf", "PMDA", page_url))
                        time.sleep(0.5)
                        if not result:
                            continue
                        body, raw = result
                        if kind == "review_report":
                            with fitz.open(stream=body, filetype="pdf") as pdf:
                                report = "".join(p.get_text() for p in pdf)
                            if not any(alias in report for alias in target["aliases"]):
                                self.errors.append({"label": f"PMDA review report {url}", "error_type": "ValueError", "message": "Report does not name the target development code", "at": now()})
                                continue
                        self.documents.append({"document_id": f"PMDA:{list_id}:{kind}:{name}", "version_id": raw["sha256"], "product_id": target["product_id"], "source": "PMDA", "role": role, "document_type": label, "title": f"{target['product_id']} PMDA {label} ({name})", "language": "ja", "source_metadata": {"general_list_id": list_id, "kind": kind}, **raw})
                print("PMDA", target["product_id"], list_id, flush=True)

    def health_canada(self, cfg):
        # Decision documents (SBD, RDS) are web pages found through the portal's ingredient filter;
        # product monographs are PDFs linked from the Drug Product Database.
        listing = cfg["sources"]["hc_review_documents"]
        listed = {}  # ingredient -> {resource id: title}; products of several ingredients each search their own list
        for ingredient in sorted({t.get("ingredient") or cfg["ingredient"] for t in cfg["products"] if t.get("ca_brand")}):
            found = listed.setdefault(ingredient, {})
            for number in range(20):
                url = f"{listing}?f%5B0%5D=medicinal_ingredients%3A{urllib.parse.quote(ingredient.capitalize())}&page={number}"
                body, _ = self.fetch(url, "html", "Health Canada", listing)
                rows = re.findall(r'href="(?:https://dhpp\.hpfb-dgpsa\.ca)?/review-documents/resource/((?:SBD|RDS|SSR)\w+)"[^>]*>(.*?)</a>', body.decode("utf-8", errors="replace"), re.S)
                new = {rid: " ".join(re.sub(r"<[^>]+>", "", title).split()) for rid, title in rows if rid not in found}
                if not new:
                    break
                found.update(new)
                time.sleep(0.5)
        self.metrics["health_canada"] = {"ingredient_documents_listed": {k: len(v) for k, v in listed.items()}}
        types = {"SBD": "Summary Basis of Decision", "RDS": "Regulatory Decision Summary", "SSR": "Summary Safety Review"}
        for target in cfg["products"]:
            if not target.get("ca_brand"):
                continue
            for rid, title in sorted(listed[target.get("ingredient") or cfg["ingredient"]].items()):
                if norm(target["ca_brand"]) not in norm(title):
                    continue
                url = f"{listing}/resource/{rid}"
                result = self.attempt(f"Health Canada {rid}", lambda: self.fetch(url, "html", "Health Canada", listing))
                time.sleep(0.5)
                if result:
                    self.documents.append({"document_id": f"HC:{rid}", "version_id": result[1]["sha256"], "product_id": target["product_id"], "source": "Health Canada", "role": "regulatory_review", "document_type": types[rid[:3]], "title": title, "source_metadata": {"resource_id": rid}, **result[1]})
            api = f"{cfg['sources']['hc_dpd_api']}?brandname={urllib.parse.quote(target['ca_brand'].lower())}&type=json"
            listed = self.attempt(f"Health Canada DPD {target['ca_brand']}", lambda: self.fetch(api, "json", "Health Canada"))
            monographs = {}
            for product in json.loads(listed[0]) if listed else []:
                if norm(target["ca_brand"]) not in norm(product["brand_name"]):
                    continue
                info = f"{cfg['sources']['hc_dpd_info']}?lang=eng&code={product['drug_code']}"
                page = self.attempt(f"Health Canada DPD page {product['drug_code']}", lambda: self.fetch(info, "html", "Health Canada", api))
                time.sleep(0.5)
                for pm in re.findall(r"https://pdf\.hres\.ca/dpd_pm/\w+\.PDF", page[0].decode("utf-8", errors="replace")) if page else []:
                    monographs.setdefault(pm, info)
            for pm, info in sorted(monographs.items()):
                result = self.attempt(f"Health Canada product monograph {pm.rsplit('/', 1)[1]}", lambda: self.fetch(pm, "pdf", "Health Canada", info))
                if result:
                    self.documents.append({"document_id": f"HC:PM:{pm.rsplit('/', 1)[1][:-4]}", "version_id": result[1]["sha256"], "product_id": target["product_id"], "source": "Health Canada", "role": "label", "document_type": "Product Monograph", "title": f"{target['ca_brand']} Product Monograph", "source_metadata": {"dpd_info_page": info}, **result[1]})
            print("Health Canada", target["ca_brand"], sum(d["source"] == "Health Canada" and d["product_id"] == target["product_id"] for d in self.documents), "documents", flush=True)

    def guidelines(self, cfg, ids=None):
        # Biosimilar guidelines (guidelines.json): no product_id, role "guideline". Each keeps the status its landing page shows
        # at collection time (draft/final, adopted, reference number, dates); the seed's own status is used only without a landing page.
        seeds = json.loads((PROJECT / "configs/collection/guidelines.json").read_text(encoding="utf-8"))
        records = []
        for g in [g for g in seeds["guidelines"] if not ids or g["id"] in ids]:
            page = self.attempt(f"guideline landing {g['id']}", lambda: self.fetch(g["landing"], "html", g["source"])) if g.get("landing") else None
            status = landing_status(g, page[0].decode("utf-8", errors="replace")) if page else {"status": g.get("status"), "status_from": "guidelines.json"}
            meta = {"guideline_id": g["id"], "jurisdiction": g["jurisdiction"], "topics": g["topics"], "landing": g.get("landing"), "checked": seeds["checked"], **status}
            parts = [(g["url"], "pdf", "")] if g.get("url") else [(g["landing"].replace(".html", f"/{p}.html") if p else g["landing"], "html", p) for p in g["pages"]]
            for url, kind, part in parts:
                result = self.attempt(f"guideline {g['id']} {part}", lambda: self.fetch(url, kind, g["source"], g.get("landing")))
                time.sleep(0.5)
                if result:
                    self.documents.append({"document_id": f"GL:{g['id']}" + (f":{part}" if part else ""), "version_id": result[1]["sha256"], "product_id": None,
                                           "source": g["source"], "step": "guidelines", "role": "guideline", "document_type": g["document_type"],
                                           "title": g["title"] + (f" — {part}" if part else ""), "source_metadata": meta, **result[1]})
            records.append(dict(meta, title=g["title"], collected=[d["document_id"] for d in self.documents if d["source_metadata"] is meta]))
            print("Guideline", g["id"], {k: v for k, v in status.items() if v}, flush=True)
        done = {r["guideline_id"] for r in records}
        write_jsonl(self.work / "guidelines.jsonl", [r for r in read_jsonl(self.work / "guidelines.jsonl") if r["guideline_id"] not in done] + records)

    def parse_html(self, doc):
        # A web page has no pages: each titled section becomes one row, numbered in document order in the page column.
        parser = Sections(); parser.feed((ROOT / doc["raw_path"]).read_text(encoding="utf-8", errors="replace"))
        rows = []
        for section in parser.sections:
            text = "\n".join(line for line in (" ".join(l.split()) for l in section["text"].splitlines()) if line)
            if text == section["title"]:
                continue  # heading with no body of its own
            rows.append({"document_id": doc["document_id"], "version_id": doc["version_id"], "product_id": doc["product_id"], "role": doc["role"], "pdf_page": len(rows)+1, "pdf_page_label": section["title"], "locator_type": "html_section", "source_url": doc["url"], "text": text, "blocks": [], "image_regions": [], "possible_image_table": False, "parser": "html.parser", "parser_version": "stdlib", "schema_version": SCHEMA_VERSION})
        return rows

    def prepare_text_layer(self, doc, path):
        # Fonts without /ToUnicode are repaired into a copy that all text extraction reads; OCR still renders the original.
        repaired = ROOT / f"data/repaired/{doc['version_id']}.pdf"
        fonts = text_layer.repair(path, repaired)
        text_path = repaired if any(f["status"] == "repaired" for f in fonts) else path
        doc["text_path"] = text_path.relative_to(ROOT).as_posix()
        doc["text_layer"] = {"version": text_layer.VERSION, "fonts": fonts}
        return text_path

    PARSED_FIELDS = ["page_count", "nonempty_text_pages", "possible_image_table_pages", "parser", "parser_version", "processing_status",
                     "parsed_path", "text_path", "text_layer", "pk_candidate_pages"]

    def parse(self, reparse=False):
        # A document version already parsed keeps its parsed file and catalog fields (what OCR, evidence units and chunks were built from);
        # reparse() is the explicit way to parse stored versions again.
        parsed = {(d["document_id"], d["version_id"]): d for d in read_jsonl(ROOT / "data/catalog/documents.jsonl")}
        for doc in self.documents:
            old = parsed.get((doc["document_id"], doc["version_id"]))
            if not reparse and old and old.get("parsed_path") and (ROOT / old["parsed_path"]).exists():
                doc.update({k: old[k] for k in self.PARSED_FIELDS if k in old})
                continue
            path = ROOT / doc["raw_path"]
            slug = re.sub(r"[^a-zA-Z0-9_-]", "_", doc["document_id"])
            if doc["kind"] == "html":
                page_rows = self.parse_html(doc)
                self.pages.extend(page_rows)
                write_jsonl(ROOT / f"data/parsed/{slug}.pages.jsonl", page_rows)
                doc.update({"page_count": len(page_rows), "nonempty_text_pages": len(page_rows), "possible_image_table_pages": [], "parser": "html.parser", "parser_version": "stdlib", "processing_status": "parsed_unreviewed", "parsed_path": f"data/parsed/{slug}.pages.jsonl"})
                print("Parsed", doc["document_id"], len(page_rows), "sections", flush=True)
                continue
            text_path = self.prepare_text_layer(doc, path)
            pdf = fitz.open(text_path)
            if pdf.is_encrypted:
                raise ValueError("Encrypted PDF")
            page_rows = []
            for idx, page in enumerate(pdf):
                check, replace = text_layer.inspect(page)
                raw_text = page.get_text("text", sort=True)
                text = text_layer.normalize(raw_text, replace)
                check["private_use_left"] = text_layer.private_use_count(text)
                check["private_use_replaced"] = text_layer.private_use_count(raw_text) - check["private_use_left"]
                blocks = [{"block_id": f"{doc['version_id']}:{idx+1}:{b[5]}", "bbox": list(b[:4]), "text": text_layer.normalize(b[4], replace)} for b in page.get_text("blocks", sort=True) if b[6] == 0]
                image_regions = [{"bbox": list(im["bbox"]), "width": im["width"], "height": im["height"]} for im in page.get_image_info()]
                row = {"document_id": doc["document_id"], "version_id": doc["version_id"], "product_id": doc["product_id"], "role": doc["role"], "pdf_page": idx+1, "pdf_page_label": page.get_label() or str(idx+1), "source_url": doc["url"], "text": text, "blocks": blocks, "image_regions": image_regions, "possible_image_table": bool(image_regions and re.search(r"\bTable\s+\d", text)), "text_layer": check, "parser": "PyMuPDF", "parser_version": fitz.VersionBind, "schema_version": SCHEMA_VERSION}
                self.pages.append(row); page_rows.append(row)
            pdf.close()
            doc["text_layer"]["pages_needing_ocr"] = [p["pdf_page"] for p in page_rows if p["text_layer"]["needs_ocr"]]
            doc["text_layer"]["private_use_replaced"] = sum(p["text_layer"]["private_use_replaced"] for p in page_rows)
            write_jsonl(ROOT / f"data/parsed/{slug}.pages.jsonl", page_rows)
            doc["page_count"] = len(page_rows)
            doc["nonempty_text_pages"] = sum(bool(p["text"].strip()) for p in page_rows)
            doc["possible_image_table_pages"] = [p["pdf_page"] for p in page_rows if p["possible_image_table"]]
            doc["parser"] = "PyMuPDF"; doc["parser_version"] = fitz.VersionBind
            doc["processing_status"] = "parsed_unreviewed"
            doc["parsed_path"] = f"data/parsed/{slug}.pages.jsonl"
            # Second representation for inspection of layout; no claim of semantic accuracy.
            layout_path = ROOT / f"data/parsed/{slug}.layout.txt"
            subprocess.run(["pdftotext", "-layout", str(text_path), str(layout_path)], check=True, capture_output=True)
            if doc["role"] == "reference_assessment":
                self.extract_sample_tables(doc, page_rows)
            print("Parsed", doc["document_id"], len(page_rows), "pages", flush=True)

    def extract_sample_tables(self, doc, page_rows):
        def score(row):
            if row["pdf_page"] < 10:
                return -1
            text = row["text"].lower()
            if not ("auc" in text and ("cmax" in text or "c max" in text)):
                return -1
            return (5 * bool(re.search(r"90\s*%", text)) + 4 * ("geometric" in text) + 3 * ("ratio" in text) + 2 * ("confidence" in text) + 2 * bool(re.search(r"table\s+\d", text)))
        ranked = sorted(page_rows, key=lambda row: (-score(row), row["pdf_page"]))
        candidates = [r for r in ranked if score(r) >= 0][:3]
        doc["pk_candidate_pages"] = [{"pdf_page": r["pdf_page"], "score": score(r)} for r in candidates]
        selected_pages = {r["pdf_page"] for r in candidates}
        for row in page_rows:
            if row["pdf_page"] in self.review_sample_pages.get(doc["product_id"], []) and row["pdf_page"] not in selected_pages:
                candidates.append(row)
        with pdfplumber.open(ROOT / doc["text_path"]) as pdf:
            for row in candidates:
                page = pdf.pages[row["pdf_page"]-1]
                all_tables = []
                for method, settings in [("lines", {}), ("text", {"vertical_strategy":"text", "horizontal_strategy":"text", "min_words_vertical":2})]:
                    for index, table in enumerate(page.find_tables(settings)):
                        grid = table.extract()
                        if not grid:
                            continue
                        table_record = {"document_id": doc["document_id"], "version_id": doc["version_id"], "product_id": doc["product_id"], "pdf_page": row["pdf_page"], "source_url": doc["url"], "method": method, "table_index": index, "bbox": list(table.bbox), "cells": grid, "processing_status": "candidate_unreviewed", "parser": "pdfplumber", "parser_version": pdfplumber.__version__}
                        all_tables.append(table_record); self.sample_tables.append(table_record)
                write_json(ROOT / f"data/tables/{doc['product_id']}_p{row['pdf_page']:03}.json", {"page_text": row["text"], "tables": all_tables})

    def product_records(self, cfg):
        """One record per target: its targets.json fields, EMA identity, ingredient, US proper name, reference product and Purple Book
        licensure. A value no step produced in this run is taken from the previous record; products outside cfg are kept."""
        old = {p["product_id"]: p for p in read_jsonl(ROOT / "data/catalog/products.jsonl")}
        ema = {p["product_id"]: p for p in self.products}
        records = []
        for t in cfg["products"]:
            prev = old.pop(t["product_id"], {})
            rec = dict(prev, **t)
            rec["ema_product_number"] = ema.get(t["product_id"], {}).get("ema_product_number") or prev.get("ema_product_number")
            rec["ema_metadata"] = ema.get(t["product_id"], {}).get("ema_metadata") or prev.get("ema_metadata")
            rec["ingredient"] = t.get("ingredient") or cfg["ingredient"]
            rec["kind"] = t.get("kind") or "biosimilar"  # ingredient -> reference product (originator) -> biosimilars
            rec.update(self.licensure.get(t["product_id"]) or {k: prev.get(k) for k in ("us_licensure", "us_proper_name", "reference_product")})
            if rec["kind"] == "reference":
                rec["reference_product"] = rec["reference_product_id"] = None
            else:
                # The biosimilar points at its reference product's own record; the Purple Book name stays when there is one.
                ref = next((r for r in cfg["products"] if r["product_id"] == t.get("reference_product_id")), None)
                rec["reference_product_id"] = t.get("reference_product_id")
                if ref:
                    rec["reference_product"] = dict(rec.get("reference_product") or {}, product_id=ref["product_id"],
                                                    name=(rec.get("reference_product") or {}).get("name") or ref.get("ema_name") or ref.get("us_brand"))
                    rec["reference_product"].setdefault("source", "targets.json")
            records.append(rec)
        return records + list(old.values())

    def finish_catalog(self, cfg):
        """Parse what this run fetched and write the catalog files; returns the full document list."""
        self.parse()
        existing = read_jsonl(ROOT / "data/catalog/documents.jsonl")
        for d in self.documents:
            d["last_seen_run"] = self.run_id
        # A document already in the catalog stays when this run did not produce it again: its step did not run (--only), the
        # source no longer lists it, or the download failed. It is never dropped silently; last_seen_run tells when it was last listed.
        produced = {d["document_id"] for d in self.documents}
        order = {d["document_id"]: i for i, d in enumerate(existing)}
        docs = sorted([d for d in existing if d["document_id"] not in produced] + self.documents, key=lambda d: order.get(d["document_id"], len(order)))
        products = self.product_records(cfg)
        ingredient = {p["product_id"]: p["ingredient"] for p in products}
        kind = {p["product_id"]: p.get("kind") or "biosimilar" for p in products}
        for d in docs:
            classify(d)
            d["ingredient"] = ingredient.get(d.get("product_id"))
            d["product_kind"] = kind.get(d.get("product_id"))  # reference / biosimilar; None for guidelines
        write_jsonl(ROOT / "data/catalog/documents.jsonl", docs)
        write_jsonl(ROOT / "data/catalog/products.jsonl", products)
        refreshed = {s["nct_id"] for s in self.studies}
        studies = [s for s in read_jsonl(ROOT / "data/catalog/studies.jsonl") if s["nct_id"] not in refreshed] + self.studies
        write_jsonl(ROOT / "data/catalog/studies.jsonl", studies)
        results = [r for r in read_jsonl(ROOT / "data/catalog/study_results.jsonl") if r["nct_id"] not in refreshed] + self.study_results
        write_jsonl(ROOT / "data/catalog/study_results.jsonl", results)
        return docs

    def database(self):
        # Loads the catalog files as written (documents, products, studies, posted results) and the pages parsed in this run.
        docs = read_jsonl(ROOT / "data/catalog/documents.jsonl")
        path = ROOT / "data/collection.sqlite"
        conn = sqlite3.connect(path)
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS products(product_id TEXT PRIMARY KEY, ema_product_number TEXT, metadata_json TEXT);
        CREATE TABLE IF NOT EXISTS studies(nct_id TEXT PRIMARY KEY, product_id TEXT, metadata_json TEXT);
        CREATE TABLE IF NOT EXISTS study_results(nct_id TEXT, outcome_index INTEGER, product_id TEXT, title TEXT, metadata_json TEXT, PRIMARY KEY(nct_id, outcome_index));
        CREATE TABLE IF NOT EXISTS document_versions(document_id TEXT, version_id TEXT, product_id TEXT, source TEXT, role TEXT, title TEXT, source_url TEXT, raw_path TEXT, page_count INTEGER, metadata_json TEXT, PRIMARY KEY(document_id,version_id));
        CREATE TABLE IF NOT EXISTS pages(document_id TEXT, version_id TEXT, pdf_page INTEGER, product_id TEXT, role TEXT, text TEXT, metadata_json TEXT, PRIMARY KEY(document_id,version_id,pdf_page));
        CREATE TABLE IF NOT EXISTS ingestion_runs(run_id TEXT PRIMARY KEY, metrics_json TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS page_search USING fts5(document_id UNINDEXED, version_id UNINDEXED, pdf_page UNINDEXED, product_id UNINDEXED, role UNINDEXED, text);
        """)
        # products and studies mirror the catalog files: rows the catalog no longer has are removed.
        conn.execute("DELETE FROM products")
        for p in read_jsonl(ROOT / "data/catalog/products.jsonl"):
            conn.execute("INSERT OR REPLACE INTO products VALUES (?,?,?)", (p["product_id"],p.get("ema_product_number"),json.dumps(p,ensure_ascii=False)))
        conn.execute("DELETE FROM studies")
        for s in read_jsonl(ROOT / "data/catalog/studies.jsonl"):
            conn.execute("INSERT OR REPLACE INTO studies VALUES (?,?,?)", (s["nct_id"],s["product_id"],json.dumps(s,ensure_ascii=False)))
        conn.execute("DELETE FROM study_results")
        conn.executemany("INSERT INTO study_results VALUES (?,?,?,?,?)", [(r["nct_id"], r["outcome_index"], r["product_id"], r["title"], json.dumps(r, ensure_ascii=False)) for r in read_jsonl(ROOT / "data/catalog/study_results.jsonl")])
        for d in docs:
            conn.execute("INSERT OR REPLACE INTO document_versions VALUES (?,?,?,?,?,?,?,?,?,?)", (d["document_id"],d["version_id"],d["product_id"],d["source"],d["role"],d["title"],d["url"],d["raw_path"],d["page_count"],json.dumps(d,ensure_ascii=False)))
        for p in self.pages:
            conn.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?)", (p["document_id"],p["version_id"],p["pdf_page"],p["product_id"],p["role"],p["text"],json.dumps({k:v for k,v in p.items() if k != "text"},ensure_ascii=False)))
        # Versions the catalog no longer lists (replaced by --refresh, or a web page fetched again) leave the database and the index.
        current = {(d["document_id"], d["version_id"]) for d in docs}
        stale = [r for r in conn.execute("SELECT document_id, version_id FROM document_versions") if r not in current]
        conn.executemany("DELETE FROM document_versions WHERE document_id = ? AND version_id = ?", stale)
        conn.executemany("DELETE FROM pages WHERE document_id = ? AND version_id = ?", stale)
        self.metrics["stale_versions_removed"] = len(stale)
        # Rebuild from canonical pages in one transaction: repeat ingestion does not duplicate.
        conn.execute("DELETE FROM page_search")
        conn.execute("INSERT INTO page_search SELECT document_id,version_id,pdf_page,product_id,role,text FROM pages")
        self.metrics["database_counts"] = {table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ["products","studies","study_results","document_versions","pages","page_search"]}
        conn.execute("INSERT OR REPLACE INTO ingestion_runs VALUES (?,?)", (self.run_id,json.dumps(self.metrics,ensure_ascii=False)))
        conn.commit(); conn.close()

    def run(self):
        cfg = json.loads((ROOT / "configs/collection/targets.json").read_text(encoding="utf-8"))
        self.review_sample_pages = cfg.get("review_sample_pages", {})
        for step in STEPS:
            if not self.only or step in self.only:
                self.attempt(f"{step} step", lambda: getattr(self, step)(cfg))
        docs = self.finish_catalog(cfg)
        if self.ocr:
            # Works from the catalog and parsed pages just written; can also be run alone: python ocr_regions.py
            import ocr_regions
            self.metrics["ocr"] = self.attempt("OCR of image regions and text-less pages", ocr_regions.run)
        self.metrics.update({"finished_at": now(), "download_count": len(self.downloads), "unique_raw_objects_in_run": len({x['sha256'] for x in self.downloads}),
                             "reused_documents": sum(bool(d.get("cache_reused")) for d in self.documents), "fetched_documents": sum(not d.get("cache_reused") for d in self.documents),
                             "catalog_documents": len(docs), "carried_documents": sum(d.get("last_seen_run") != self.run_id for d in docs),
                             "pdf_documents": len(self.documents), "total_pdf_pages": sum(d["page_count"] for d in docs), "ema_assessment_pages": sum(d["page_count"] for d in docs if d["role"] == "reference_assessment"),
                             "study_result_rows": len(self.study_results), "sample_table_candidates": len(self.sample_tables), "error_count": len(self.errors)})
        self.database()
        # data/summary.json describes a full run; a partial run records its own summary next to its manifests.
        write_json(ROOT / (f"data/runs/{self.run_id}.summary.json" if self.only else "data/summary.json"), self.metrics)
        write_jsonl(ROOT / f"data/runs/{self.run_id}.downloads.jsonl", self.downloads)
        write_jsonl(ROOT / f"data/runs/{self.run_id}.errors.jsonl", self.errors)
        print(json.dumps(self.metrics, ensure_ascii=False, indent=2), flush=True)

    def reparse(self):
        # Parse again the stored raw files of the documents already in the catalog; nothing is fetched.
        cfg = json.loads((ROOT / "configs/collection/targets.json").read_text(encoding="utf-8"))
        self.review_sample_pages = cfg.get("review_sample_pages", {})
        self.documents = [json.loads(line) for line in (ROOT / "data/catalog/documents.jsonl").read_text(encoding="utf-8").split("\n") if line.strip()]
        self.parse(reparse=True)
        write_jsonl(ROOT / "data/catalog/documents.jsonl", self.documents)
        if self.ocr:
            import ocr_regions
            self.metrics["ocr"] = self.attempt("OCR of image regions and text-less pages", ocr_regions.run)
        pdfs = [d for d in self.documents if d["kind"] == "pdf"]
        self.metrics.update({"mode": "reparse", "finished_at": now(), "pdf_documents": len(pdfs), "total_pdf_pages": sum(d["page_count"] for d in pdfs),
                             "repaired_documents": [d["document_id"] for d in pdfs if d["text_path"] != d["raw_path"]],
                             "pages_needing_ocr": sum(len(d["text_layer"]["pages_needing_ocr"]) for d in pdfs),
                             "private_use_replaced": sum(d["text_layer"]["private_use_replaced"] for d in pdfs),
                             "sample_table_candidates": len(self.sample_tables), "error_count": len(self.errors)})
        self.database()
        write_json(ROOT / f"data/runs/{self.run_id}.summary.json", self.metrics)
        print(json.dumps(self.metrics, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="Rebuild from checked raw snapshots without network access")
    parser.add_argument("--ocr", action="store_true", help="OCR every image region and text-less page (Tesseract + RapidOCR); results stay separate from native text")
    parser.add_argument("--only", help="Comma-separated steps to run, keeping other sources' documents as they are: " + ",".join(STEPS))
    parser.add_argument("--reparse", action="store_true", help="Parse the catalog's stored raw files again (text-layer repair included) without collecting")
    parser.add_argument("--refresh", action="store_true", help="Download documents already in the catalog again; a changed file becomes a new version (OCR, evidence units and chunks of the old one go stale)")
    args = parser.parse_args()
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    if only and set(only) - set(STEPS):
        parser.error("unknown step: " + ",".join(sorted(set(only) - set(STEPS))))
    if args.reparse and (only or args.offline):
        parser.error("--reparse reads the catalog only; it does not combine with --only or --offline")
    collector = Collector(offline=args.offline, ocr=args.ocr, only=only, refresh=args.refresh)
    collector.reparse() if args.reparse else collector.run()
