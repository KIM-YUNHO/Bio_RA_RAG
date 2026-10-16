# 7. 평가와 진행 중 작업 — 현재 상태

- 작성: 2026-10-16 16:50 KST
- 기준: `docs/RA_QUERY_HANDLING_LOG.md`(main, 11:38까지), `feature/generation-units`·`feature/stable-queries` 브랜치의 같은 기록과 커밋 메시지, `data/runs/` 실행 기록
- 상위: [현재 서빙 상태](README.md)

## 1. 평가 세트

| 세트 | 위치 | 규모 | 무엇을 보나 | 주의 |
| :-- | :-- | :-- | :-- | :-- |
| 답변 29문항 | `data/runs/guard_2026-10-15/cases_e2e29.json` | 29문항 × 2회, 384점 만점 | 최종 답변 루브릭 점수 | 개발에 계속 써 온 문항이라 과적합 위험. 문항 목록은 [변경 기록 부록](../RA_QUERY_HANDLING_LOG.md#부록-평가-문항-목록) |
| 검색 골든셋 | `data/runs/retrieval_gold_2026-10-16/gold.json` | 100문항, 정답 묶음 265개(주 지표 235) | 계획 고정 후 그룹별 recall@k·nDCG·MRR | QA_candidates 23문항은 사람 검토 전. 타임라인 외 29문항 묶음은 실버 정답 |

채점: `eval/prompt_variants.py`(세부 사실·PK 표), `eval/answer_scoring.py`(열거형·감시 문항).

## 2. 현재 서빙 구성의 측정치

서빙 구성 = 계획기 facet(`fact` 포함)·문서 목록 + 그룹당 8 + 리랭커 `fquery_unit` + RRF + 풀 40 + correspondence 기본 포함 + 4.10.2.

### 2.1 검색 골든셋 (API 비용 없음, 풀 40, 주 지표 235묶음)

| 구성 | R@8 | nDCG@8 | MRR | 사실 정답 | QA_candidates | 세부 질문 | 감시 | 열거형 | 타임라인 |
| :-- | --: | --: | --: | --: | --: | --: | --: | --: | --: |
| 리랭커 없음 | 0.715 | 0.529 | 0.482 | 0.884 | 0.400 | 0.889 | 0.900 | 0.933 | 0.500 |
| `ask_unit` (08:12~11:38 서비스) | 0.804 | 0.651 | 0.609 | 0.870 | 0.675 | 0.867 | 1.000 | 1.000 | 0.679 |
| **`fquery_unit` (현재)** | **0.843** | **0.669** | **0.617** | 0.928 | 0.750 | 0.889 | 1.000 | 1.000 | 0.696 |

후보 풀 상한은 약 0.90이다. 리랭커 지연은 질문당 중앙값 약 0.8~1.8초, 사실 × 제품 × 기관이 많은 질문은 최대 수십 초.

### 2.2 답변 end-to-end (29문항 × 2회, gpt-5.6-luna)

| 구성 | 점수 /384 | claim 탈락 | 처리 시간 중앙값 / 최대 | 기록 |
| :-- | --: | --: | :-- | :-- |
| 리랭커 이전 기본값 | 278 | 86 | 17.5초 / 52초 | `data/runs/planner_facets_2026-10-15/e2e_final` |
| `ask_unit` + RRF | 311 | 85 | 18.1초 / 75초 | `…/e2e_atomic_fuse` |
| **`fquery_unit` + RRF (현재)** | **305** | 72 | 20.3초 / 99초 | `data/runs/generation_units_2026-10-16/base_fquery` |

- 현재 구성의 end-to-end(305)는 main 기록이 아니라 `feature/generation-units` 브랜치의 변경 기록(13:42 항목)에만 있다.
- 311과 305의 차이는 계획기 편차 범위 안이다. 같은 코드·질문에서도 Q1(허가 타임라인)이 10점과 25점으로 갈린다. 매 실행 계획을 새로 만드는 end-to-end로는 6점 차이의 원인을 가를 수 없다.

### 2.3 현재 구성의 손실 위치 (`eval/loss_attribution.py`, 305점 실행)

| 위치 | 항목 수 (384개 중) |
| :-- | --: |
| 성공 | 305 |
| 생성 (입력에 있는데 claim 없음) | 32 |
| 앞 단계 때문 (날짜가 빠져 계산 불가) | 17 |
| 순위·상위 k (풀에는 있으나 입력에 없음) | 14 |
| 검증 (claim이 있었으나 탈락) | 9 |
| 1차 검색 (풀에도 없음) | 6 |

손실은 허가 날짜·기간 타임라인에 몰려 있다. 생성 손실의 원인은 체크리스트가 제품마다 펼쳐지지 않는 것과 계산 facet 누락이다([05](05_GENERATION_VERIFICATION.md) 4절).

## 3. 수치를 인용할 때

| 수치 | 뜻 | 아닌 것 |
| :-- | :-- | :-- |
| 111문서·15,682청크 | 서비스 릴리스 04의 규모 | 전체 수집 코퍼스·전수 의미 검증 |
| 88/90 셀 | H 선정용 ABP654 15쪽 고정 셀 | 전체 OCR 정확도 |
| 8/17 → 11/17 | 01 대 02 색인의 개발 질문 완전 충족(고정 코드) | 현재 코드·04 정확도 |
| recall@8 0.843 | 고정 계획 검색 골든셋 | 최종 답변 정확도 |
| 305/384 | 29문항 × 2회 루브릭 점수 | 29문항 중 정답 수, 독립 정답률 |

2026-10-14 보호 해제 9문서는 이후 독립 평가 자료로 쓰지 않는다.

## 4. 평가 도구

| 도구 | 하는 일 | API 비용 |
| :-- | :-- | :-- |
| `eval/run_questions.py` | 29문항 end-to-end (`--retrieval-pages`, `--rerank`, `--rerank-pool`, `--planner-inventory`, `--extra-default`) | 있음 (실행당 약 $0.40) |
| `eval/retrieval_gold.py`, `eval/build_retrieval_gold.py` | 검색 골든셋 생성·평가, `--variants 모델:모드[:fuse]`, `--pool` | 없음 |
| `eval/loss_attribution.py` | end-to-end 보고서로 정답 항목별 손실 단계 분해 | 없음 |
| `eval/packing_recall.py` | 저장 계획으로 검색·패킹만 다시 해 LLM 입력 근거 recall | 없음 |
| `eval/paired_dev.py`, `eval/prompt_variants.py` | 계획·근거 고정 짝 비교 | 생성만 |

## 5. main에 반영되지 않은 작업 (서빙 안 됨)

| 브랜치 | main보다 | 내용 | 상태 |
| :-- | --: | :-- | :-- |
| `feature/generation-units` | 6커밋 | G1 계산 facet 체크리스트 줄, G2 체크리스트를 범위 제품·기관으로 펼침, G3 근거를 제품×기관 단위로 묶어 제시(`RA_GENERATION_UNITS`). 손실 분해 기록, `eval/plan_stability.py` | end-to-end 306점(생성 손실 32→21, 순위 손실 14→25는 계획기 편차). 판정 보류 |
| `feature/stable-queries` | 5커밋 (마지막 15:50) | 계획기 필드 축소, 사실당 영어 검색어 1개, 질문에 적힌 식별자 유지, 1차 검색어에 그룹 제품명, 지침 인용 복원 | 계획 4개 안정성 측정(사실 정답 62문항): recall 평균 0.877 → 0.941, 최저 0.824 → 0.941, 흔들리는 정답 9.8% → 0%. end-to-end 미실행 |
| `docs/ra-painpoints-luna-selection` | 2커밋 | 발표 문서의 RA pain point·Luna 선정 보강 | 문서 PR |

main 작업 폴더에는 이 밖에 미커밋 화면 수정(`rag_service/web/`, `workspaces.py`)과 발표 문서 수정이 있다.

## 6. 원 기록

- [RA 질문 처리 변경 기록](../RA_QUERY_HANDLING_LOG.md) (main: 11:38까지. 12:29 이후 항목은 `feature/generation-units` 브랜치)
- [H v2 개발 평가](../rag/H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md), [전처리 최종 선정](../preprocessing/FINAL_PREPROCESSING_DECISION_2026-10-14.md)
- [발표 이슈·근거 정리](../project/PRESENTATION_ISSUE_REVIEW_2026-10-16.md) §3~4 (문서 간 시점 차이, 수치 해석)
