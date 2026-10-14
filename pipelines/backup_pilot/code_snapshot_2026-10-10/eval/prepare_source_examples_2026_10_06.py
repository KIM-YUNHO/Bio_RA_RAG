"""Explicit assistant source-review annotations; never an automatic label generator.

Each label below is a reviewed example. Literal anchors are checked separately by
inspect_source_chunks.py; these examples are not independent accuracy evidence.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/rag/candidates/source_examples_2026-10-06/corpus"
SB, ABP, CT = "EMA:63563", "EMA:64196", "EMA:64792"
F_SB = "FDA:761373Orig1s000,761425Orig1s000:MultidisciplineR"
F_ABP = "FDA:761285Orig1s000,761331Orig1s000:MultidisciplineR"
F_CT = "FDA:761338Orig1Orig2s000:MultidisciplineR"
H_SB, H_ABP, H_CT = "HC:SBD1739909145014", "HC:SBD1721675750511", "HC:SBD1739202346124"


def source(doc, page, first, last=None, kind="native", region=None):
    result = {"document_id": doc, "pdf_page": page, "source_kind": kind, "start_quote": first}
    if last:
        result["end_quote"] = last
    if region is not None:
        result["region"] = region
    return result


def proof(src, *fields):
    return {"source": src, "fields": list(fields)}


def label(key, evidence, role="subject", scope="single", analysis="unknown", endpoints=None, comparator="", details=None):
    return {"study_key": key, "role": role, "scope": scope, "analysis": analysis, "endpoints": endpoints or [],
            "comparator": comparator, "evidence": evidence, "analysis_details": details or
            {"aggregation": "unknown", "timing": "unknown", "intent": "unknown", "labels": []}}


def case(cid, description, target, labels, note=""):
    return {"case_id": cid, "description": description, "target": target, "labels": labels, "note": note}


def main():
    cases = []
    sb_pk = proof(source(SB, 37, "Study SB17-1001 is a randomised"), "study_key")
    sb_ps = proof(source(SB, 37, "SB17-3001 | Comparative efficacy", kind="ocr_region", region=1), "study_key")
    ct_pk = proof(source(CT, 31, "Study CT-P43 1.1 was a Phase 1"), "study_key")
    ada = source(SB, 42, "The comparative ADA incidences", "this is agreed.")
    common_ada = proof(source(SB, 42, "the Phase I and Phase III studies using a bridging ligand-binding assay"), "role", "scope")
    cases.append(case("E01", "SB17 공통 ADA 분석법", ada,
        [label("ST_SB17_1001", [sb_pk, common_ada], scope="multi"), label("ST_SB17_3001", [sb_ps, common_ada], scope="multi")],
        "원문이 1상·3상 공통 분석법이라고 명시한다. 가까운 PK 절 제목만 보고 SB17-1001 하나로 제한하지 않는다. pooling도 추정하지 않는다."))
    nab = source(SB, 42, "The comparative NAb incidences", "been appropriately characterised.")
    common_nab = proof(source(SB, 42, "Phase I study in healthy subjects as well as in the clinical Phase III study in psoriasis patients"), "role", "scope")
    cases.append(case("E02", "SB17 공통 NAb 분석법", nab,
        [label("ST_SB17_1001", [sb_pk, common_nab], scope="multi"), label("ST_SB17_3001", [sb_ps, common_nab], scope="multi")],
        "E01과는 소제목 경계에서 나뉜다. 공통 NAb 방법의 구간에 두 시험을 부착한다."))
    cases.append(case("E03", "같은 NAb 청크 안의 SB17-1001 특정 논의",
        source(SB, 42, "In general, although the cut-point", "standard and acceptable."),
        [label("ST_SB17_1001", [proof(source(SB, 42, "pre-dose samples from SB17-1001"), "study_key", "role")])],
        "공통 방법 구간 뒤의 특정 시험 문장에만 적용한다. E02의 SB17-3001을 이 문장까지 복사하지 않는다."))
    cases.append(case("E04", "SB17 대상자 흐름",
        source(SB, 42, "A total of 373 subjects were screened", "due to subject pregnancy)."),
        [label("ST_SB17_1001", [sb_pk, proof(source(SB, 42, "A total of 373 subjects were screened"), "role")])],
        "Patient flow 경계에서 분석법과 분리된다. 근거는 대상자 흐름이며 endpoint/analysis는 억지로 채우지 않는다."))
    overview = "Table 2의 기존 OCR 텍스트 항목이다. 논리 행/열과 수치의 정합성을 검증한 것은 아니며 VLM 결과는 사용하지 않았다."
    cases.append(case("E05", "시험 개요 표의 SB17-1001 항목",
        source(SB, 37, "SB17-1001 | Comparative PK", "US Stelara in healthy subjects", "ocr_region", 1),
        [label("ST_SB17_1001", [proof(source(SB, 37, "SB17-1001 | Comparative PK", kind="ocr_region", region=1), "study_key", "role")])], overview))
    cases.append(case("E06", "같은 개요 표의 SB17-3001 항목",
        source(SB, 37, "SB17-3001 | Comparative efficacy", "psoriasis (PsO) | transition period.", "ocr_region", 1),
        [label("ST_SB17_3001", [sb_ps, proof(source(SB, 37, "SB17-3001 | Comparative efficacy", kind="ocr_region", region=1), "role")])], overview + " 같은 검색 청크에 있어도 원문 범위별로 시험이 다르다."))
    cases.append(case("E07", "SB17 PK 통계표",
        source(SB, 45, "Ratio PK Parameter | Treatment", "EU Stelara | 67 | 64 | 5433", "ocr_region", 1),
        [label("ST_SB17_1001", [proof(source(SB, 45, "Pharmacokinetic Analysis Set, Study SB17-1001"), "study_key"),
            proof(source(SB, 45, "PK Parameter | Treatment", kind="ocr_region", region=1), "role")], endpoints=["AUCinf", "Cmax"])],
        "Table 5의 기존 OCR 영역을 보존한다. AUCinf는 이 OCR의 원문 표기이며 수치·단위·행 연결은 확정하지 않는다. Primary PK parameters만으로 analysis=primary를 확정하지 않는다."))
    cases.append(case("E08", "CT-P43 1.1 시험 설계",
        source(CT, 31, "Study CT-P43 1.1 was a Phase 1", "in healthy male subjects."),
        [label("ST_CTP43_1_1", [ct_pk, proof(source(CT, 31, "Study CT-P43 1.1 was a Phase 1, 2-part"), "role")])],
        "시험 전체의 설계 문단이다. 두 파트가 존재한다는 사실만으로 문단 전체를 Part 2로 지정하지 않는다."))
    cases.append(case("E09", "CT-P43 1.1 Part 1의 안전성 목적",
        source(CT, 31, "The first 30 subjects were", "to compare initial safety"),
        [label("ST_CTP43_1_1_P1", [ct_pk, proof(source(CT, 31, "enrolled in Part 1 to compare initial safety"), "study_key", "role")])],
        "같은 Methods 문단 내부라도 Part 1 문장 범위에만 적용한다."))
    cases.append(case("E10", "CT-P43 1.1 Part 2 PK 목적",
        source(CT, 31, "Primary objective (Part 2):", "EU-approved Stelara to US-licensed Stelara)."),
        [label("ST_CTP43_1_1_P2", [ct_pk, proof(source(CT, 31, "Primary objective (Part 2):"), "study_key", "role")],
               endpoints=["AUC0-inf", "Cmax"], comparator="CT-P43 to EU-approved Stelara")],
        "실제 Part 2 소제목에서 분리한다. Primary objective는 분석 유형의 primary 확정 근거로 사용하지 않는다."))
    cases.append(case("E11", "시험 전환 직전의 CT-P43 1.1 결과",
        source(CT, 39, "Primary PK parameter AUC0-inf", "Impact of ADA positivity on Cmax was not found."),
        [label("ST_CTP43_1_1", [ct_pk, proof(source(CT, 39, "Impact of ADA positivity on Cmax was not found."), "role")], endpoints=["AUC0-inf", "Cmax"])],
        "이 원문 구간에는 Part 식별 근거를 추가로 검토하지 않았으므로 상위 시험까지만 부착한다. 뒤에 등장하는 1.2를 적용하지 않는다."))
    ct_jp = proof(source(CT, 39, "Study CT-P43 1.2 was a Phase I"), "study_key", "role")
    cases.append(case("E12", "동일 페이지의 CT-P43 1.2 설계",
        source(CT, 39, "Study CT-P43 1.2 was a Phase I", "Japanese Male Subjects."),
        [label("ST_CTP43_1_2", [ct_jp])], "Study CT-P43 1.2 제목에서 이전 시험과 청크·상속 범위를 분리한다."))
    pool_target = source(CT, 51, "Post-Hoc pooled analysis", "endpoints were all comparable.")
    pool_proof = proof(source(CT, 51, "pooled analysis of the PK results of Studies CT-P43.1.1 part 2 and CT-P43 1.2."), "study_key", "role", "analysis", "aggregation")
    pool_heading = proof(source(CT, 51, "Post-Hoc pooled analysis"), "timing", "analysis_labels")
    pooled_details = {"aggregation": "pooled", "timing": "post_hoc", "intent": "unknown", "labels": ["Post-Hoc pooled analysis"]}
    pool_labels = [label(k, [pool_proof, pool_heading], role=r, scope="multi", analysis="pooled", endpoints=["AUC0-inf", "Cmax"], details=pooled_details)
                   for k, r in [("ST_CTP43_POOLED", "subject"), ("ST_CTP43_1_1_P2", "pooled_component"), ("ST_CTP43_1_2", "pooled_component")]]
    cases.append(case("E13", "CT-P43 post-hoc 통합분석", pool_target, pool_labels,
        "분석 자체가 subject이고 두 시험은 pooled_component다. 앞 CT-P43 3.1 표의 제목을 이어 붙이지 않는다. pooled와 post_hoc을 별도 차원으로 동시에 보존한다."))
    cases.append(case("E14", "통합분석의 post-hoc tipping point 검토",
        source(CT, 51, "In addition, the applicant conducted a post-hoc tipping point analysis", "125.00 for the AUC."),
        [label("ST_CTP43_POOLED", [pool_proof, proof(source(CT, 51, "post-hoc tipping point analysis to test the robustness"), "role", "analysis", "timing")],
               analysis="sensitivity_or_post_hoc", endpoints=["AUC"], details={"aggregation": "pooled", "timing": "post_hoc", "intent": "unknown", "labels": ["post-hoc tipping point analysis"]})],
        "같은 큰 검색 청크 안에서도 E13과 다른 구간 판정이다. 구성 시험을 새 구간 전체의 개별 subject로 복사하지 않는다. intent는 별도의 명시 근거를 확정하지 않아 unknown이다."))
    cases.append(case("E15", "비임상 Local Tolerance",
        source(CT, 28, "Macroscopic observation", "Steqeyma or EU-Stelara."),
        [label(None, [proof(source(CT, 28, "control animals and the animals administered Steqeyma or EU-Stelara."), "role")], role="no_study", scope="none", analysis="not_applicable")],
        "임상시험 키를 부착하지 않는다. no_study는 등록한 임상시험이 없다는 뜻이며 비임상 시험 자체가 없다는 뜻은 아니다."))
    cases.append(case("E16", "비임상 논의 속 임상 PK 결과 인용",
        source(ABP, 36, "No difference in the PK profile was observed in", "clinical study 20190230."),
        [label("ST_ABP_20190230", [proof(source(ABP, 36, "No difference in the PK profile was observed in clinical study 20190230."), "study_key")], role="unknown", scope="unknown")],
        "주변은 in vitro pharmacology 논의지만 이 문장은 임상 PK 결과를 인용한다. 시험 키는 확인되지만 보조 인용과 결과 요약의 역할 경계는 보류한다. 절 제목만으로 no_study 또는 mentioned를 확정하지 않는다."))
    cases.append(case("E17", "FDA SB17 시험 설계",
        source(F_SB, 48, "Study SB17-1001 was a randomized", "subcutaneously in the abdomen."),
        [label("ST_SB17_1001", [proof(source(F_SB, 48, "Study SB17-1001 was a randomized, double-blind, three-arm"), "study_key", "role")])]))
    cases.append(case("E18", "FDA ABP654 1상 ADA/NAb 결과 구간",
        source(F_ABP, 31, "• The incidence of anti-drug antibodies (ADA) and neutralizing antibody (NAb) formation for ABP 654 was numerically lower (15.4%", "20190230)."),
        [label("ST_ABP_20190230", [proof(source(F_ABP, 31, "healthy subjects in the PK similarity study (Study 20190230)."), "study_key", "role")], endpoints=["ADA", "NAb"])],
        "같은 Immunogenicity 청크에 두 시험 결과가 있다. 해당 bullet에만 1상 시험을 부착한다."))
    cases.append(case("E19", "동일 FDA 청크의 ABP654 3상 ADA/NAb 결과 구간",
        source(F_ABP, 31, "• The incidence of ADA and NAb formation for ABP 654 was numerically lower (21.9%", "(Study 20190232)."),
        [label("ST_ABP_20190232", [proof(source(F_ABP, 31, "patients with psoriasis in the comparative clinical study (Study 20190232)."), "study_key", "role")], endpoints=["ADA", "NAb"])],
        "E18과 겹치지 않는 원문 범위다. 청크 전체에 하나의 study_key를 강제하지 않는다."))
    cases.append(case("E20", "FDA CT-P43 Part 2 실패 결과의 검토",
        source(F_CT, 26, "The Applicant’s three-way pharmacokinetics", "CT-P43 and US-Stelara/EU-Stelara."),
        [label("ST_CTP43_1_1_P2", [proof(source(F_CT, 26, "(Study CT-P43 1.1 Part 2) did not meet"), "study_key", "role")], endpoints=["AUC0-inf"])],
        "같은 페이지의 새 시험 제안이나 적응증 제외 논의에는 이 판정을 상속하지 않는다."))
    h_abp_pk = proof(source(H_ABP, 13, "Study 20190230 was conducted in 238 healthy adult male and female subjects"), "study_key", "role")
    cases.append(case("E21", "Health Canada ABP654 PK 구간",
        source(H_ABP, 13, "Study 20190230 was conducted", "Pharmacokinetic parameters were estimated by a non-compartmental analysis."),
        [label("ST_ABP_20190230", [h_abp_pk], endpoints=["AUClast", "Cmax"], comparator="EU-Stelara")],
        "물리 PDF 페이지가 아닌 원래 HTML section 13이다. 다른 소제목의 3상 시험을 상속하지 않는다."))
    cases.append(case("E22", "같은 HTML section의 ABP654 유효성 시험",
        source(H_ABP, 13, "Study 20190232 was a randomized", "followed up to Week 52."),
        [label("ST_ABP_20190232", [proof(source(H_ABP, 13, "Study 20190232 was a randomized, double-blind, active-controlled study."), "study_key", "role")])],
        "Comparative Efficacy, Safety, and Immunogenicity 소제목에서 PK 구간과 나뉜다."))
    hct_parent = proof(source(H_CT, 14, "Study CT-P43 1.1 consisted of two parts."), "study_key")
    cases.append(case("E23", "Health Canada CT-P43 Part 1 문장",
        source(H_CT, 14, "Part 1 (pilot study)", "Steqeyma or EU-Stelara."),
        [label("ST_CTP43_1_1_P1", [hct_parent, proof(source(H_CT, 14, "Part 1 (pilot study) was designed to collect preliminary safety data"), "study_key", "role")])],
        "하나의 HTML 문단·검색 청크 안에서도 Part 1 문장 범위만 지정한다."))
    cases.append(case("E24", "동일 HTML 문단의 CT-P43 Part 2",
        source(H_CT, 14, "Part 2 (comparative pharmacokinetic study)", "Cmax) of both Steqeyma and EU-Stelara."),
        [label("ST_CTP43_1_1_P2", [hct_parent, proof(source(H_CT, 14, "Part 2 (comparative pharmacokinetic study) enrolled 241 subjects"), "study_key", "role")], endpoints=["AUClast", "Cmax"], comparator="EU-Stelara")],
        "E23과 겹치지 않는다. 각 기관의 본문을 보존하며 EMA의 대상자 수로 이 원문을 보정하지 않는다."))
    hp = proof(source(H_CT, 14, "pooled post-hoc analysis of Study CT-P43 1.1 Part 2 and Study CT-P43 1.2"), "study_key", "role", "analysis", "aggregation", "timing")
    hdetails = {"aggregation": "pooled", "timing": "post_hoc", "intent": "unknown", "labels": ["pooled post-hoc analysis"]}
    cases.append(case("E25", "Health Canada CT-P43 통합분석",
        source(H_CT, 14, "In addition, pooled post-hoc analysis", "Cmax was 109.68."),
        [label(k, [hp], role=r, scope="multi", analysis="pooled", endpoints=["AUClast", "Cmax"], details=hdetails)
         for k, r in [("ST_CTP43_POOLED", "subject"), ("ST_CTP43_1_1_P2", "pooled_component"), ("ST_CTP43_1_2", "pooled_component")]],
        "기관별 endpoint 표기 AUClast를 그대로 사용한다. pooled와 post_hoc을 동시에 기록한다."))
    cases.append(case("E26", "Health Canada SB17 PK 시험",
        source(H_SB, 14, "Study SB17-1001 was a Phase I", "in healthy subjects."),
        [label("ST_SB17_1001", [proof(source(H_SB, 14, "Study SB17-1001 was a Phase I, randomized, double-blind"), "study_key", "role")])]))
    cases.append(case("E27", "Health Canada SB17 유효성 시험",
        source(H_SB, 14, "Study SB17-3001 was a confirmatory", "conducted at Week 52."),
        [label("ST_SB17_3001", [proof(source(H_SB, 14, "Study SB17-3001 was a confirmatory, Phase III"), "study_key", "role")])],
        "동일 HTML section 14의 별도 소제목이다. PK 시험 메타데이터를 이어 붙이지 않는다."))
    out = ROOT / "eval/source_case_review_2026-10-06.json"
    payload = {"review_status": "source_reviewed_by_assistant", "domain_expert_reviewed": False,
               "purpose": "Concrete source/chunk/metadata examples, not an accuracy benchmark",
               "source_chunks_sha256": hashlib.sha256((CORPUS / "chunks.jsonl").read_bytes()).hexdigest(), "cases": cases}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(cases)} source review cases: {out}")


if __name__ == "__main__":
    main()
