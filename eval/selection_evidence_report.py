"""Build the reviewable Korean evidence tables from the completed frozen run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "data/runs/selection_evidence_2026-10-19"
DOC = ROOT / "docs/evaluation/CURRENT_SERVING_COMPARISON_2026-10-19.md"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def f(value):
    return f"{value:.3f}"


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers),
                       *["| " + " | ".join(str(v) for v in row) + " |" for row in rows]])


def main():
    result = read(RUN / "results.json")
    pure = read(RUN / "channel_baselines.json")
    verify = read(RUN / "verification.json")
    if result["items"] != 100 or pure["items"] != 100 or not verify["passed"]:
        raise ValueError("Only a complete verified 100-item comparison may be reported")
    core = pure["summary"]["all"]
    current = result["summary"]["all"]
    names = {"dense_channel": "Dense 후보 단독", "bm25_channel": "BM25 후보 단독",
             "hybrid_unstripped": "Hybrid, 공통어 제거 전", "hybrid_current": "Hybrid, 공통어 제거 적용"}
    first = table(["1차 후보 검색", "후보 Recall@40", "1차 Recall@8", "기존 nDCG@8", "기존 MRR"],
        [[names[k], *[f(core[k][m]) for m in ["recall@40", "recall@8", "ndcg@8", "mrr"]]] for k in names])
    second_names = [("hybrid_current:none", "현재 1차 + 리랭커 없음"),
                    ("hybrid_current:ask_rrf", "BGE + 한국어 사실 질의 + RRF"),
                    ("hybrid_current:fquery_rrf", "현재 BGE + 영어 사실 질의 + RRF"),
                    ("hybrid_current:fquery_only", "같은 영어 사실 질의, rerank-only")]
    second = table(["동일한 현재 후보 풀의 2차 구성", "Recall@8", "기존 nDCG@8", "기존 MRR", "후보 풀 상한"],
        [[label, *[f(current[k][m]) for m in ["recall@8", "ndcg@8", "mrr", "pool_ceiling"]]] for k, label in second_names])
    integrated = table(["서빙 공통 보정 유지, 후보 채널만 변경", "후보 Recall@40", "2차 전 Recall@8", "같은 2차 적용 후 Recall@8", "적용 후 nDCG@8", "적용 후 MRR"],
        [[names[k], f(current[k + ":none"]["recall@40"]), f(current[k + ":none"]["recall@8"]),
          *[f(current[k + ":fquery_rrf"][m]) for m in ["recall@8", "ndcg@8", "mrr"]]] for k in names])
    subgroups = table(["질문 출처", "정답 묶음", "현재 1차 Recall@8", "현재 2차 Recall@8", "2차 nDCG@8", "2차 MRR", "후보 풀 상한"],
        [[scope, result["summary"][scope]["hybrid_current:none"]["groups"],
          f(result["summary"][scope]["hybrid_current:none"]["recall@8"]),
          *[f(result["summary"][scope]["hybrid_current:fquery_rrf"][m])
            for m in ["recall@8", "ndcg@8", "mrr", "pool_ceiling"]]] for scope in ["fact_gold", "qa_chemr", "e2e29"]])
    differences = result["paired_question_bootstrap"]["comparisons"]
    ci_rows = []
    for d in differences:
        if d["chosen"] == "hybrid_current:fquery_rrf" and d["baseline"] == "hybrid_current:none":
            ci_rows.append([d["metric"], f"{d['difference']:+.3f}",
                f"[{d['bootstrap95'][0]:+.3f}, {d['bootstrap95'][1]:+.3f}]",
                f"{d['questions_improved']} / {d['questions_worse']} / {d['questions_equal']}"])
    ci_table = table(["현재 2차 − 1차 순위", "차이", "질문단위 bootstrap 95% 구간", "개선 / 악화 / 동일 문항"], ci_rows)
    pure_ci = read(RUN / "channel_baselines_ci.json")
    ci1 = table(["현재 hybrid 후보 회수 − 단독 채널", "Recall@40 차이", "질문단위 bootstrap 95% 구간", "개선 / 악화 / 동일 문항"],
        [[d["baseline"], f"{d['difference']:+.3f}", f"[{d['bootstrap95'][0]:+.3f}, {d['bootstrap95'][1]:+.3f}]",
          f"{d['improved']} / {d['worse']} / {d['equal']}"]
         for d in pure_ci["comparisons"] if d["metric"] == "recall@40"])
    rows = [read(p) for p in sorted((RUN / "rows").glob("*.json"))]
    ranks = {}
    for key, label in [("hybrid_current:none", "2차 전"), ("hybrid_current:fquery_rrf", "현재 2차 후")]:
        facts = [g for r in rows for g in r["variants"][key]["groups"] if not g["weak"]]
        ranks[label] = [sum(g["rank"] == 1 for g in facts),
                        sum(g["rank"] is not None and 2 <= g["rank"] <= 8 for g in facts),
                        sum(g["rank"] is not None and g["rank"] > 8 for g in facts),
                        sum(g["rank"] is None for g in facts)]
    rank_table = table(["정답의 최선 순위", "2차 전", "현재 2차 후"],
        [[label, ranks["2차 전"][i], ranks["현재 2차 후"][i]]
         for i, label in enumerate(["1위", "2~8위", "9~40위", "후보 풀 밖"])])
    by_id = {r["id"]: r for r in rows}
    protection = []
    for key in ["fda_approval_date:BAT2206", "excipients:ABP654", "excipients:BAT2206"]:
        r = by_id[key]
        label = f"[{key}](../../data/runs/selection_evidence_2026-10-19/rows/{key.replace(':', '_')}.json)"
        protection.append([label, *[r["variants"][variant]["groups"][0]["rank"] for variant in
            ["hybrid_current:none", "hybrid_current:fquery_only", "hybrid_current:fquery_rrf"]]])
    protection_table = table(["기존 검색 강점 보존 사례", "1차 순위", "rerank-only 순위", "현재 RRF 순위"], protection)
    new = current["hybrid_current:fquery_rrf"]
    old = current["hybrid_current:none"]
    facts = [g for r in rows for g in r["variants"]["hybrid_current:fquery_rrf"]["groups"] if not g["weak"]]
    found = sum(g["rank"] is not None for g in facts)
    top8 = sum(g["rank"] is not None and g["rank"] <= 8 for g in facts)
    narrative = f"""# 현재 서빙 구성의 전처리·1차·2차 선정 근거와 재평가

작성: 2026-10-19 KST. 기존 선정 근거에 **현재 계획기를 사용한 동일 문항 비교**를 추가했다. 전처리 구성·검색 기본값·릴리스·서비스 프로세스는 변경하지 않았다. 이번에는 답변을 새로 생성하지 않고 계획·검색·재정렬을 평가했다.

현재 서빙 기준은 `ra-research-rag-4.10.3`, 계획기 `ra-research-tasks-8-fact-queries`, 릴리스04·111문서·15,682청크, pgvector다. 제품 dense/BM25, 역할 보정, 후보40·최종8과 현재 BGE 리랭커 설정을 고정했다. 대상은 단일 질문의 정규1·2차 검색 구간이다. 생성 전 추가 보완 검색·문맥 패킹·답변 생성은 이번 재평가에 포함하지 않는다.

- **H 선택 근거:** 같은 기본 추출을 공유한 직접 비교에서 셀79→88, 기존 성공 셀 손실0.
- **하이브리드 후보 회수:** dense .821 / BM25 .864 / hybrid .889.
- **현재 재정렬 효과:** Recall@8 {old['recall@8']:.3f}→{new['recall@8']:.3f}, nDCG {old['ndcg@8']:.3f}→{new['ndcg@8']:.3f}, MRR {old['mrr']:.3f}→{new['mrr']:.3f}.
- 영어 질의·공통어 제거·RRF의 세부 선택은 관측된 맞교환으로 설명한다. 모든 비교 지표의 최상이라는 주장은 사용하지 않는다.

## 1. 전처리 H의 준비된 선정 근거

| 구성 | 고정 셀 /90 | 숫자·단위·기호 /34 | 표 영역 /18 | 그림 보존 /4 |
|---|---:|---:|---:|---:|
| 기본 추출·보정 C | 73 | 29 | 12 | 3 |
| 누락 영역 보완 G′ | 79 | 31 | 16 | 3 |
| **현재 표준 H** | **88** | **32** | **18** | **4** |

같은 새 기본 추출을 공유한 G′/H 직접 비교에서 셀9개를 추가 회복하고 기존 성공 셀 손실0을 확인했다. 시간은84.78→107.69초이며 공유 공통 단계+분기 실측 합산이다. 이 근거는 정상 기본 추출을 유지하면서 누락·불완전 영역과 그림 보존을 보완하는 H의 선택을 지지한다.

원문·기본 결과·H 결과를 보여줄 FDA 누락 comment, FDA 다군 표 복원, HC 그림 보존 사례는 [선정 근거 보고서](SELECTION_EVIDENCE_BRIEF_2026-10-19.md)에 연결했다. [전처리 최종 선정](../preprocessing/FINAL_PREPROCESSING_DECISION_2026-10-14.md)의 기존 결과를 사용했으며 이번에 전처리를 재실행하지 않았다.

## 2. 이번 재평가의 고정 조건

- 동일한 검색 골든셋100문항·265정답 묶음을 사용했다. 주 지표는 기존과 동일하게 weak30묶음을 제외한235묶음이다.
- E01·N01은 weak 묶음만 있어 주 지표 분모가0이다. 실행·함수 동일성 검사는100문항 모두 수행했고, 문항별 개선/악화/동일과 bootstrap은 주 지표를 채점할 수 있는98문항으로 집계했다. 이 처리는 모든 비교 행에 동일하다.
- 저장 응답29개는 현재 **전체 요청 해시**(메시지·문서 목록·스키마·모델·출력 설정)가 일치함을 확인하고 현재 코드로 정규화해 재사용했다. 원기록의4.10.2/계획기7 표기를 임의로 현재 버전으로 바꾼 것이 아니다.
- 나머지71개는 현재 계획기로 한 번씩 새로 생성했다. 모든 비교에서 같은 고정 계획·질문·UI 범위·정답을 사용했다. 결과를 보고 계획이나 파라미터를 수정하지 않았다.
- 질의 벡터는 서비스와 같은CUDA/fp16 BGE 경로에서 실제 문항별 질의 배치로 계산해 동결했다. 검색 DB는 서비스와 같은pgvector 경로다.
- 2차는 같은 후보 풀에서 없음/한국어 사실 질의+RRF/영어 사실 질의+RRF/영어 사실 질의 rerank-only를 비교했다. 현재 모델의 동일 revision·입력길이1024를 사용했다. 평가 배치는GPU 공유를 위해4다.
- 현재 방식의 상위8 결과가 수정하지 않은 서비스 검색 함수의 출력과 **100/100문항에서 일치**했다. 같은 동결 벡터와 점수 캐시에서의 함수 출력 비교다.

## 3. 1차 후보 검색 비교: 단독 채널과 hybrid

통계표 우선 공급을 모든 행에서 끄고 제품 후보 검색 채널을 비교했다. 필터·역할 보정·질의·후보 예산은 같다. 공통 지침의 BM25 경로는 공통으로 유지한다. 따라서 BM25 단독 행에 dense 표 보정 효과를 섞지 않는다.

{first}

1차의 주목적은 **2차에 전달할 정답 후보를 확보하는 것**이므로 후보 Recall@40을 주 지표로 해석한다. 상위8·순위 지표는 함께 제시하여 맞교환을 보존한다. 같은235묶음에서 현재 hybrid 후보 회수는 dense보다{(core['hybrid_current']['recall@40']-core['dense_channel']['recall@40'])*100:.1f}%p, BM25보다{(core['hybrid_current']['recall@40']-core['bm25_channel']['recall@40'])*100:.1f}%p 높았다.

{ci1}

공통어 제거 전후 후보 회수는 이번 조건에서 동일했다. 과거22문항의선정 실험(.855→.882)과 이번100문항 결과를 구분한다. 현재 결과로 모든 지표에서 공통어 제거의 우위를 새로 주장하지 않는다. [단독 채널 원결과](../../data/runs/selection_evidence_2026-10-19/channel_baselines.json), [질문별 차이 구간](../../data/runs/selection_evidence_2026-10-19/channel_baselines_ci.json).

## 4. 2차 재정렬 비교: 실제 서빙 후보 풀 고정

아래는 통계표 우선 공급을 포함한 현재1차 후보 풀을 그대로 공유한다.

{second}

현재 영어 사실 질의+RRF는 2차 없음 대비 Recall@8 **{(new['recall@8']-old['recall@8'])*100:+.1f}%p**, 기존 nDCG **{new['ndcg@8']-old['ndcg@8']:+.4f}**, 기존 MRR **{new['mrr']-old['mrr']:+.4f}**였다. 같은 풀의 정답 {found}묶음 중 {top8}묶음({top8/found*100:.1f}%)을 8위 안에 배치했다. 변화량은 반올림 전 수치로 계산했다.

영어 질의는 한국어 질의 대비Recall@8은{(new['recall@8']-current['hybrid_current:ask_rrf']['recall@8'])*100:+.1f}%p, nDCG는{new['ndcg@8']-current['hybrid_current:ask_rrf']['ndcg@8']:+.3f}, MRR는{new['mrr']-current['hybrid_current:ask_rrf']['mrr']:+.3f}였다. 따라서현재 영어 질의 선택은 앞쪽 순위 품질을 우선한 절충으로 설명한다. 이번 조건의 recall 단독 최상이라는 주장은 사용하지 않는다.

{rank_table}

{ci_table}

각 지표는 기존 평가 정의를 그대로 사용했다. nDCG는 정답 사실별 최선 순위의할인 평균, MRR는 사실별 최선 순위의역수 평균이며 일반적인 질문별 지표와 평가 단위가 다르다. 이번 비교를 위해 산식을 유리하게 변경하지 않았다. [전체 재평가 원결과](../../data/runs/selection_evidence_2026-10-19/results.json).

같은 영어 사실 질의에서RRF와rerank-only의 맞교환도 전체 표에 남겼다. 다음 사례는1차가 강하게 찾은 짧은 사실을순수 재정렬이8위 밖으로 밀었을 때RRF가 이를 유지하는 역할을 보여준다. 이 사례로모든 문항에서RRF가 우월하다고 일반화하지 않는다.

{protection_table}

근거는 해당 문항의 [개별 순위 기록 폴더](../../data/runs/selection_evidence_2026-10-19/rows)에 있다.

이번 전체 평균에서는 같은 영어 질의를 사용한rerank-only의Recall@8·nDCG·MRR가현재RRF보다 높았다. 위 사례는 특정 사실을 보존한 근거이며, 그 이득과 다른 문항의 손실을 함께 고려해야 한다. **현재 재정렬의 적용 효과는 확인됐고, RRF가 전체 평균에서도 최적이라는 주장은 이번 결과로 지지되지 않는다.** 서비스 설정은 유지했다.

## 5. 1·2차 연결 비교

이 표는 실제 서비스의 공통 통계표 보정·필터·역할 보정을 유지하고 후보 채널만 바꾼 소거 비교다. 3절의 순수 채널 표와 구분한다. dense 후보를 끄더라도 공통 통계표 모듈의dense 동점 판정은 남아 있다.

{integrated}

## 6. 질문 출처별 현재 구성 결과

{subgroups}

전체 평균과 함께 출처별 결과를 제시한다. QA_candidates에는 제품·기관이 명시되지 않은 질문이 포함되어 실제 서비스의 범위 해석이 반영된다. 이번에는 그 질문을 유리하게 고치거나 범위를 정답 문서로 고정하지 않았다.

## 7. 보고·해석 범위

- 기존 .843은4.10.2 고정 계획의선정 실험이고 이번 수치는 현재 요청을 사용하는 별도 재평가다. 계획·백엔드·실제 서비스 함수 경계를 맞춘 이번 조건의 결과로 제시한다. 과거 대비 차이를 계획기 하나의 인과 효과로 계산하지 않는다.
- 같은 페이지의 다른 청크도 기존 정답 규칙에서hit가 될 수 있다. 본 표는 페이지/사실 수준 검색 평가이며 전체 답변 정확도와 별도다.
- 답변 루브릭317/384는 기존29문항×2회 결과다. 이번에 새 답변 생성·전문가 정답 판정을 수행한 값으로 바꾸지 않는다.
- 입력 벡터와 리랭커 점수 캐시를 공유했으므로 이번 실행 시간은진단 기록이다. 속도 우위 근거는 기존 조건이 명시된 모델·풀 크기 실험을 사용한다.
- 비교의 모든 행·출처별 결과·개선/악화 문항을 남겼다. 기존 선택의 직접 근거와 추가로 확정하기 어려운 세부 선택을 구분한다.

## 8. 검증과 재현

검증: 실제 검색 함수상위8 일치100/100, 원본gold·weak 구분 불변, 기존nDCG 채점 함수와 일치, 제품·검색 소스 해시 불변, 순수BM25의dense DB 질의0, 단독 채널의별도 native top40 순위 일치를 확인했다.

- [검증 결과](../../data/runs/selection_evidence_2026-10-19/verification.json)
- [계획 준비·요청 동일성](../../data/runs/selection_evidence_2026-10-19/preparation.json)
- [현재 평가 설정](../../data/runs/selection_evidence_2026-10-19/evaluation_identity.json)
- [신규 계획 호출·토큰](../../data/runs/selection_evidence_2026-10-19/planner_usage.json)
- [평가 스크립트](../../eval/selection_evidence_eval.py)
- [표 생성 스크립트](../../eval/selection_evidence_report.py)

```powershell
.\\.venv\\Scripts\\python.exe -X utf8 -u -B eval/selection_evidence_eval.py --out data/runs/selection_evidence_2026-10-19 --phase evaluate
.\\.venv\\Scripts\\python.exe -X utf8 -u -B eval/selection_evidence_eval.py --out data/runs/selection_evidence_2026-10-19 --phase channels
.\\.venv\\Scripts\\python.exe -X utf8 -u -B eval/selection_evidence_eval.py --out data/runs/selection_evidence_2026-10-19 --phase summarize
.\\.venv\\Scripts\\python.exe -X utf8 -u -B eval/selection_evidence_eval.py --out data/runs/selection_evidence_2026-10-19 --phase verify
.\\.venv\\Scripts\\python.exe -X utf8 -B eval/selection_evidence_report.py
```

기존 frozen plans/query_vectors/rows/점수 캐시를 재사용한다. 새 계획을 호출하는 `--allow-new-plans`는재현 명령에 포함하지 않았다. 검증된 준비·검색 결과를 다시 출력하는 데 새 생성 API 호출은 필요하지 않다.
"""
    for before, after in {
        "문항의선정": "문항의 선정", "사실별 최선 순위의할인": "사실별 최선 순위의 할인",
        "사실별 최선 순위의역수": "사실별 최선 순위의 역수", "따라서현재": "따라서 현재",
        "에서RRF": "에서 RRF", "와rerank-only": "와 rerank-only", "사실을순수": "사실을 순수",
        "때RRF": "때 RRF", "사례로모든": "사례로 모든", "문항에서RRF": "문항에서 RRF",
        "사용한rerank-only": "사용한 rerank-only", "의Recall": "의 Recall", "가현재RRF": "가 현재 RRF",
        "모듈의dense": "모듈의 dense", "질문단위": "질문 단위", "같은CUDA": "같은 CUDA",
        "같은pgvector": "같은 pgvector", "배치는GPU": "배치는 GPU", "입력길이1024": "입력 길이 1024",
        "시간은진단": "시간은 진단", "함수상위8": "함수 상위8", "원본gold": "원본 gold",
        "기존nDCG": "기존 nDCG", "순수BM25": "순수 BM25", "채널의별도": "채널의 별도",
        "는재현": "는 재현", "대비Recall": "대비 Recall",
    }.items():
        narrative = narrative.replace(before, after)
    DOC.write_text(narrative, encoding="utf-8")
    print(DOC)


if __name__ == "__main__":
    main()
