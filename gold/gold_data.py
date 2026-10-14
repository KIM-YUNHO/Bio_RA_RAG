# -*- coding: utf-8 -*-
"""정답셋 v0.2 라벨 데이터 (Claude = 라벨러 A). 모든 값은 EMA EPAR 원문에서 직접 확인."""

DOCS = {  # source_id -> (document_id, jsonl, total_pages)
    "SRC_EPAR_PYZ": ("EMA:63563", "EMA_63563.pages.jsonl", 168),
    "SRC_EPAR_WEZ": ("EMA:64196", "EMA_64196.pages.jsonl", 117),
    "SRC_EPAR_STQ": ("EMA:64792", "EMA_64792.pages.jsonl", 133),
}

DOC_SHA256 = {  # source_id -> 라벨링 당시 원문 PDF의 SHA-256 (data/raw 파일명, pages.jsonl의 version_id)
    "SRC_EPAR_PYZ": "fea275487bb971962201b77aec3d2e12cee7d545be026b378f1980fa272f6e76",
    "SRC_EPAR_WEZ": "123ca94880a07a1e9affd6f894d7c6dd95ac38714657ed338683b8ed2016e03c",
    "SRC_EPAR_STQ": "513959da3a6d724b63160792cbf18acb7f83d3396ab8147f491d543ce392bcce",
}

ROWS = []

def add(**kw):
    base = dict(analysis_id="NA", arm="NA", comparison="NA", analysis_set="NA", timepoint=None, row_type="CORE",
                status=None, value_text=None, value_num=None, ci_low=None, ci_high=None, unit=None,
                n_test=None, n_ref=None, denominator=None, source_id=None, pdf_page=None, locator=None,
                modality=None, quote=None, alt=None, searched=None, cross_src=None, cross_val=None,
                tier="AI_DRAFT", notes=None, b_hint=None)
    base.update(kw)
    ROWS.append(base)

def pct(x):
    return round(float(x) * 100, 2)

# =====================================================================================
# SB17 / Pyzchiva — SB17-1001
# =====================================================================================
P, S, D = "P_SB17", "ST_SB17_1001", "SRC_EPAR_PYZ"
add(product_id=P, study_key=S, field_id="design_phase", status="REPORTED", value_text="Phase I", value_num=1,
    source_id=D, pdf_page=37, locator="2.6.2.1 Study design", modality="TEXT",
    quote="randomised, double-blind, three-arm, parallel group, single-dose Phase I study")
add(product_id=P, study_key=S, field_id="design_population", status="REPORTED",
    value_text="Healthy male or female, aged 18-55 years (inclusive); body weight 60.0-90.0 kg, BMI 19.0-29.9 kg/m2",
    source_id=D, pdf_page=38, locator="Key inclusion criteria 1-2", modality="TEXT",
    quote="Healthy male or female, aged 18-55 years (inclusive) on the day of signing the informed consent.")
add(product_id=P, study_key=S, field_id="design_type", status="REPORTED",
    value_text="randomised, double-blind, three-arm, parallel group, single-dose",
    source_id=D, pdf_page=37, locator="2.6.2.1 Study design", modality="TEXT",
    quote="Study SB17-1001 is a randomised, double-blind, three-arm, parallel group, single-dose Phase I study")
add(product_id=P, study_key=S, field_id="dose", status="REPORTED", value_text="45 mg/0.5 mL", value_num=45, unit="mg",
    source_id=D, pdf_page=39, locator="Treatments", modality="TEXT",
    quote="Subjects received a single dose of 45 mg/0.5 mL of either Pyzchiva, EU-approved Stelara, or US- licensed Stelara on Day 1 as an SC injection.")
add(product_id=P, study_key=S, field_id="route", status="REPORTED", value_text="SC",
    source_id=D, pdf_page=39, locator="Treatments", modality="TEXT", quote="on Day 1 as an SC injection")
add(product_id=P, study_key=S, field_id="primary_pk_endpoints", status="REPORTED", value_text="Cmax; AUC0-inf",
    source_id=D, pdf_page=39, locator="Primary endpoints", modality="TEXT",
    quote="Maximum serum concentration (Cmax) AND area under the serum concentration-time curve from time zero extrapolated to infinity (AUC0-inf)")
add(product_id=P, study_key=S, field_id="equiv_margin", status="REPORTED", value_text="0.8 to 1.25 (80% to 125%)",
    unit="ratio", source_id=D, pdf_page=39, locator="Objectives", modality="TEXT",
    quote="entirely contained within the equivalence margin of 0.8 to 1.25",
    notes="EMA 심사는 SB17 vs EU 비교만 1차 판정 기준(p41). US 관련 비교는 FDA용.")
add(product_id=P, study_key=S, field_id="ada_assay", status="REPORTED",
    value_text="ADA: bridging ligand-binding assay, ECL (MSD), three-tiered. NAb: competitive ligand-binding assay, ECL (MSD)",
    source_id=D, pdf_page=42, locator="ADA assay / NAb assay", modality="TEXT",
    quote="using a bridging ligand-binding assay with an electrochemiluminescence (ECL) platform with Mesoscale Discovery (MSD)",
    alt="p42 NAb assay 문단")
for arm in ("TEST", "EU_RP", "US_RP"):
    add(product_id=P, study_key=S, field_id="n_randomized", arm=arm, analysis_set="RANDOMIZED", status="REPORTED",
        value_text="67", value_num=67, unit="명", source_id=D, pdf_page=42, locator="Patient flow", modality="TEXT",
        quote="201 subjects (67 subjects in each Pyzchiva, EU Stelara, and US Stelara) were randomised",
        alt="p43 Table 3 (이미지)")
for arm in ("TEST", "EU_RP", "US_RP"):
    add(product_id=P, study_key=S, field_id="n_pk_set", arm=arm, analysis_set="PK_SET", status="REPORTED",
        value_text="67", value_num=67, unit="명", source_id=D, pdf_page=42, locator="Patient flow", modality="TEXT",
        quote="were randomised, all of whom included in the SAF and PKS",
        alt="p43 Table 3 (이미지), p47 Table 8 'N = 67'",
        notes="PKS N=67이지만 평가변수별 실제 분석 n은 62/64/60 → GMR 행의 n_test/n_ref 참조")
for arm, txt, v in (("TEST", "18/67 (26.9%)", 26.9), ("EU_RP", "23/67 (34.3%)", 34.3), ("US_RP", "23/67 (34.3%)", 34.3)):
    add(product_id=P, study_key=S, field_id="ada_incidence", analysis_id="ADA_POSTDOSE_ANY", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Post-dose (Day 29/71/99)", status="REPORTED", value_text=txt, value_num=v,
        unit="%", denominator="ALL_SUBJECTS (N=67)", source_id=D, pdf_page=50, locator="Immunogenicity", modality="TEXT",
        quote="The overall incidence of subjects with positive post-dose ADA to ustekinumab was 18/67 (26.9%), 23/67 (34.3%), and 23/67 (34.3%)",
        alt="p49 Table 9 'Post-dose' 행 (이미지)",
        notes="정의: 기저치 이후 1회 이상 ADA 양성(기저치 양성 여부 무관)")
for arm in ("TEST", "EU_RP", "US_RP"):
    add(product_id=P, study_key=S, field_id="nab_incidence", analysis_id="NAB_POSTDOSE_ANY", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Post-dose overall", status="NOT_FOUND",
        searched="Pyzchiva EPAR 전체 텍스트 검색 + p49 Table 9(이미지) 확인: NAb는 시점별(Day 29/71/99)·ADA 양성자 분모로만 보고, 전체 기간 값 없음",
        source_id=D, notes="Day 99 시점 값은 EXTRA 행 참조")
# PRIMARY PK (ANOVA)
pk = [("pk_gmr_auc_inf", "0.99", "0.90", "1.08", "AUCinf"), ("pk_gmr_cmax", "0.90", "0.82", "0.98", "Cmax")]
for f, v, lo, hi, nm in pk:
    add(product_id=P, study_key=S, field_id=f, analysis_id="PRIMARY", comparison="TEST_vs_EU_RP", analysis_set="PK_SET",
        timepoint="Single dose", status="REPORTED", value_text=f"{v} (90% CI {lo}; {hi})", value_num=pct(v), ci_low=pct(lo),
        ci_high=pct(hi), unit="%", n_test=62, n_ref=64, source_id=D, pdf_page=43, locator="Table 4", modality="TEXT",
        quote=f"{lo}; {hi}", alt="p45 Table 5 (이미지, 동일 값)", cross_src="PILOT_OBS", cross_val=pct(v),
        notes="원문 scale=ratio. 1차 분석 = log 변환 ANOVA(p41)")
    for cmp_ in ("TEST_vs_US_RP", "EU_RP_vs_US_RP"):
        add(product_id=P, study_key=S, field_id=f, analysis_id="PRIMARY", comparison=cmp_, analysis_set="PK_SET",
            timepoint="Single dose", status="NOT_FOUND", source_id=D,
            searched="p45 본문은 '90% CI가 기준 범위 안'이라고만 서술. 1차(ANOVA) 수치는 EPAR에 없음. 수치는 사후 ANCOVA(Table 7, p46)에만 있음",
            notes="정성 결론은 pk_equiv_concluded 행에 기록")
for cmp_ in ("TEST_vs_EU_RP", "TEST_vs_US_RP", "EU_RP_vs_US_RP"):
    add(product_id=P, study_key=S, field_id="pk_gmr_auc_last", analysis_id="PRIMARY", comparison=cmp_,
        analysis_set="PK_SET", timepoint="Single dose", status="NOT_FOUND", source_id=D,
        searched="AUClast는 p47 Table 8 기술통계(평균·SD)만 보고, 기하평균비 미보고")
add(product_id=P, study_key=S, field_id="pk_equiv_concluded", analysis_id="PRIMARY", comparison="TEST_vs_EU_RP",
    analysis_set="PK_SET", status="REPORTED", value_text="Y", source_id=D, pdf_page=45, locator="Table 5 아래 본문",
    modality="TEXT", quote="These values for the 90% CIs were within the predefined equivalence margin of 0.8 to 1.25",
    notes="Cmax CI가 1을 포함하지 않아 사후 ANCOVA 수행(p45)")
for cmp_ in ("TEST_vs_US_RP", "EU_RP_vs_US_RP"):
    add(product_id=P, study_key=S, field_id="pk_equiv_concluded", analysis_id="PRIMARY", comparison=cmp_,
        analysis_set="PK_SET", status="REPORTED", value_text="Y", source_id=D, pdf_page=45, locator="Table 5 아래 본문",
        modality="TEXT",
        quote="Therefore, the PK profiles between Pyzchiva and US Stelara and between EU Stelara and US Stelara were equivalent.")
# EXTRA: 사후 ANCOVA (체중 공변량)
adhoc = [("pk_gmr_auc_inf", "TEST_vs_EU_RP", "1.01", "0.92", "1.11", 62, 64, 48, "TEXT",
          "The geometric LSMean ratios (90% CI) between Pyzchiva and EU Stelara for AUCinf and Cmax were 1.01 (0.92 to 1.11) and 0.93 (0.85 to 1.02)"),
         ("pk_gmr_cmax", "TEST_vs_EU_RP", "0.93", "0.85", "1.02", 62, 64, 48, "TEXT",
          "The geometric LSMean ratios (90% CI) between Pyzchiva and EU Stelara for AUCinf and Cmax were 1.01 (0.92 to 1.11) and 0.93 (0.85 to 1.02)"),
         ("pk_gmr_auc_inf", "TEST_vs_US_RP", "1.02", "0.94", "1.12", 62, 60, 46, "IMAGE", None),
         ("pk_gmr_cmax", "TEST_vs_US_RP", "0.96", "0.88", "1.05", 62, 60, 46, "IMAGE", None),
         ("pk_gmr_auc_inf", "EU_RP_vs_US_RP", "1.00", "0.92", "1.10", 64, 60, 46, "IMAGE", None),
         ("pk_gmr_cmax", "EU_RP_vs_US_RP", "1.02", "0.94", "1.12", 64, 60, 46, "IMAGE", None)]
for f, cmp_, v, lo, hi, nt, nr, pg, mod, q in adhoc:
    add(product_id=P, study_key=S, field_id=f, analysis_id="ADHOC_ANCOVA_WT", comparison=cmp_, analysis_set="PK_SET",
        timepoint="Single dose", row_type="EXTRA", status="REPORTED", value_text=f"{v} ({lo}, {hi})", value_num=pct(v),
        ci_low=pct(lo), ci_high=pct(hi), unit="%", n_test=nt, n_ref=nr, source_id=D, pdf_page=pg,
        locator="Table 7 (ad-hoc ANCOVA, 체중 공변량)" if pg == 46 else "Table 8 아래 본문", modality=mod, quote=q,
        alt="p46 Table 7 (이미지)" if pg == 48 else None,
        notes="사후 분석. 1차 분석 값과 혼동 금지 (분석 함정 사례)")
for arm, txt, v in (("TEST", "5/14 (35.7%)", 35.7), ("EU_RP", "12/19 (63.2%)", 63.2), ("US_RP", "11/19 (57.9%)", 57.9)):
    add(product_id=P, study_key=S, field_id="nab_incidence", analysis_id="NAB_AT_TIMEPOINT", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Day 99", row_type="EXTRA", status="REPORTED", value_text=txt, value_num=v,
        unit="%", denominator="ADA_POSITIVE (해당 시점 ADA 양성자)", source_id=D, pdf_page=50, locator="Immunogenicity",
        modality="TEXT", quote="The incidence of subject with NAb at Day 99 was 5/14 (35.7%), 12/19 (63.2%), and 11/19 (57.9%)",
        alt="p49 Table 9 (이미지)", notes="분모가 ADA 양성자 → 다른 제품의 '전체 대상자 분모' NAb와 직접 비교 불가")

# =====================================================================================
# ABP 654 / Wezenla — 20190230
# =====================================================================================
P, S, D = "P_ABP654", "ST_ABP_20190230", "SRC_EPAR_WEZ"
add(product_id=P, study_key=S, field_id="design_phase", status="REPORTED", value_text="phase 1", value_num=1,
    source_id=D, pdf_page=42, locator="PK similarity study (20190230)", modality="TEXT",
    quote="The study was a phase 1, randomised, double-blind, single-dose, 3-arm, parallel-group study in healthy subjects.")
add(product_id=P, study_key=S, field_id="design_population", status="REPORTED",
    value_text="Healthy subjects, 18 to 45 years of age; body weight ≥ 50 kg and ≤ 90 kg; BMI 18.0–30.0 kg/m2 (non-Japanese), 18.0–25.0 kg/m2 (Japanese)",
    source_id=D, pdf_page=41, locator="Tabular overview of clinical studies", modality="IMAGE",
    notes="연령·체중 조건은 개요표(이미지)에만 있음. p42 본문은 'healthy subjects'만 기재")
add(product_id=P, study_key=S, field_id="design_type", status="REPORTED",
    value_text="randomised, double-blind, single-dose, 3-arm, parallel-group", source_id=D, pdf_page=42,
    locator="PK similarity study (20190230)", modality="TEXT",
    quote="The study was a phase 1, randomised, double-blind, single-dose, 3-arm, parallel-group study in healthy subjects.")
add(product_id=P, study_key=S, field_id="dose", status="REPORTED", value_text="90 mg", value_num=90, unit="mg",
    source_id=D, pdf_page=42, locator="PK similarity study (20190230)", modality="TEXT",
    quote="Each subject received either a single 90 mg SC injection of ABP 654, ustekinumab (EU), or ustekinumab (US) on Day 1.",
    notes="SB17·CT-P43(45 mg)과 용량이 다름 → 제품 간 PK 절대값 비교 불가")
add(product_id=P, study_key=S, field_id="route", status="REPORTED", value_text="SC", source_id=D, pdf_page=42,
    locator="PK similarity study (20190230)", modality="TEXT", quote="single 90 mg SC injection")
add(product_id=P, study_key=S, field_id="primary_pk_endpoints", status="REPORTED", value_text="AUCinf; Cmax",
    source_id=D, pdf_page=43, locator="PK endpoints", modality="TEXT", quote="The primary PK parameters: AUCinf and Cmax")
add(product_id=P, study_key=S, field_id="equiv_margin", status="REPORTED", value_text="0.8 to 1.25", unit="ratio",
    source_id=D, pdf_page=44, locator="Table 4 아래 본문", modality="TEXT",
    quote="fully contained within the prespecified margin of 0.8 to 1.25")
add(product_id=P, study_key=S, field_id="ada_assay", status="REPORTED",
    value_text="ADA: MSD-ECL bridging assay with acid dissociation, 3-tiered. NAb: ECL immunoassay (non cell-based)",
    source_id=D, pdf_page=42, locator="Analytical methods", modality="TEXT",
    quote="Anti-drug antibodies were determined by an MSD-ECL method with acid dissociation of the complexes",
    alt="p47 (NAb 분석법: 'not the standard cell-based assay')")
for arm in ("TEST", "EU_RP", "US_RP"):
    add(product_id=P, study_key=S, field_id="n_randomized", arm=arm, analysis_set="RANDOMIZED", status="NOT_FOUND",
        source_id=D, searched="EPAR 텍스트 전체 + p41 개요표(이미지): 군별 무작위배정 수 미기재, 전체 238명만 보고",
        notes="전체 수는 EXTRA 행(arm=ALL)")
add(product_id=P, study_key=S, field_id="n_randomized", arm="ALL", analysis_set="RANDOMIZED", row_type="EXTRA",
    status="REPORTED", value_text="238/237ᵃ (Randomized / Analyzed for Safety)", value_num=238, unit="명",
    source_id=D, pdf_page=41, locator="Tabular overview of clinical studies", modality="IMAGE")
for arm, n in (("TEST", 78), ("EU_RP", 80), ("US_RP", 79)):
    add(product_id=P, study_key=S, field_id="n_pk_set", arm=arm, analysis_set="PK_SET", status="REPORTED",
        value_text=str(n), value_num=n, unit="명", source_id=D, pdf_page=43, locator="PK results", modality="TEXT",
        quote="PK analysis sets included n = 78 in the ABP 654 group, n = 80 in the ustekinumab (EU) group and n = 79 in the ustekinumab (US) group.")
for arm, txt, v, N in (("TEST", "12 (15.4)", 15.4, 78), ("EU_RP", "29 (36.3)", 36.3, 80), ("US_RP", "30 (38.0)", 38.0, 79)):
    add(product_id=P, study_key=S, field_id="ada_incidence", analysis_id="ADA_TREATMENT_EMERGENT", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Through EOS (Day 112)", status="REPORTED", value_text=txt, value_num=v,
        unit="%", denominator=f"ALL_SUBJECTS (N={N})", source_id=D, pdf_page=94,
        locator="Table 42 'Developing antibody incidence … through EOS'", modality="IMAGE",
        notes="정의: 기저치 음성(또는 결과 없음) → 기저치 이후 양성. SB17의 'post-dose positive'와 정의 다름")
for arm, txt, v in (("TEST", "13 (16.7)", 16.7), ("EU_RP", "30 (37.5)", 37.5), ("US_RP", "30 (38.0)", 38.0)):
    add(product_id=P, study_key=S, field_id="ada_incidence", analysis_id="ADA_ANYTIME", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Anytime (기저치 포함)", row_type="EXTRA", status="REPORTED", value_text=txt,
        value_num=v, unit="%", denominator="ALL_SUBJECTS", source_id=D, pdf_page=94,
        locator="Table 42 'Binding antibody positive anytime'", modality="IMAGE", notes="기저치 양성자 포함 정의")
for arm, txt, v in (("TEST", "2 (2.6)", 2.6), ("EU_RP", "6 (7.5)", 7.5), ("US_RP", "10 (12.7)", 12.7)):
    add(product_id=P, study_key=S, field_id="nab_incidence", analysis_id="NAB_TREATMENT_EMERGENT", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Through EOS (Day 112)", status="REPORTED", value_text=txt, value_num=v,
        unit="%", denominator="ALL_SUBJECTS", source_id=D, pdf_page=94,
        locator="Table 42 'Neutralizing antibody positive postbaseline … through EOS'", modality="IMAGE")
abp = {  # field: [(comparison, v, lo, hi, n_test, n_ref)]
    "pk_gmr_auc_inf": [("TEST_vs_EU_RP", "0.9391", "0.8620", "1.0232", 76, 77), ("TEST_vs_US_RP", "1.0124", "0.9289", "1.1034", 76, 76),
                       ("US_RP_vs_EU_RP", "0.9276", "0.8514", "1.0107", 76, 77)],
    "pk_gmr_auc_last": [("TEST_vs_EU_RP", "0.9428", "0.8685", "1.0235", 78, 80), ("TEST_vs_US_RP", "1.0162", "0.9358", "1.1035", 78, 79),
                        ("US_RP_vs_EU_RP", "0.9278", "0.8549", "1.0069", 79, 80)],
    "pk_gmr_cmax": [("TEST_vs_EU_RP", "0.9452", "0.8779", "1.0175", 78, 80), ("TEST_vs_US_RP", "1.0030", "0.9314", "1.0801", 78, 79),
                    ("US_RP_vs_EU_RP", "0.9423", "0.8755", "1.0143", 79, 80)],
}
for f, items in abp.items():
    for cmp_, v, lo, hi, nt, nr in items:
        cross = pct(v) if (cmp_ == "TEST_vs_EU_RP" and f in ("pk_gmr_auc_inf", "pk_gmr_cmax")) else None
        add(product_id=P, study_key=S, field_id=f, analysis_id="PRIMARY", comparison=cmp_, analysis_set="PK_SET",
            timepoint="Single dose", status="REPORTED", value_text=f"{v} ({lo}, {hi})", value_num=pct(v), ci_low=pct(lo),
            ci_high=pct(hi), unit="%", n_test=nt, n_ref=nr, source_id=D, pdf_page=44,
            locator="Table 5 (ANCOVA with baseline weight)", modality="IMAGE", cross_src="PILOT_OBS" if cross else None,
            cross_val=cross,
            notes=("원문 비교 방향 = US/EU (분자 US). 뒤집지 않고 기록" if cmp_.startswith("US_RP") else
                   "원문 scale=ratio. EPAR 주 통계표가 체중 보정 ANCOVA"))
for cmp_ in ("TEST_vs_EU_RP", "TEST_vs_US_RP", "US_RP_vs_EU_RP"):
    add(product_id=P, study_key=S, field_id="pk_equiv_concluded", analysis_id="PRIMARY", comparison=cmp_,
        analysis_set="PK_SET", status="REPORTED", value_text="Y", source_id=D, pdf_page=44, locator="Table 4 아래 본문",
        modality="TEXT",
        quote="Therefore, PK similarity was demonstrated between ABP 654 and ustekinumab (US), ABP 654 and ustekinumab (EU), and ustekinumab (US) and ustekinumab (EU).")

# =====================================================================================
# CT-P43 / Steqeyma — CT-P43 1.1 Part 2, CT-P43 1.2, pooled
# =====================================================================================
P, D = "P_CTP43", "SRC_EPAR_STQ"
for S, ph_q, ph_p, pop_txt, pop_q, pop_p, dt_txt, dose_q, dose_p, route_q, ep_txt, ep_q, ep_p, mg_q, mg_p in (
    ("ST_CTP43_1_1_P2", "Study CT-P43 1.1 was a Phase 1, 2-part, randomised, double-blind, 3-arm, parallel group, single dose study", 31,
     "healthy male subjects", "in healthy male subjects", 31,
     "2-part, randomised, double-blind, 3-arm, parallel group, single dose (Part 2 = PK 유사성)",
     "subjects were randomised in a 1:1:1 ratio to receive a single dose (45 mg/0.5 mL) of CT-P43, EU-approved Stelara, or US-licensed Stelara.", 31,
     "administered subcutaneously via pre-filled syringe (PFS) on Day 1", "AUC0-inf; Cmax",
     "To demonstrate pharmacokinetic (PK) similarity in terms of area under the concentration-time curve from time zero to infinity (AUC0-inf) and maximum serum concentration (Cmax)", 31,
     "were to be contained within the acceptance interval of 80.00% to 125.00% to demonstrate similarity", 31),
    ("ST_CTP43_1_2", "Study CT-P43 1.2 was a Phase I, Randomised, Double-Blind, Parallel Group, Single-Dose Study", 39,
     "Healthy Japanese male subjects", "in Healthy Japanese Male Subjects", 39,
     "Randomised, Double-Blind, Parallel Group, Single-Dose (3군: CT-P43, EU-, US-Stelara)",
     "randomised in a 1:1:1 ratio to receive a single dose (45 mg/0.5 mL) of CT-P43, EU-approved Stelara or US-licensed Stelara.", 39,
     "All subjects received a single dose of study drug subcutaneously via PFS on Day 1", "Cmax; AUC0-inf",
     "as well as to demonstrate similarity between EU-Stelara and US-Stelara, in terms of Cmax and AUC0-inf.", 39,
     "were to be contained within the acceptance interval of 80.00% to 125.00% to demonstrate similarity", 40),
):
    add(product_id=P, study_key=S, field_id="design_phase", status="REPORTED", value_text="Phase 1" if "1_1" in S else "Phase I",
        value_num=1, source_id=D, pdf_page=ph_p, locator="Methods", modality="TEXT", quote=ph_q)
    add(product_id=P, study_key=S, field_id="design_population", status="REPORTED", value_text=pop_txt, source_id=D,
        pdf_page=pop_p, locator="Methods", modality="TEXT", quote=pop_q)
    add(product_id=P, study_key=S, field_id="design_type", status="REPORTED", value_text=dt_txt, source_id=D,
        pdf_page=ph_p, locator="Methods", modality="TEXT", quote=ph_q)
    add(product_id=P, study_key=S, field_id="dose", status="REPORTED", value_text="45 mg/0.5 mL", value_num=45, unit="mg",
        source_id=D, pdf_page=dose_p, locator="Methods", modality="TEXT", quote=dose_q)
    add(product_id=P, study_key=S, field_id="route", status="REPORTED", value_text="SC (PFS)", source_id=D, pdf_page=dose_p,
        locator="Methods", modality="TEXT", quote=route_q)
    add(product_id=P, study_key=S, field_id="primary_pk_endpoints", status="REPORTED", value_text=ep_txt, source_id=D,
        pdf_page=ep_p, locator="Primary objective", modality="TEXT", quote=ep_q)
    add(product_id=P, study_key=S, field_id="equiv_margin", status="REPORTED", value_text="80.00% to 125.00%", unit="%",
        source_id=D, pdf_page=mg_p, locator="Statistical methods", modality="TEXT", quote=mg_q)
    add(product_id=P, study_key=S, field_id="ada_assay", status="REPORTED", value_text="MSD-ECL / MSD-ECL with SPEAD",
        source_id=D, pdf_page=30, locator="Bioanalytical methods", modality="TEXT", quote="(using MSD-ECL and MSD-ECL with SPEAD)",
        notes="ADA·NAb 분석법을 이름으로 구분하지 않음. 두 1상 시험 공통 서술")

S = "ST_CTP43_1_1_P2"
for arm, n in (("TEST", 80), ("EU_RP", 82), ("US_RP", 79)):
    add(product_id=P, study_key=S, field_id="n_randomized", arm=arm, analysis_set="RANDOMIZED", status="REPORTED",
        value_text=str(n), value_num=n, unit="명", source_id=D, pdf_page=32, locator="Results", modality="TEXT",
        quote="A total of 241 subjects were randomly assigned to a treatment group for Part 2 (80 subjects in the CT- P43 treatment group, 82 subjects in the EU-approved Stelara, and 79 subjects in the US-licensed Stelara treatment group).",
        alt="p29 Table 2 (Part 2 Randomised: 241)")
for arm, n in (("TEST", 80), ("EU_RP", 81), ("US_RP", 77)):
    add(product_id=P, study_key=S, field_id="n_pk_set", arm=arm, analysis_set="PK_SET", status="REPORTED",
        value_text=f"N={n}", value_num=n, unit="명", source_id=D, pdf_page=38, locator="Table 8 머리글 (PK set)",
        modality="IMAGE", notes="본문에 PK set 군별 N 서술 없음. Table 6의 Cmax n과 일치")
for arm, txt, v in (("TEST", "13 [16.3%]", 16.3), ("EU_RP", "24 [29.3%]", 29.3), ("US_RP", "34 [43.6%]", 43.6)):
    add(product_id=P, study_key=S, field_id="ada_incidence", analysis_id="ADA_POSTDOSE_ANY", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Through EOS (Day 127)", status="REPORTED", value_text=txt, value_num=v,
        unit="%", denominator="ALL_SUBJECTS (Safety Set Part 2, 군별 N 본문 미기재)", source_id=D, pdf_page=111,
        locator="2.3.8.7 Immunological events", modality="TEXT",
        quote="(13 [16.3%] in the CT-P43 treatment group, 24 [29.3%] in the EU- Stelara treatment group, and 34 [43.6%] subjects in the US- Stelara treatment group)",
        alt="p36 (CT-P43·EU 값만)", notes="정의: 투여 후 1회 이상 ADA 양성")
for arm, txt, v in (("TEST", "5 [6.3%]", 6.3), ("EU_RP", "6 [7.3%]", 7.3), ("US_RP", "9 [11.5%]", 11.5)):
    add(product_id=P, study_key=S, field_id="nab_incidence", analysis_id="NAB_POSTDOSE_ANY", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Through EOS (Day 127)", status="REPORTED", value_text=txt, value_num=v,
        unit="%", denominator="ALL_SUBJECTS", source_id=D, pdf_page=111, locator="2.3.8.7 Immunological events",
        modality="TEXT",
        quote="(5 [6.3%] subjects in the CT-P43 treatment group, 6 [7.3%] subjects in the EU-approved Stelara treatment group, and 9 [11.5%] subjects in the US-licensed Stelara treatment group)")
c11 = {"pk_gmr_auc_inf": [("TEST_vs_EU_RP", 124.36, 115.58, 133.82, 79, 81), ("TEST_vs_US_RP", 126.40, 117.37, 136.13, 79, 77),
                          ("EU_RP_vs_US_RP", 101.64, 94.42, 109.41, 81, 77)],
       "pk_gmr_cmax": [("TEST_vs_EU_RP", 116.18, 108.42, 124.49, 80, 81), ("TEST_vs_US_RP", 112.02, 104.45, 120.13, 80, 77),
                       ("EU_RP_vs_US_RP", 96.42, 89.92, 103.38, 81, 77)]}
for f, items in c11.items():
    for cmp_, v, lo, hi, nt, nr in items:
        add(product_id=P, study_key=S, field_id=f, analysis_id="PRIMARY", comparison=cmp_, analysis_set="PK_SET",
            timepoint="Single dose", status="REPORTED", value_text=f"{v:.2f} ({lo:.2f}, {hi:.2f})", value_num=v, ci_low=lo,
            ci_high=hi, unit="%", n_test=nt, n_ref=nr, source_id=D, pdf_page=35, locator="Table 6 (ANCOVA, PK Set)",
            modality="TEXT", quote=f"({lo:.2f}, {hi:.2f})",
            cross_src="PILOT_OBS" if cmp_ == "TEST_vs_EU_RP" else None, cross_val=v if cmp_ == "TEST_vs_EU_RP" else None,
            notes=("AUC 90% CI 상한 >125% → 동등성 미충족" if (f == "pk_gmr_auc_inf" and cmp_ != "EU_RP_vs_US_RP") else
                   "원문 scale=% (percent)"))
for cmp_ in ("TEST_vs_EU_RP", "TEST_vs_US_RP", "EU_RP_vs_US_RP"):
    add(product_id=P, study_key=S, field_id="pk_gmr_auc_last", analysis_id="PRIMARY", comparison=cmp_,
        analysis_set="PK_SET", timepoint="Single dose", status="NOT_FOUND", source_id=D,
        searched="AUC0-last는 p38 Table 8 기술통계(이미지)만 보고, 기하평균비 미보고")
for cmp_ in ("TEST_vs_EU_RP", "TEST_vs_US_RP"):
    add(product_id=P, study_key=S, field_id="pk_equiv_concluded", analysis_id="PRIMARY", comparison=cmp_,
        analysis_set="PK_SET", status="REPORTED", value_text="N", source_id=D, pdf_page=35, locator="Table 6 아래 본문",
        modality="TEXT",
        quote="However, for AUC0-inf, the upper limit of the 90% CIs of the geometric LS means ratio of CT-P43 to EU- Stelara and that of CT-P43 to US-Stelara were above 125%")
add(product_id=P, study_key=S, field_id="pk_equiv_concluded", analysis_id="PRIMARY", comparison="EU_RP_vs_US_RP",
    analysis_set="PK_SET", status="PENDING", value_text="Y (A 제안)", source_id=D, pdf_page=35, locator="Table 6 아래 본문",
    modality="TEXT",
    quote="However, for AUC0-inf, the upper limit of the 90% CIs of the geometric LS means ratio of CT-P43 to EU- Stelara and that of CT-P43 to US-Stelara were above 125%",
    notes="조정 필요: EU/US 결론을 명시한 문장 없음. 실패한 두 비교만 열거한 문장에서 함의됨. 규칙상 명시 문장 필요 → B 검토자가 판정")
for f, v, lo, hi, nt, nr, lab in (("pk_gmr_auc_inf", 124.59, 116.71, 133.01, 79, 81, "POSTHOC_US_EXCL"),
                                  ("pk_gmr_cmax", 116.50, 109.05, 124.46, 80, 81, "POSTHOC_US_EXCL"),
                                  ("pk_gmr_auc_inf", 114.32, 106.51, 122.71, 58, 49, "POSTHOC_ALL_ADJ"),
                                  ("pk_gmr_cmax", 111.37, 103.03, 120.37, 59, 49, "POSTHOC_ALL_ADJ")):
    add(product_id=P, study_key=S, field_id=f, analysis_id=lab, comparison="TEST_vs_EU_RP", analysis_set="PK_SET",
        timepoint="Single dose", row_type="EXTRA", status="REPORTED", value_text=f"{v:.2f} ({lo:.2f},{hi:.2f})",
        value_num=v, ci_low=lo, ci_high=hi, unit="%", n_test=nt, n_ref=nr, source_id=D, pdf_page=37,
        locator=("Table 7 'Original (unadjusted)' 행 (US 제외 ANCOVA)" if lab == "POSTHOC_US_EXCL" else
                 "Table 7 '단백질 함량 보정 & ADA 음성 & 체중 60–90 kg' 행"), modality="IMAGE",
        notes=("1차 분석(124.36/116.18)과 다른 값 — US군 제외 모델. 분석 함정 사례" if lab == "POSTHOC_US_EXCL" else
               "사후 보정 분석. 허가 판단의 1차 근거 아님"))

S = "ST_CTP43_1_2"
for arm, n, pg, q in (("TEST", 114, 29, "CT-P43: 114"),
                      ("EU_RP", 110, 29, "EU-Stelara: 110"),
                      ("US_RP", 107, 30, "US-Stelara: 107")):
    add(product_id=P, study_key=S, field_id="n_randomized", arm=arm, analysis_set="RANDOMIZED", status="REPORTED",
        value_text=str(n), value_num=n, unit="명", source_id=D, pdf_page=pg, locator="Table 2 Overview (텍스트 표)",
        modality="TEXT", quote=q, alt="p40 (투여 111/108/106)",
        notes="표의 텍스트 층에서 열 순서가 섞여 있음(p29 'Randomised: 331' 뒤에 군별 수). 화면 대조 권장")
for arm, n in (("TEST", 110), ("EU_RP", 105), ("US_RP", 105)):
    add(product_id=P, study_key=S, field_id="n_pk_set", arm=arm, analysis_set="PK_SET", status="REPORTED",
        value_text=f"N={n}", value_num=n, unit="명", source_id=D, pdf_page=45, locator="Table 14 머리글 (PK set)",
        modality="IMAGE", notes="본문에 PK set 군별 N 서술 없음. Table 12의 Cmax n과 일치")
for arm, txt, v in (("TEST", "18 [16.2%]", 16.2), ("EU_RP", "48 [44.4%]", 44.4), ("US_RP", "42 [39.6%]", 39.6)):
    add(product_id=P, study_key=S, field_id="ada_incidence", analysis_id="ADA_POSTDOSE_ANY", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Through EOS (Day 113)", status="REPORTED", value_text=txt, value_num=v,
        unit="%", denominator="ALL_SUBJECTS (Safety Set)", source_id=D, pdf_page=112, locator="2.3.8.7 Immunological events",
        modality="TEXT",
        quote="(18 [16.2%] in the CT-P43 treatment arm, 48 [44.4%] in the EU- Stelara treatment arm, and 42 [39.6%] subjects in the US- Stelara treatment arm)",
        notes="문장이 p111에서 시작해 p112에 값이 있음")
for arm, txt, v in (("TEST", "14 [12.6%]", 12.6), ("EU_RP", "18 [16.7%]", 16.7), ("US_RP", "16 [15.1%]", 15.1)):
    add(product_id=P, study_key=S, field_id="nab_incidence", analysis_id="NAB_POSTDOSE_ANY", arm=arm,
        analysis_set="SAFETY_SET", timepoint="Through EOS (Day 113)", status="REPORTED", value_text=txt, value_num=v,
        unit="%", denominator="ALL_SUBJECTS", source_id=D, pdf_page=112, locator="2.3.8.7 Immunological events",
        modality="TEXT",
        quote="(14 [12.6%] subjects in the CT-P43 treatment arm, 18 [16.7%] subjects in the EU-approved Stelara treatment arm, and 16 [15.1%] subjects in the US-licensed Stelara treatment arm)")
c12 = {"pk_gmr_auc_inf": [("TEST_vs_EU_RP", 108.31, 102.07, 114.94, 109, 105), ("TEST_vs_US_RP", 100.56, 94.76, 106.71, 109, 105),
                          ("EU_RP_vs_US_RP", 92.84, 87.44, 98.57, 105, 105)],
       "pk_gmr_cmax": [("TEST_vs_EU_RP", 101.25, 95.14, 107.76, 110, 105), ("TEST_vs_US_RP", 95.03, 89.29, 101.13, 110, 105),
                       ("EU_RP_vs_US_RP", 93.85, 88.12, 99.96, 105, 105)]}
for f, items in c12.items():
    for cmp_, v, lo, hi, nt, nr in items:
        add(product_id=P, study_key=S, field_id=f, analysis_id="PRIMARY", comparison=cmp_, analysis_set="PK_SET",
            timepoint="Single dose", status="REPORTED", value_text=f"{v:.2f} ({lo:.2f}, {hi:.2f})", value_num=v, ci_low=lo,
            ci_high=hi, unit="%", n_test=nt, n_ref=nr, source_id=D, pdf_page=43, locator="Table 12 (ANCOVA, PK Set)",
            modality="TEXT", quote=f"({lo:.2f}, {hi:.2f})", alt="p44 Table 13 (이미지, 동일 값)",
            cross_src="PILOT_OBS" if cmp_ == "TEST_vs_EU_RP" else None, cross_val=v if cmp_ == "TEST_vs_EU_RP" else None,
            notes="원문 scale=% (percent)")
for cmp_ in ("TEST_vs_EU_RP", "TEST_vs_US_RP", "EU_RP_vs_US_RP"):
    add(product_id=P, study_key=S, field_id="pk_gmr_auc_last", analysis_id="PRIMARY", comparison=cmp_,
        analysis_set="PK_SET", timepoint="Single dose", status="NOT_FOUND", source_id=D,
        searched="p45 Table 14(이미지)는 Tmax·t1/2·%AUCextrap만 보고. AUClast 기하평균비 없음")
    add(product_id=P, study_key=S, field_id="pk_equiv_concluded", analysis_id="PRIMARY", comparison=cmp_,
        analysis_set="PK_SET", status="REPORTED", value_text="Y", source_id=D, pdf_page=44, locator="Table 13 아래 본문",
        modality="TEXT",
        quote="indicating that PK similarity was achieved for CT-P43, EU-approved Stelara and US-licensed Stelara.")

S = "ST_CTP43_POOLED"
for f, lo, hi in (("pk_gmr_auc_inf", 109.55, 120.30), ("pk_gmr_cmax", 102.45, 112.48)):
    add(product_id=P, study_key=S, field_id=f, analysis_id="POOLED_POSTHOC", comparison="TEST_vs_EU_RP",
        analysis_set="PK_SET", timepoint="Single dose", row_type="EXTRA", status="REPORTED",
        value_text=f"90% CI ({lo:.2f}, {hi:.2f}) — 점추정치 미보고", ci_low=lo, ci_high=hi, unit="%", source_id=D,
        pdf_page=51, locator="Post-Hoc pooled analysis", modality="TEXT",
        quote="(AUC0-inf 109.55, 120.30, Cmax 102.45, 112.48)",
        notes="부분 보고: 신뢰구간만 있고 점추정치 없음 → value_num 비움")
