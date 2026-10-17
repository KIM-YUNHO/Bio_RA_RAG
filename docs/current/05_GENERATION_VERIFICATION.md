# 5. 문맥 구성·생성·검증 — 현재 상태

- 작성: 2026-10-16 16:50 KST
- 수정: 2026-10-17 17:45 KST (재시작으로 4.10.3·`feature/hide-model-gaps`(PR #9) 반영: 답변 본문에는 검증된 주장만)
- 기준: `rag/ra_pipeline.py`(`VERSION = 'ra-research-rag-4.10.3'`), `rag/ra_retrieval.py`(패킹), `rag/source_study_scope.py`, `rag/ra_prompt.py`, `rag/ra_answer.py`, `rag/ra_review.py`, `rag/openai_chat.py`, 서비스 요약(`answer_strategy: ra-grounded-synthesis-4.8.10`, `citation_mode: spans`)
- 상위: [현재 서빙 상태](README.md)

## 1. 흐름

```text
그룹별 상위 8개 근거
 → 근거 블록 E1, E2 … (제목·연결 표·같은 시험 문맥 복원, 시험 충돌 차단)
 → 인용 구간 S1, S2 … (문장·셀 단위 ID)
 → 생성 (LLM 호출 2): claim + citation_span_ids, gap
 → 코드 검사 (숫자·단위·제품·endpoint·조건)
 → 내부 검토 (LLM 호출 3): claim별 지지 여부
 → 완료 판정 (answered / partial / not_found) + 검토 메모·미팅 질문 초안
```

LLM은 세 단계 모두 `gpt-5.6-luna`(reasoning `none`)이고 질문당 최대 3회다. 근거가 없거나 유효한 claim이 남지 않으면 일부 호출을 건너뛴다. 자동 무제한 재시도는 없다.

## 2. 근거 패킹

| 단계 | 현재 동작 |
| :-- | :-- |
| 순서 | 모든 그룹의 1위, 그다음 2위 … 순으로 돌아가며 채움. 청크 ID 중복 제거 |
| 제목 복원 | 메타데이터 문구가 아니라 같은 문서·버전의 source unit에서 가장 가까운 시험 제목, 라벨은 번호 붙은 적응증 제목까지 원문으로 공급 |
| 연결 표·그림 | 본문이 명시적으로 참조한 표·그림을 붙임 |
| 같은 시험 문맥 | 시험 정체성이 확인될 때 같은 시험의 앞뒤 본문을 추가(그룹별 원문 1,200토큰 한도) |
| **시험 충돌 차단 (4.10.2)** | 참조 문장·캡션·표 본문에 인쇄된 시험 ID가 상속 제목과 충돌하면 그 표를 공급하지 않음. Part 1/Part 2 구분. 충돌하는 상속 제목은 요청 시점 복사본에서 제거하고 `study_context_conflict`로 기록 |
| **한 번 더 연결 (4.10.2)** | 같은 시험 문맥으로 새로 넣은 본문의 표 참조를 한 차례만 더 따라감(재귀 없음) |
| 예산 초과 | 패킷을 넣을 때마다 전체 비용을 다시 계산하고, 넘으면 그 패킷을 빼고 사유를 기록. 원문 중간을 자르지 않음 |

저장된 청크·헤더·벡터는 바꾸지 않는다. 잘못 상속된 헤더는 색인에 남아 있어 dense·BM25 순위 편향은 그대로다.

## 3. 입력 예산

| 항목 | 값 |
| :-- | :-- |
| 호출당 읽기 예산 | **65,536토큰** (서비스 인자 `--context-tokens` 기본값, 4.10.1부터). 프로젝트 한도이며 모델 한도가 아니다 |
| 출력 예약 | 3,000토큰 |
| 여유분 | 256토큰 → 입력 한도 62,280토큰 |
| 계산 | 선택 모델 tiktoken + 메시지 framing allowance. 제공자 집계와 완전히 같지는 않음 |
| 질문당 블록 수 | 대략 그룹 수 × 8 + 붙은 조각. 29문항 측정 중앙값 약 15개, 5제품 × 3기관 타임라인 질문(Q1)은 약 130개. 지금 설정에서는 대부분 예산이 차지 않아 검색된 후보가 모두 들어간다 |

이전 문서의 "envelope 16,384"는 `OpenAIPipelineChat` 함수 기본값이고, 서비스는 65,536으로 띄운다.

## 4. 생성 프롬프트

| 설정 | 현재 |
| :-- | :-- |
| 프롬프트 | 모듈식(`RA_PROMPT_VARIANT=modular`, 기본). CORE + 질문·근거에 맞춘 모듈 + 가장 특화된 지시를 끝에 |
| 모듈 | PROTOCOL(CT.gov 근거가 있을 때 "계획"으로 기술), GUIDELINE, STUDY, COMPARISON, TABLE, 작업 의도별 GUIDANCE, DERIVED(계산 facet), ENUMERATION(제품×기관 > 8), CHECKLIST, REQUESTS |
| 요청 방식 | 체크리스트(`RA_REQUIREMENT_MODE=checklist`, 기본). claim 수는 상한 안에서 자유, 목록 밖 내용도 원 질문을 보고 답함 |
| 체크리스트 줄 | facet × **질문에 적힌** 제품 × 항목. 기관은 줄에 없음 |
| claim 상한 | 생성 응답 스키마 `MAX_PROPOSED_CLAIMS` = 16 |
| 인용 | `spans` — LLM은 인용 구간 ID만 고르고 코드가 원문 문자열·문서 버전·페이지·위치로 복원. 떨어진 구간을 합쳐 없는 인용문을 만들지 않음 |

알려진 생성 쪽 빈틈(손실 분해로 확인, main 미수정): 제품명 없이 "제품별로" 묻는 질문은 체크리스트가 제품마다 펼쳐지지 않는다. 계산 facet이 체크리스트에서 빠져 기간 계산이 누락되기 쉽다. 수정안은 `feature/generation-units`에 있다([07 평가](07_EVALUATION.md) 5절).

되돌리기: `RA_PROMPT_VARIANT=current`(이전 단일 프롬프트), `RA_REQUIREMENT_MODE=slots`(facet × 제품 × 항목마다 claim 1칸).

## 5. 검사

| 검사 | 내용 |
| :-- | :-- |
| 인용 존재 | 인용 ID·문자열이 실제 공급 원문에 있는가 |
| 경계 | 문서·버전·제품·시험·Part 경계 |
| 숫자·단위 | 답변의 숫자·날짜·단위가 인용 구간에 있는가(`ra_quantities`). 4.10.2부터 `kg/m 2`·`kg/m²`를 `kg/m2`로 정규화 |
| 표 | 숫자 셀과 헤더·군·단위·통계량 관계(`ra_table_citations`) |
| 조건 | 용량·경로·시점·간격·비교약이 자체 인용에 있는가, 실제 대 제안·신청 내용 구분 |
| 귀속 | 기관 판단·신청자 설명·일반 사실, 지침의 필수·선택·사례별 조건 |
| 내부 검토 (LLM 3) | 선택 인용을 보고 claim별 지지 여부 판단, 근거 부족·충돌 claim 보류 |

- 칸별 키워드 검사(예: design 칸에 "설계" 단어 필수)는 없다. 제품·숫자·endpoint 대조만 한다.
- claim 하나에 같은 작업 ID가 두 번 오면 하나로 합친다(답변 전체 거부하지 않음).
- 질문 조건 재확인(`BIOSIM_CONDITION_CHECK=on`, `rag/ra_condition_check.py`)은 **꺼져 있다**.
- 승인일 질문을 코드가 읽은 날짜 필드로 답하는 `--approval-timeline`도 꺼져 있다.

## 6. 완료 판정과 산출물

- 완료 판정은 계획기 facet 기준이다. 값으로 대조 가능한 facet(`dose_route`, `dosing_schedule`, `table_statistics`)이 모두 확인돼야 `answered`. 질문에 적힌 숫자·경로는 필수 조건이다.
- 작업 상태 `completed`와 답변 충족 `answered/partial/not_found`는 다르다.
- 답변 본문(`answer_ko`)에는 검사를 통과한 claim만 나온다. 생성 모델이 쓴 gap 문장은 인용 검사를 거치지 않으므로 본문에 붙이지 않고 `validation.model_gap_reasons`(`shown_to_user: false`)에만 남긴다. "인용으로 완전히 확인하지 못한 요청" 목록도 본문에서 빠졌고, `result['gaps']`에 남아 완료 판정과 검토 메모에 쓰인다(PR #9).
- 서비스 요약의 `numeric_accuracy_certified`, `semantic_accuracy_certified`, `current_guideline_validity_verified`는 모두 `false`다. 인용 일치·수치 검사·내부 검토는 전문가 인증이 아니다.
- 보고서(질문·계획·검색 후보·공급 근거·제외 청크·예산·claim·gap·단계 시간·사용량)는 `data/runs/ra_service_reports/`에 저장된다. 검토 메모·미팅 질문 초안은 보고서에서 만들며 추가 LLM 호출이 없다.

## 7. 원 기록

- [RA 생성 프롬프트 모듈](../rag/RA_PROMPT_MODULES_2026-10-15.md)
- [문맥 연결 감사](../rag/CONTEXT_LINK_AUDIT_2026-10-16.md), [문맥 연결 수정 4.10.2](../rag/CONTEXT_LINK_FIX_2026-10-16.md) — 문서에는 "서버 미재시작"으로 적혀 있지만, 이후 재시작한 서비스(현재 10-17 17:41·17:43 시작, 4.10.3)에 적용돼 있다
- [RAG 파이프라인 상세](../rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md) §9~12
- [RA 질문 처리 변경 기록](../RA_QUERY_HANDLING_LOG.md) 01:55 항목(체크리스트·완료 판정 변경)
