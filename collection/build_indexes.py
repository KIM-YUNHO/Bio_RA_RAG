#!/usr/bin/env python3
"""Rebuild data/raw/INDEX.csv and add newly collected documents to pdfs_by_product/. Safe to re-run."""
import csv
import hashlib
import json
import re
import shutil
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = json.loads((ROOT / "configs/collection/targets.json").read_text(encoding="utf-8"))
GUIDELINES = "GUIDELINES"  # documents with no product (role guideline)

def folder_of(product):
    # <product_id>_<EU name>-<US name> (distinct names only): SB17_Pyzchiva, ABP654_Wezenla-Wezlana, BAT2206_Usymro-Starjemza
    return product["product_id"] + "_" + "-".join(dict.fromkeys(n for n in (product.get("ema_name"), product.get("us_brand")) if n))

def label_of(product):
    eu, us = product.get("ema_name"), product.get("us_brand")
    names = f"{eu} (EU·US)" if eu == us else " · ".join(f"{n} ({j})" for n, j in ((eu, "EU"), (us, "US")) if n)
    return f"{product['aliases'][0]} / {names}" + (" — 대조약(원 의약품)" if product.get("kind") == "reference" else "")

FOLDERS = {p["product_id"]: folder_of(p) for p in TARGETS["products"]}
LABELS = {folder_of(p): label_of(p) for p in TARGETS["products"]} | {GUIDELINES: "지침 (제품 무관)"}

def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()] if path.exists() else []

def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))

def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)

def readable_name(doc, studies):
    # Names are derived from the document ID so a re-run gives the same file name.
    parts = doc["document_id"].split(":")
    safe = lambda s: re.sub(r"[^A-Za-z0-9.-]+", "-", str(s)).strip("-")
    if doc.get("role") == "guideline":
        return f"GL_{doc['source'].replace(' ', '')}_{safe(':'.join(parts[1:]))}"
    if doc["source"] == "ClinicalTrials.gov":  # same pattern as the hand-named copies: CTgov_<NCT>_<sponsor study id>_<Protocol|SAP>-<n>_<date>
        meta, org = doc["source_metadata"], (studies.get(parts[1], {}).get("org_study_id") or {}).get("id", "")
        kind = "Protocol" if doc["role"] == "reference_protocol" else "SAP"
        return f"CTgov_{parts[1]}_{safe(org)}_{kind}-{re.sub(r'[^0-9]', '', meta.get('filename', '')) or 'x'}_{meta.get('date', '')}"
    if doc["source"] == "EMA":
        return f"EMA_{doc['document_type']}_{parts[1]}"
    if doc.get("step") == "fda_letters":
        return f"FDA_{parts[1]}_{safe(parts[2])}"
    if doc["source"] == "FDA":
        suffix = doc["source_metadata"]["suffix"]
        base = parts[1][:-len(suffix)] if parts[1].endswith(suffix) else parts[1]
        return f"FDA_BLA{'-'.join(dict.fromkeys(re.findall(r'(?<!\d)\d{6}(?!\d)', base)))}_{(re.search(r'((?:Orig\d)+s\d{3})', base) or re.search(r'(s\d{3})', base))[1]}_{suffix}"
    if doc["source"] == "PMDA":
        return f"PMDA_{parts[2]}_{parts[3]}"
    if parts[1] == "PM":
        return f"HC_ProductMonograph_{parts[2]}"
    return f"HC_{parts[1][:3]}_{parts[1][3:]}"

def sync_readable_copies(docs):
    index_path = ROOT / "pdfs_by_product/INDEX.csv"
    rows = read_csv(index_path)
    fields = list(rows[0])
    known = {r["sha256"] for r in rows}
    product_label = {r["file"].split("/")[0]: r["product"] for r in rows} | {k: v for k, v in LABELS.items() if k not in {r["file"].split("/")[0] for r in rows}}
    studies = {s["nct_id"]: s for s in read_jsonl(ROOT / "data/catalog/studies.jsonl")}
    added = 0
    for doc in docs:
        if doc["sha256"] in known:
            continue
        folder = FOLDERS[doc["product_id"]] if doc.get("product_id") else GUIDELINES
        (ROOT / "pdfs_by_product" / folder).mkdir(exist_ok=True)
        number = 1 + max([int(r["file"].split("/")[1][:2]) for r in rows if r["file"].startswith(folder + "/")] or [0])
        rel = f"{folder}/{number:02}_{readable_name(doc, studies)}.{doc['kind']}"
        shutil.copyfile(ROOT / doc["raw_path"], ROOT / "pdfs_by_product" / rel)
        assert hashlib.sha256((ROOT / "pdfs_by_product" / rel).read_bytes()).hexdigest() == doc["sha256"]
        notes = []
        if doc["kind"] == "html":
            notes.append(f"웹페이지(HTML), 섹션 {doc['page_count']}개로 나눠 파싱")
        if doc.get("language") == "ja":
            notes.append("일본어 문서")
        if doc["page_count"] > doc["nonempty_text_pages"]:
            notes.append(f"{doc['page_count']}페이지 중 {doc['page_count'] - doc['nonempty_text_pages']}페이지 텍스트 없음")
        meta = doc["source_metadata"]
        application = meta.get("application_doc") or (meta.get("submissions") or [{}])[0]
        if doc.get("role") == "guideline":
            notes.append(f"지침 상태: {meta.get('status') or '미확인'}" + "".join(f", {k} {meta[k]}" for k in ("issued", "first_published", "date_modified") if meta.get(k)))
        if doc.get("step") == "fda_letters":
            notes.append("Drugs@FDA 문서 링크에서 받음(심사 패키지 밖의 승인서한 또는 최신 라벨)")
        rows.append({"file": rel, "product": product_label[folder], "source": doc["source"], "doc_type": f"{doc['source']} {doc['document_type']}", "study_code": "-", "nct_id": "-",
                     "version": f"{application['SubmissionType'].strip()}-{application['SubmissionNo']}" if application else meta.get("resource_id") or meta.get("reference_number") or doc["document_id"].rsplit(":", 1)[1],
                     "doc_date": application.get("ApplicationDocsDate", "")[:10] + (" (Drugs@FDA 등록일)" if application else "") or (str(meta.get("last_updated_date") or "")[:10] + " (EMA 게시·갱신일)" if meta.get("last_updated_date") else ""), "pages": doc["page_count"], "text_pages": doc["nonempty_text_pages"],
                     "image_table_candidate_pages": len(doc["possible_image_table_pages"]), "size_MB": f"{doc['bytes'] / 1e6:.1f}", "sha256_verified": "Y", "sha256": doc["sha256"],
                     "original_raw_path": doc["raw_path"], "parsed_path": doc["parsed_path"], "url": doc["url"], "retrieved_at": doc["retrieved_at"][:19] + "Z", "notes": "; ".join(notes)})
        known.add(doc["sha256"]); added += 1
    write_csv(index_path, rows, fields)
    return added, {r["sha256"]: r["file"] for r in rows}

def drop_downloaded(cache):
    path = ROOT / "pdfs_by_product/NOT_DOWNLOADED.csv"
    rows = read_csv(path)
    # The same file can be listed with http:// or with a percent-encoded comma in its name.
    same = lambda url: urllib.parse.unquote(re.sub(r"^http://", "https://", url.strip()))
    fetched = {same(url) for url in cache}
    # Drugs@FDA lists some files under a malformed URL (domain repeated, year folder missing); the file name identifies them.
    fda_name = lambda url: same(url).rsplit("/", 1)[1] if "accessdata.fda.gov/drugsatfda_docs/" in url else None
    fetched_fda = {fda_name(url) for url in cache} - {None}
    remaining = [r for r in rows if same(r["url"]) not in fetched and fda_name(r["url"]) not in fetched_fda]
    # Why each is not collected (docs/collection/INGESTION_PIPELINE.md section 4.1).
    for r in remaining:
        url = same(r["url"])
        r["note"] = ("기준상 미수집: 제품정보와 같은 내용의 변경 추적본(Word)" if url.endswith(".docx")
                     else "기준상 미수집: 과거 라벨(최신 라벨과 심사 패키지 라벨을 받음)" if "/label/" in url
                     else "기준상 미수집: 원 신청 서한(심사 패키지의 Approval Letter로 받음)" if "/appletter/" in url and re.search(r"Orig\d+s000", url)
                     else r.get("note", ""))
    write_csv(path, remaining, list(rows[0]))
    return len(rows) - len(remaining), len(remaining)

def raw_index(cache, docs, copies):
    cfg = json.loads((ROOT / "configs/collection/targets.json").read_text(encoding="utf-8"))["sources"]
    by_hash = {d["sha256"]: d for d in docs}
    studies = {s["api_source"]["sha256"]: s for s in read_jsonl(ROOT / "data/catalog/studies.jsonl")}
    fixed = {cfg["ema_products"]: ("EMA 전체 의약품 목록", "data/catalog/ema_authorised_biosimilars.jsonl; data/catalog/products.jsonl"),
             cfg["ema_documents"]: ("EMA 전체 EPAR 문서 목록", "data/catalog/ema_documents_{" + ",".join(p["product_id"] for p in TARGETS["products"] if p.get("ema_name")) + "}.jsonl"),
             cfg["purple_downloads"]: ("Purple Book 다운로드 페이지(월별 CSV 링크 탐색용)", ""),
             cfg["fda_downloads"]: ("Drugs@FDA 다운로드 페이지(ZIP 링크 탐색용)", "")}
    patterns = [(r"purplebook-search-.*\.csv$", "Purple Book 월별 데이터 CSV", "data/catalog/purple_full_selected.jsonl; data/catalog/purple_changes_selected.jsonl"),
                (r"fda\.gov/media/.*download", "Drugs@FDA 데이터 ZIP", "data/catalog/fda_products_selected.jsonl; data/catalog/fda_application_docs_selected.jsonl"),
                (r"drugsatfda_docs/nda/.*TOC\.html$", "FDA 승인 패키지 목차 페이지(심사 PDF 탐색용)", ""),
                (r"PmdaSearch/iyakuDetail/GeneralList/", "PMDA 제품 페이지(문서 링크 탐색용)", ""),
                (r"dhpp\.hpfb-dgpsa\.ca/review-documents\?", "Health Canada 심사문서 목록(성분 필터, 문서 탐색용)", ""),
                (r"health-products\.canada\.ca/api/drug/drugproduct", "Health Canada DPD 제품 목록(API)", ""),
                (r"health-products\.canada\.ca/dpd-bdpp/info", "Health Canada DPD 제품 페이지(제품설명서 링크 탐색용)", "")]
    rows = []
    for url, record in sorted(cache.items(), key=lambda item: item[1]["retrieved_at"]):
        body = (ROOT / record["raw_path"]).read_bytes()
        verified = hashlib.sha256(body).hexdigest() == record["sha256"] and len(body) == record["bytes"]
        doc, study = by_hash.get(record["sha256"]), studies.get(record["sha256"])
        product = nct = document_id = derived = ""
        if doc:
            description, product, nct, document_id, derived = f"{doc['document_type']} — {doc['title']}", doc["product_id"], doc.get("nct_id", ""), doc["document_id"], doc["parsed_path"]
        elif study:
            description, product, nct, derived = f"ClinicalTrials.gov 시험 레코드 ({'/'.join(study['phases'])}, 첨부 {len(study['large_docs'])}개)", study["product_id"], study["nct_id"], "data/catalog/studies.jsonl"
        elif url in fixed:
            description, derived = fixed[url]
        else:
            description, derived = next(((label, files) for pattern, label, files in patterns if re.search(pattern, url)), (f"{record['source']} {record['kind']}", ""))
        rows.append({"raw_file": Path(record["raw_path"]).name, "kind": record["kind"], "source": record["source"], "description": description, "product_id": product, "nct_id": nct, "document_id": document_id,
                     "readable_copy": f"pdfs_by_product/{copies[record['sha256']]}" if record["sha256"] in copies else "", "derived_files": derived, "bytes": record["bytes"], "sha256_verified": "Y" if verified else "N",
                     "sha256": record["sha256"], "url": url, "parent_url": record.get("parent_url") or "", "retrieved_at": record["retrieved_at"][:19] + "Z"})
    write_csv(ROOT / "data/raw/INDEX.csv", rows, list(rows[0]))
    return rows

if __name__ == "__main__":
    cache = json.loads((ROOT / "data/catalog/download_cache.json").read_text(encoding="utf-8"))
    docs = read_jsonl(ROOT / "data/catalog/documents.jsonl")
    added, copies = sync_readable_copies(docs)
    removed, remaining = drop_downloaded(cache)
    rows = raw_index(cache, docs, copies)
    files_on_disk = len([p for p in (ROOT / "data/raw").iterdir() if p.name != "INDEX.csv"])
    print(json.dumps({"raw_index_rows": len(rows), "raw_files_on_disk": files_on_disk, "unique_raw_hashes": len({r["sha256"] for r in rows}), "all_hashes_verified": all(r["sha256_verified"] == "Y" for r in rows),
                      "readable_copies_added": added, "readable_copies_total": len(copies), "not_downloaded_removed": removed, "not_downloaded_remaining": remaining}, indent=2))
