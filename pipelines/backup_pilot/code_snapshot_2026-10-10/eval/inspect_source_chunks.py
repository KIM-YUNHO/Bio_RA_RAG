#!/usr/bin/env python3
"""Compile source-reviewed examples into scoped child metadata, without model calls.

Review specifications are explicit annotations, not automatic classifier results.
Each target/evidence anchor must resolve uniquely in the current source index.
"""
import argparse
import collections
from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rag"))
import attribute_contexts as ac
import context_units as cu
import study_registry as sr
from evidence import contains_quote
from chunk_schema import validate
from context_schema import SCHEMA as METADATA_SCHEMA, validate_metadata


def anchor_pattern(text):
    return re.compile(r"\s+".join(re.escape(p) for p in text.split()))


def select(units, selector):
    """Resolve anchors in one source section/region; retain original unit offsets."""
    grouped = collections.defaultdict(list)
    for u in units.values():
        if any(u.get(k) != selector[k] for k in ("document_id", "pdf_page", "source_kind", "region", "section_id") if k in selector):
            continue
        # OCR prefixes can repeat a native caption with a different joiner.
        if u["source_kind"] == "native" and u["source_span"]["joiner"] != "\n":
            continue
        grouped[(u["document_id"], u["pdf_page"], u["source_kind"], u["region"], u["section_id"])].append(u)
    matches = []
    for members in grouped.values():
        text, positions = "", []
        for u in members:
            if text:
                text += "\n"
            positions.append((u, len(text), len(text) + len(u["text"])))
            text += u["text"]
        starts = list(anchor_pattern(selector["start_quote"]).finditer(text))
        for first in starts:
            last = anchor_pattern(selector.get("end_quote", selector["start_quote"])).search(text, first.start())
            if not last:
                continue
            start, end = first.start(), last.end()
            ranges = [{"unit_id": u["unit_id"], "start": max(start, a) - a, "end": min(end, b) - a}
                      for u, a, b in positions if max(start, a) < min(end, b)]
            matches.append({"text": text[start:end], "source_ranges": ranges, "source": members[0]})
    if len(matches) != 1:
        raise ValueError(f"Expected one source match, got {len(matches)}: {selector}")
    return matches[0]


def compile_cases(units, membership, cases):
    assignments, examples = [], []
    for case in cases:
        target = select(units, case["target"])
        labels = []
        for n, label in enumerate(case["labels"]):
            if label["study_key"] is not None and label["study_key"] not in sr.studies_of(target["source"]["product_id"]):
                raise ValueError(f"Study/product mismatch: {case['case_id']}")
            if label["role"] not in ac.ROLES or label["analysis"] not in ac.ANALYSES:
                raise ValueError(f"Invalid metadata enum: {case['case_id']}")
            if label["role"] == "no_study" and (label["study_key"] is not None or label["scope"] != "none"):
                raise ValueError(f"Invalid no-study scope: {case['case_id']}")
            if any(not contains_quote(target["text"], e) for e in label.get("endpoints", [])):
                raise ValueError(f"Endpoint absent from target: {case['case_id']}")
            if label.get("comparator") and not contains_quote(target["text"], label["comparator"]):
                raise ValueError(f"Comparator absent from target: {case['case_id']}")
            proofs = []
            for proof in label["evidence"]:
                found = select(units, proof["source"])
                u = found["source"]
                if u["product_id"] != target["source"]["product_id"]:
                    raise ValueError(f"Evidence belongs to a different product: {case['case_id']}")
                proofs.append({"quote": found["text"], "fields": proof["fields"], "document_id": u["document_id"],
                               "version_id": u["version_id"], "pdf_page": u["pdf_page"], "source_kind": u["source_kind"],
                               "region": u["region"], "source_ranges": found["source_ranges"]})
            assignment = {k: label.get(k, [] if k == "endpoints" else "") for k in
                          ("study_key", "role", "scope", "analysis", "endpoints", "comparator")}
            assignment["analysis_details"] = label.get("analysis_details", {"aggregation": "unknown", "timing": "unknown", "intent": "unknown", "labels": []})
            validate(assignment["analysis_details"], ac.ANALYSIS_DETAILS, "analysis_details")
            if any(not contains_quote(target["text"], v) for v in assignment["analysis_details"]["labels"]):
                raise ValueError(f"Analysis label absent from target: {case['case_id']}")
            assignment.update({"assignment_id": f"{case['case_id']}:a{n}", "case_id": case["case_id"],
                               "evidence": proofs, "applies_to": target["source_ranges"],
                               "status": "source_reviewed_by_assistant", "method": "source_example_review",
                               "domain_expert_reviewed": False})
            assignments.append(assignment)
            labels.append(assignment)
        examples.append({"case_id": case["case_id"], "description": case["description"], "note": case.get("note", ""),
                         "target": case["target"], "text": target["text"], "source_ranges": target["source_ranges"], "assignments": labels})
    inherited = cu.inherit(assignments, membership)
    for value in inherited.values():
        validate_metadata(value, units)
    return examples, assignments, inherited


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rag-dir", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    units, _, membership = cu.load(args.rag_dir)
    specification = json.loads(args.cases.read_text(encoding="utf-8"))
    current_sha = hashlib.sha256((args.rag_dir / "chunks.jsonl").read_bytes()).hexdigest()
    if specification.get("source_chunks_sha256", current_sha) != current_sha:
        raise ValueError("Review specification belongs to a different source chunk build")
    cases = specification["cases"]
    examples, assignments, inherited = compile_cases(units, membership, cases)
    chunks = {c["chunk_id"]: c for c in map(json.loads, (args.rag_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines())}
    for example in examples:
        aids = {a["assignment_id"] for a in example["assignments"]}
        example["children"] = [{"chunk": chunks[cid], "metadata": data} for cid, data in inherited.items()
                               if any(a["assignment_id"] in aids for a in data["contexts"])]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "chunk_metadata.schema.json").write_text(json.dumps(METADATA_SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, rows in (("examples.jsonl", examples), ("assignments.jsonl", assignments), ("chunk_metadata.jsonl", inherited.values())):
        (args.output_dir / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    now = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S KST")
    summary = {"created_at": now, "scope": "Source-reviewed demonstration, not held-out classifier or retrieval accuracy",
               "cases": len(examples), "assignments": len(assignments), "covered_chunks": sum(bool(c["contexts"]) for c in inherited.values()),
               "documents": sorted({e["target"]["document_id"] for e in examples}),
               "statuses": dict(collections.Counter(c["status"] for c in inherited.values())),
               "new_llm_calls": 0, "new_embeddings": 0, "active_index_changed": False,
               "schema_and_source_bounds": "passed",
               "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
               "chunks_sha256": hashlib.sha256((args.rag_dir / "chunks.jsonl").read_bytes()).hexdigest()}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown:
        lines = ["# 실제 원문 청킹·메타데이터 사례", "", f"- 본문 최종 수정: {now}", f"- 수정시각 기록: {now}", "",
                 "기존 파싱/OCR 원문에서 검토한 적용 구간이다. 자동 LLM 판정 결과나 독립 정답률 평가가 아니다. "
                 "부착 상태는 source_reviewed_by_assistant이며 도메인 전문가 검토는 수행하지 않았다. "
                 "아래 원문 구간 외에는 판정을 상속하지 않는다. 기존 VLM 표 작업과 활성 인덱스는 변경하지 않았다.", "",
                 f"검토 구간 {len(examples)}개, 판정 {len(assignments)}개, 메타데이터가 연결된 후보 청크 {summary['covered_chunks']}개.", "",
                 "본문은 BGE-M3 목표 768·최대 1,024, OCR 표 후보는 최대 2,048토큰이다. 소제목·시험·통합분석 경계를 넘겨 채우지 않으며 작은 청크도 보존한다. "
                 "표는 현재 OCR 영역 단위이며 논리 행/열 검증은 VLM 연결 후 수행한다. "
                 "pdf_page는 PDF 물리 페이지 또는 기존 HTML section 번호이고 locator_type으로 구분한다.", ""]
        for e in examples:
            lines += [f"## {e['case_id']} — {e['description']}", "", e["note"], "", "판정 대상 원문:", "", "```text", e["text"], "```", "",
                      "부착할 의미 메타데이터(근거·정확한 적용 범위는 examples.jsonl에 포함):", "", "```json",
                      json.dumps([{k: a[k] for k in ("study_key", "role", "scope", "analysis", "analysis_details", "endpoints", "comparator", "status")} for a in e["assignments"]], ensure_ascii=False, indent=2), "```", "",
                      "연결되는 실제 검색 청크:", "", "| 위치 | 본문 토큰 | 절/표 | 적용 상태 |", "|---|---:|---|---|"]
            for child in e["children"]:
                c, m = child["chunk"], child["metadata"]
                loc = f"{c['document_id']} {c['locator_type']} {c['pdf_page']} {c['source_kind']}"
                lines.append(f"| {loc} | {c['n_tokens']} | {(c['structure']['caption'] or c['section'] or '').replace('|', '/')} | {m['status']} |")
            lines += [""]
        args.markdown.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
