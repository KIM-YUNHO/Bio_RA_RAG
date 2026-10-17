# 4. 질문 계획·검색 — 현재 상태

- 작성: 2026-10-16 16:50 KST
- 수정: 2026-10-16 17:55 KST (`feature/stable-queries` 병합 기준: 계획기 칸 축소, 사실당 영어 검색어 1개. 병합 후 서비스 재시작이 필요하다)
- 수정: 2026-10-17 17:45 KST (8775·8885 재시작 완료. 두 서비스가 `query_strategy: ra-research-tasks-8-fact-queries`, 4.10.3을 보고한다)
- 기준: `rag/ra_retrieval.py`(`DEFAULT_PROFILE`), `rag/ra_tasks.py`, `rag/ra_models.py`, `rag/rerank.py`, `rag/ra_sources.py`, `rag/ra_research.py`, 서비스 요약(`query_strategy: ra-research-tasks-8-fact-queries`, `retrieval_strategy: ra-task-hybrid-context-7-topic-queries-slot-terms`)
- 상위: [현재 서빙 상태](README.md)

## 1. 흐름

```text
질문 + 화면 범위 + 직전 대화
 → ① 계획 (gpt-5.6-luna, LLM 호출 1)
 → ② 범위 확정: 제품 × 기관
 → ③ 그룹마다 1차 검색: dense 40 + BM25 40 → RRF → 문서 역할 가산
 → ④ 리랭커: bge-reranker-v2-m3, 질의 fquery_unit, 후보 풀 40, 하이브리드와 RRF → 상위 8
 → (공통 지침 역할이면) 기관별 지침 BM25
 → ⑤ 생성 전 제한된 추가 로컬 검색 (최대 1회, LLM 호출 없음)
 → 문맥 구성·생성 ([05](05_GENERATION_VERIFICATION.md))
```

## 2. 계획 (LLM 호출 1)

계획기는 질문의 의미를 읽고 닫힌 선택지로 출력한다. 코드는 고유명사와 질문 원문에 있는 단어만 확인한다.

| 출력 | 내용 | 제약 |
| :-- | :-- | :-- |
| 작업(task) | 질문 안의 요청 | 1~4개 |
| `request` | 작업을 한국어로 다시 쓴 요청문 | |
| `queries` | 작업의 검색어. 코드가 facet마다의 `query`로 채운다(사실당 1개, 중복 제거, 계산 facet 제외). facet이 없는 열린 작업만 계획기가 쓴 영어 검색어 1개 | 1~12개 |
| `source_roles` | 읽을 문서 역할(assessment·label·correspondence·guideline·quality·rmp·decision_summary·other_review·protocol·sap) | 1~3개 (여러 기관을 읽는 작업은 3개까지) |
| `jurisdictions` | 이 작업이 읽을 기관(EU·US·CA·sponsor) | 없으면 질문 범위 전체 |
| `answer_facets` | 요청 사실 목록. 종류(dose_route, dosing_schedule, table_statistics, calculation, … 목록 밖은 `fact`), `ask`(질문 용어의 한국어 한 줄), `query`(영어 사실 검색어 1개: 질문이 그 사실에 붙인 적응증·시험 번호·대상군·투여 경로·문서 섹션을 포함하고 제품·기관 이름과 두 번째 사실은 뺀다. 예: `Crohn's disease intravenous induction dose by body weight`), `items`(따로 답할 군·구간·시점) | 최대 12개 |
| `guideline_request_quote` | guideline 역할을 고를 때 질문 속 일반 지침 요청 문구를 그대로 복사. 제품 질문이면 빈 값 | 제품 질문에 guideline을 고르지 않게 하는 장치 |

- 2026-10-16 17:55부터 계획기는 `english_query`, 기관 언급 `quote`, 고정 주제 `topics`를 쓰지 않는다(계획기 `ra-research-tasks-8-fact-queries`).

- 계획기는 범위 안 **문서 목록**(제품·기관·문서 종류·제목·날짜·기본 검색 여부)을 함께 본다(`RA_PLANNER_INVENTORY=1`).
- 기관은 enum과 질문 속 단어(`name`)를 함께 내고, 그 단어가 질문에 있어야 인정한다. 기관 이름 목록 규칙은 없다.
- 후속 질문: 이전 계획의 범위를 후보로 주고 계획기가 판단한다. 새 기관은 사용자가 쓴 단어가 있어야 인정한다. 독립 질문으로 바꾼 뒤 같은 경로를 탄다(추가 호출 없음).
- 계획기 temperature 0·seed를 줘도 실행마다 표현이 달라진다. 검색어를 사실당 1개로 고정해 이 편차가 검색 결과를 흔드는 정도를 줄였다(같은 질문 계획 4개에서 사실 정답 flip 9.8% → 0%, [07 평가](07_EVALUATION.md) 2.4절). 계획기가 두 사실을 "and"로 묶어 쓰는 경우는 아직 남아 있다.

## 3. 범위와 검색 그룹

- 화면의 제품·기관·문서 선택이 바깥 경계, 질문에 적힌 제품·기관이 그 안에서 좁힌다. 질문에 제품명이 없으면 화면에서 고른 전체 제품이다.
- **검색 그룹 = 작업 × 제품 × 기관.** 그룹마다 따로 검색해 상위 8개를 남긴다. 예: 5제품 × EU 질문은 작업 1개당 5그룹.
- 그룹 안 후보: 기본 검색 문서 + `correspondence`(RA 기본 포함) + 작업이 고른 문서 역할의 비기본 문서. 화면의 보조 문서 옵션을 켜면 비기본 전체.

## 4. 1차 검색 (그룹마다)

| 순위 목록 | 내용 |
| :-- | :-- |
| 사실 검색어별 | dense(BGE-M3 내적) 상위 40 + BM25 상위 40을 RRF(k=60)로 합침 |
| 고정 주제 질의 | 새 계획은 `topics`가 비어 있어 생기지 않는다. `topics` 칸이 없는 옛 저장 계획을 다시 돌릴 때만 질문 단어로 주제를 골라 추가한다(`ra_topic_queries`) |
| 통계 표 헤더 | 질문이 표 통계를 물으면 `ra_statistical_tables`가 헤더가 맞는 표 후보를 첫 순위 목록으로 넣음 |
| 문서 역할 가산 | 각 순위 목록에 "계획기가 고른 역할의 문서만" 부분 목록을 가중치 1.0으로 다시 RRF (`role_boost`) |

- BM25 입력은 절 경로 + 캡션 + 본문이다(제품·기관·문서 제목 같은 모든 청크 공통 단어는 제외). 질의에서도 EMA·FDA·EPAR·label 같은 그룹 공통 단어를 뺀다(`strip_slot_terms`).
- BM25는 SQLite FTS5 `unicode61`, 영문·숫자 용어와 표·그림 번호를 OR 검색한다.

## 5. 리랭커 (기본 켜짐)

| 항목 | 값 |
| :-- | :-- |
| 모델 | 로컬 `BAAI/bge-reranker-v2-m3` (cross-encoder, GPU). 서비스 시작 `--warmup` 때 미리 올림 |
| 후보 풀 | 그룹마다 하이브리드 순서(다양성 유지)로 40개 |
| 질의 `fquery_unit` | 그룹의 제품명(ID·브랜드)·기관 + 계획기의 사실별 영어 검색어(+항목). 예: "SB17 Pyzchiva EMA: date of first authorisation". facet이 없는 열린 작업은 작업 영어 검색어 |
| 점수 | 한 문단은 여러 질의 중 가장 높은 점수 |
| 결합 | 리랭커 순위와 하이브리드 순위를 RRF로 합침(`rerank_fuse=True`). 하이브리드가 강하게 잡은 날짜·숫자 페이지를 리랭커 혼자 묻지 못하게 한다 |
| 최종 | 그룹당 상위 8개 |
| 적용 | 2026-10-16 11:38 KST부터 (`b7668bd`). 그 전 08:12~11:38은 `ask_unit`(한국어 ask) |

## 6. 공통 지침 검색

작업 역할에 `guideline`이 있으면 기관마다 지침 스냅샷을 BM25로 찾는다(검색어별 30개, 순위 결합). 질문에 비교 임상 효능시험이 명시되면 `comparative clinical efficacy studies`를 검색어에 한 번 더한다. dense·리랭커는 쓰지 않는다. 지침 상태 레지스트리로 제외·상태를 적용한다([03 색인](03_INDEX_CHUNK_EMBEDDING.md) 6절).

## 7. 생성 전 추가 로컬 검색

계획에서 인식한 필수 facet에 후보 근거가 없을 때만 한 번 더 찾는다. 최대 1라운드·2항목·새 패킷 6개·새 원문 1,200토큰, LLM 호출 0회. 기존 제품·기관·문서 범위를 넘지 않는다(`rag/ra_research.py`, 서비스 인자 `--research-rounds 1`). 답변 뒤 부족·상충을 판단해 반복 재검색하는 루프는 없다.

## 8. 현재 기본값과 바꾸는 방법

`rag/ra_retrieval.py`:

```python
DEFAULT_PROFILE = {'bm25_weight': 1.0, 'question_query': None, 'question_bm25': False, 'query_unit': False, 'strip_slot_terms': True,
                   'suppress_boilerplate': False, 'collapse_duplicates': False, 'role_boost': True,
                   'extra_default_groups': ('correspondence',), 'rerank': 'fquery_unit', 'rerank_pool': 40, 'rerank_fuse': True,
                   'rerank_question': False}
```

| 스위치 | 기본 | 뜻 |
| :-- | :-- | :-- |
| `--retrieval-pages` (서비스 인자) | 8 | 그룹당 근거 수. 라이브러리 함수 기본값 5와 다르니 서비스 경로는 8로 본다 |
| `RA_PLANNER_INVENTORY` | `1` | 계획기에 문서 목록 제공. `0`이면 끔 |
| `RA_CHECKLIST_PACKING` | `0` | 체크리스트 기준 패킹(측정상 효과 없음) |
| `rerank` | `fquery_unit` | `None`이면 리랭커 끔. `ask_unit`, `ask`, `request` 등 다른 질의 방식 |
| `rerank_pool` | 40 | 응답 속도가 우선이면 20 (recall@8 0.804 → 0.787 측정) |
| `suppress_boilerplate`, `collapse_duplicates` | 끔 | 측정상 이득 없어 꺼 둔 실험 옵션 |
| `question_query`, `rerank_question` | 끔 | 원 질문을 그룹마다 dense 순위 하나로, 리랭커 질의 하나로 함께 쓰기(anchor). 사실 정답 recall 0.931 → 0.902로 하락해 끔. 제품이 없는 질문에서는 flip을 줄였다 |
| `query_unit` | 끔 | 1차 검색 질의 앞에 그룹 제품명·기관. 그룹이 이미 제품 × 기관으로 걸러져 있어 끔 |
| `question_bm25` | 끔 | 원 질문을 BM25에도 넣어 시험 번호 같은 식별자를 글자 그대로 맞춤. 측정 안 함 |

환경 변수는 서비스 프로세스 시작 때 읽는다. 바꾸면 재시작해야 한다.

## 9. 원 기록

- [RA 질문 처리 변경 기록](../RA_QUERY_HANDLING_LOG.md) — 개념 정리(작업·facet·그룹·atomic need), 10-16 01:55~17:55 결정과 측정 (마지막 항목 17:55 "검색어 안정화"가 현재)
- [RAG 파이프라인 상세](../rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md) §7~8 (검색 구조. 리랭커 미연결·페이지 수 5는 당시 상태)
