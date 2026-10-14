#!/usr/bin/env python3
"""Build the retrieval query set from the gold values by fixed templates.

Nobody has supplied real user questions, so every question here is generated from a gold row group
by rule. Each question comes in two Korean phrasings (abbreviations kept / terms spelled out) plus
an English reference, so the evaluation can show how much the result depends on phrasing.
The gold set is an AI draft and covers the three EMA assessment reports only.
"""
import collections
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eval" / "queries_v0.1.csv"

STUDIES = {
    "ST_SB17_1001": {"product": "SB17", "doc": "EMA:63563", "a": "SB17-1001 시험에서", "b": "SB17의 건강인 대상 1상 약동학 시험에서",
                     "en": "SB17 phase 1 PK study SB17-1001"},
    "ST_ABP_20190230": {"product": "ABP654", "doc": "EMA:64196", "a": "ABP 654 20190230 시험에서", "b": "ABP 654의 건강인 대상 1상 약동학 시험에서",
                        "en": "ABP 654 phase 1 PK study 20190230"},
    "ST_CTP43_1_1_P2": {"product": "CTP43", "doc": "EMA:64792", "a": "CT-P43 1.1 Part 2 시험에서", "b": "CT-P43의 1.1 시험 파트 2에서",
                        "en": "CT-P43 phase 1 PK study CT-P43 1.1 Part 2"},
    "ST_CTP43_1_2": {"product": "CTP43", "doc": "EMA:64792", "a": "CT-P43 1.2 시험에서", "b": "CT-P43의 1.2 시험에서",
                     "en": "CT-P43 phase 1 PK study CT-P43 1.2"},
    "ST_CTP43_POOLED": {"product": "CTP43", "doc": "EMA:64792", "a": "CT-P43 1.1 Part 2와 1.2 통합 분석에서", "b": "CT-P43의 두 1상 시험을 합친 통합 분석에서",
                        "en": "CT-P43 pooled analysis of studies CT-P43 1.1 Part 2 and CT-P43 1.2"},
}

FIELD_TOPIC = {
    "design_phase": "design", "design_type": "design", "design_population": "population", "dose": "dosing", "route": "dosing",
    "primary_pk_endpoints": "endpoints", "equiv_margin": "margin", "ada_assay": "assay", "n_randomized": "n_randomized",
    "n_pk_set": "n_pk_set", "ada_incidence": "ada", "nab_incidence": "nab", "pk_gmr_auc_inf": "gmr", "pk_gmr_cmax": "gmr",
    "pk_gmr_auc_last": "gmr", "pk_equiv_concluded": "equivalence",
}

# topic -> (Korean with abbreviations, Korean spelled out, English reference). {s} is the study phrase.
SIMPLE = {
    "design": ("{s} 시험 설계(단계, 무작위배정, 눈가림, 군 구성)는?",
               "{s} 몇 상 시험이고 무작위배정, 눈가림, 투여군 구성은 어떻게 되어 있나?",
               "{s}: study design (phase, randomisation, blinding, treatment arms)"),
    "population": ("{s} 대상자 선정기준(연령, 체중, BMI)은?",
                   "{s} 어떤 사람들을 대상으로 했나? 나이, 체중, 체질량지수 기준은?",
                   "{s}: study population and key inclusion criteria (age, body weight, BMI)"),
    "dosing": ("{s} 투여 용량과 투여 경로는?",
               "{s} 약을 얼마나, 어떤 경로로 투여했나?",
               "{s}: dose and route of administration"),
    "endpoints": ("{s} 1차 PK 평가변수는?",
                  "{s} 일차 약동학 평가변수는 무엇인가?",
                  "{s}: primary pharmacokinetic endpoints"),
    "margin": ("{s} PK 동등성 한계(equivalence margin)는?",
               "{s} 약동학 동등성을 판정하는 허용 범위는?",
               "{s}: predefined equivalence margin for PK similarity"),
    "assay": ("{s} ADA와 NAb 분석법은?",
              "{s} 항약물항체와 중화항체를 어떤 분석법으로 측정했나?",
              "{s}: assay methods for anti-drug antibodies (ADA) and neutralising antibodies (NAb)"),
    "n_randomized": ("{s} 군별 무작위배정 인원은?",
                     "{s} 각 투여군에 몇 명이 무작위배정되었나?",
                     "{s}: number of subjects randomised per treatment arm"),
    "n_pk_set": ("{s} 군별 PK 분석군(PK set) 인원은?",
                 "{s} 약동학 분석에 포함된 대상자 수는 군별로 몇 명인가?",
                 "{s}: number of subjects in the PK analysis set per treatment arm"),
    "equivalence": ("{s} PK 동등성이 입증되었나? (90% CI가 동등성 한계 이내인지)",
                    "{s} 약동학적으로 대조약과 동등하다는 결론이 났나?",
                    "{s}: was PK equivalence concluded (90% CIs within the equivalence margin)"),
}

IMMUNO = {
    "ADA_POSTDOSE_ANY": ("{s} 투여 후 ADA 양성률(군별)은?",
                         "{s} 투여 후 항약물항체가 생긴 대상자 비율은 군별로 얼마인가?",
                         "{s}: incidence of post-dose anti-drug antibody (ADA) positive subjects by treatment arm"),
    "ADA_TREATMENT_EMERGENT": ("{s} EOS까지 투여 후 발생한 ADA 발생률(군별)은?",
                               "{s} 투여 후 새로 항약물항체가 생긴 대상자 비율은 시험 종료 시점까지 군별로 얼마인가?",
                               "{s}: treatment-emergent anti-drug antibody incidence through end of study by treatment arm"),
    "ADA_ANYTIME": ("{s} 기저치 포함 전 기간 ADA 양성률(군별)은?",
                    "{s} 기저치를 포함해 한 번이라도 항약물항체 양성이었던 대상자 비율은 군별로 얼마인가?",
                    "{s}: incidence of subjects positive for binding anti-drug antibodies at any time, including baseline, by treatment arm"),
    "NAB_POSTDOSE_ANY": ("{s} 투여 후 NAb 양성률(군별)은?",
                         "{s} 투여 후 중화항체 양성 대상자 비율은 군별로 얼마인가?",
                         "{s}: incidence of post-dose neutralising antibody (NAb) positive subjects by treatment arm"),
    "NAB_AT_TIMEPOINT": ("{s} Day 99 NAb 양성률(ADA 양성자 중, 군별)은?",
                         "{s} 99일째에 항약물항체 양성자 가운데 중화항체 양성 비율은 군별로 얼마인가?",
                         "{s}: neutralising antibody (NAb) positive subjects among ADA-positive subjects at Day 99 by treatment arm"),
    "NAB_TREATMENT_EMERGENT": ("{s} EOS까지 투여 후 NAb 양성률(군별)은?",
                               "{s} 시험 종료 시점까지 투여 후 중화항체 양성이 된 대상자 비율은 군별로 얼마인가?",
                               "{s}: post-baseline neutralising antibody positive incidence through end of study by treatment arm"),
}

ENDPOINT = {"pk_gmr_auc_inf": ("AUCinf", "무한대까지의 혈중농도-시간곡선하면적", "AUCinf"),
            "pk_gmr_cmax": ("Cmax", "최고혈중농도", "Cmax"),
            "pk_gmr_auc_last": ("AUClast", "마지막 측정 시점까지의 혈중농도-시간곡선하면적", "AUClast")}
COMPARISON = {"TEST_vs_EU_RP": ("EU Stelara 대비", "유럽 허가 대조약 대비", "versus EU-approved Stelara"),
              "TEST_vs_US_RP": ("US Stelara 대비", "미국 허가 대조약 대비", "versus US-licensed Stelara"),
              "EU_RP_vs_US_RP": ("EU Stelara와 US Stelara 간", "유럽 대조약과 미국 대조약 사이", "EU-approved Stelara versus US-licensed Stelara"),
              "US_RP_vs_EU_RP": ("EU Stelara와 US Stelara 간", "유럽 대조약과 미국 대조약 사이", "US-licensed Stelara versus EU-approved Stelara")}
ALL_COMPARISONS = ("EU·US Stelara 대비 및 두 대조약 간", "유럽·미국 대조약 대비, 그리고 두 대조약 사이",
                   "versus EU-approved and US-licensed Stelara, and between the two reference products")
ANALYSIS = {"PRIMARY": ("", "", "primary analysis"),
            "ADHOC_ANCOVA_WT": ("사후 ANCOVA(체중 공변량) 분석의 ", "체중을 공변량으로 보정한 사후 분석의 ", "ad hoc ANCOVA with body weight as covariate"),
            "POSTHOC_US_EXCL": ("US 대조약군을 제외한 사후 ANCOVA(보정 전)의 ", "미국 대조약군을 빼고 다시 분석한 사후 분석(보정 전)의 ",
                                "post hoc ANCOVA excluding the US-licensed Stelara arm, unadjusted"),
            "POSTHOC_ALL_ADJ": ("단백질 함량 보정, ADA 음성, 체중 60~90 kg 조건 사후 분석의 ", "단백질 함량을 보정하고 항약물항체 음성이며 체중 60~90 kg인 대상자로 한정한 사후 분석의 ",
                                "post hoc analysis adjusted for protein content in ADA-negative subjects weighing 60 to 90 kg"),
            "POOLED_POSTHOC": ("사후 통합 분석의 ", "사후 통합 분석의 ", "post hoc pooled analysis")}


def join3(parts, sep_ko=", ", last_en=" and "):
    a = sep_ko.join(p[0] for p in parts)
    b = sep_ko.join(p[1] for p in parts)
    en = [p[2] for p in parts]
    return a, b, (", ".join(en[:-1]) + last_en + en[-1]) if len(en) > 1 else en[0]


def gmr_text(study, analysis, comparisons, fields):
    ends = join3([ENDPOINT[f] for f in sorted(fields, key=list(ENDPOINT).index)])
    comp = ALL_COMPARISONS if len({COMPARISON[c][0] for c in comparisons}) == 3 else join3([COMPARISON[c] for c in comparisons], last_en=", and ")
    an = ANALYSIS[analysis]
    s = STUDIES[study]
    return (f"{s['a']} {an[0]}{comp[0]} {ends[0]}의 기하평균비(GMR)와 90% CI는?",
            f"{s['b']} {an[1]}{comp[1]} {ends[1]}의 기하평균비와 90% 신뢰구간은 얼마인가?",
            f"{s['en']}: geometric mean ratio and 90% CI of {ends[2]} {comp[2]} ({an[2]})")


def proximity_pages(db, doc, rows):
    """Other pages of the same report where a row's value and both CI bounds sit within 60 characters."""
    texts = collections.defaultdict(str)
    for page, text in db.execute("select pdf_page, text from pages where document_id=?", (doc,)):
        texts[page] += " " + (text or "")
    for page, text in db.execute("select pdf_page, text from ocr_regions where document_id=?", (doc,)):
        texts[page] += " " + (text or "")
    found = set()
    for r in rows:
        nums = re.findall(r"\d+\.\d+", r["value_text"])
        if len(nums) < 3:
            continue
        pattern = re.compile(re.escape(nums[0]) + r"\D{0,25}" + re.escape(nums[-2]) + r"\D{0,25}" + re.escape(nums[-1]))
        for page, text in texts.items():
            if pattern.search(re.sub(r"\s+", " ", text)):
                found.add(page)
    return found


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = list(csv.DictReader(open(ROOT / "gold/gold_values_v0.2.csv", encoding="utf-8-sig")))
    db = sqlite3.connect(f"file:{(ROOT / 'data/collection.sqlite').as_posix()}?mode=ro", uri=True)
    units = collections.defaultdict(list)
    for r in rows:
        topic = FIELD_TOPIC[r["field_id"]]
        analysis = r["analysis_id"] if topic in ("ada", "nab", "gmr") else "NA"
        comparison = r["comparison"] if topic == "gmr" else "NA"
        found = r["status"] in ("REPORTED", "PENDING")
        units[(r["study_key"], topic, analysis, comparison, found)].append(r)

    # GMR units of one analysis that point at the same pages become one question listing the comparisons.
    merged = collections.defaultdict(list)
    for (study, topic, analysis, comparison, found), rs in units.items():
        pages = frozenset(int(r["pdf_page"]) for r in rs if r["pdf_page"])
        key = (study, topic, analysis, found, pages if topic == "gmr" else None)
        merged[key].append((comparison, rs))

    out = []
    for (study, topic, analysis, found, _), parts in merged.items():
        rs = [r for _, group in parts for r in group]
        s = STUDIES[study]
        if topic == "gmr":
            comparisons = sorted({c for c, _ in parts}, key=list(COMPARISON).index)
            ko_a, ko_b, en = gmr_text(study, analysis, comparisons, {r["field_id"] for r in rs})
        elif topic in ("ada", "nab"):
            comparisons = []
            ko_a, ko_b, en = (t.format(s=s["a" if i == 0 else "b" if i == 1 else "en"]) for i, t in enumerate(IMMUNO[analysis]))
        else:
            comparisons = []
            ko_a, ko_b, en = (t.format(s=s["a" if i == 0 else "b" if i == 1 else "en"]) for i, t in enumerate(SIMPLE[topic]))
        gold_pages = sorted({int(r["pdf_page"]) for r in rs if r["pdf_page"]})
        if found and not gold_pages:
            continue
        # A unit with both found and not-found rows (per-arm values missing, total present) stays one found question.
        if not found and any(k[:3] == (study, topic, analysis) and k[3] for k in merged if k[4] is None):
            continue
        alt = sorted({int(p) for r in rs for p in re.findall(r"p(\d+)", r["alt_locations"])} - set(gold_pages))
        prox = sorted(proximity_pages(db, s["doc"], rs) - set(gold_pages) - set(alt)) if topic == "gmr" and found else []
        modality = sorted({r["modality"] for r in rs if r["modality"]})
        out.append({
            "type": "retrieval" if found else "absent", "product_id": s["product"], "study_key": study, "topic": topic,
            "analysis_id": analysis, "comparisons": ";".join(comparisons), "document_id": s["doc"],
            "gold_pages": json.dumps(gold_pages), "alt_location_pages": json.dumps(alt), "proximity_pages": json.dumps(prox),
            "acceptable_pages": json.dumps(sorted(set(gold_pages) | set(alt) | set(prox))),
            "modality": "MIXED" if len(modality) > 1 else (modality[0] if modality else ""),
            "gold_ids": ";".join(r["gold_id"] for r in rs), "ko_a": ko_a, "ko_b": ko_b, "en_ref": en,
            "expected_note": "" if found else "EMA 심사보고서에는 없음: " + "; ".join(sorted({r["searched_scope"] for r in rs if r["searched_scope"]}))[:300],
        })
    order = {k: i for i, k in enumerate(STUDIES)}
    out.sort(key=lambda q: (q["type"] != "retrieval", order[q["study_key"]], list(dict.fromkeys(FIELD_TOPIC.values())).index(q["topic"]), q["analysis_id"]))
    for i, q in enumerate(out, 1):
        q["query_id"] = f"Q{i:03d}"
    cols = ["query_id"] + [c for c in out[0] if c != "query_id"]
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    summary = {
        "queries": len(out), "retrieval": sum(q["type"] == "retrieval" for q in out), "absent": sum(q["type"] == "absent" for q in out),
        "by_product": dict(collections.Counter(q["product_id"] for q in out if q["type"] == "retrieval")),
        "by_topic": dict(collections.Counter(q["topic"] for q in out if q["type"] == "retrieval")),
        "by_modality": dict(collections.Counter(q["modality"] for q in out if q["type"] == "retrieval")),
        "with_extra_acceptable_pages": sum(q["type"] == "retrieval" and q["acceptable_pages"] != q["gold_pages"] for q in out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
