# 문서 안내

- 작성: 2026-10-14 14:10 KST
- 최종 수정: 2026-10-20 02:00 KST (project에 발표 도입부 배경 문서 추가. 이전: 10-16 16:50 `current/` 현재 서빙 상태 파트별 문서 추가)

처음 읽는 순서: [현재 서빙 상태](current/README.md) → 루트 [README](../README.md) → [운영 원칙](PIPELINE_PRINCIPLES.md) → 관심 영역 문서. 아래 날짜가 붙은 문서는 당시 기록이므로, 지금 서비스가 어떤 데이터·설정으로 도는지는 `current/`를 먼저 본다.

## current — 현재 서빙 상태 (서빙 구성이 바뀌면 여기부터 고친다)

| 문서 | 내용 |
| :-- | :-- |
| [current/README.md](current/README.md) | 한눈에 보기(포트·코드 버전·릴리스·모델·리랭커), 기존 문서 중 현재와 다른 곳, 확인 명령 |
| [01_COLLECTION_INGESTION.md](current/01_COLLECTION_INGESTION.md) | 수집·적재: 카탈로그 범위, CLI 수집, 적재 요청 에이전트·수집 창 |
| [02_PREPROCESSING.md](current/02_PREPROCESSING.md) | 전처리: 표준 H 구성, 서비스 색인 04의 계보, 미서빙 후보 05 |
| [03_INDEX_CHUNK_EMBEDDING.md](current/03_INDEX_CHUNK_EMBEDDING.md) | 색인: 릴리스 04 구성·기본 검색 범위, 청킹·임베딩, 파일·DB 저장, 공통 지침 |
| [04_RETRIEVAL.md](current/04_RETRIEVAL.md) | 질문 계획·검색: 계획기, 검색 그룹, 하이브리드, 리랭커, 기본값·스위치 |
| [05_GENERATION_VERIFICATION.md](current/05_GENERATION_VERIFICATION.md) | 문맥·생성·검증: 패킹, 입력 예산, 프롬프트·체크리스트, 검사·완료 판정 |
| [06_SERVICE_UI.md](current/06_SERVICE_UI.md) | 서비스·화면: 프로세스, 3단 화면, 파이프라인 메모, API, 기록 위치 |
| [07_EVALUATION.md](current/07_EVALUATION.md) | 평가: 현재 구성의 측정치, 평가 도구, main 미반영 브랜치 |

## 전체·원칙

| 문서 | 내용 |
| :-- | :-- |
| [PIPELINE_PRINCIPLES.md](PIPELINE_PRINCIPLES.md) | 전처리·RAG 운영 원칙(원본 보존, native 우선, 상태 분리, 평가 보호 등) |
| [DATA_PREPROCESSING_PIPELINE.md](DATA_PREPROCESSING_PIPELINE.md) | **파일럿 파이프라인** 전 단계·라이브러리·코드·산출물 (현재 서비스 데이터가 만들어진 과정) |

## collection — 수집·적재

| 문서 | 내용 |
| :-- | :-- |
| [INGESTION_PIPELINE.md](collection/INGESTION_PIPELINE.md) | 수집 적재 파이프라인 총정리(staging → promote → targets.json, 검증) |
| [INGESTION_AGENT_DB_PLAN_2026-10-14.md](collection/INGESTION_AGENT_DB_PLAN_2026-10-14.md) | **적재 요청 에이전트**·PostgreSQL/pgvector 목표 스키마·P0~P6 단계와 진행 기록 |

## preprocessing — 전처리

| 문서 | 내용 |
| :-- | :-- |
| [FINAL_PREPROCESSING_DECISION_2026-10-14.md](preprocessing/FINAL_PREPROCESSING_DECISION_2026-10-14.md) | **H를 표준 전처리로 확정**한 근거·구성·한계 (+ `.json`, `.verification.json`) |
| [pipeline_h/FINAL_REPORT_ustekinumab_20261014_v2.md](preprocessing/pipeline_h/FINAL_REPORT_ustekinumab_20261014_v2.md) | H로 92문서를 처리한 v2 완료 보고(최신 데이터) |
| [pipeline_h/REPAIR_DESIGN.md](preprocessing/pipeline_h/REPAIR_DESIGN.md), [pipeline_h/WORK_LOG.md](preprocessing/pipeline_h/WORK_LOG.md) | H 보완 설계와 작업 기록 (원래 `pipeline_h/` 폴더에 있던 문서) |
| [PIPELINE_COMPLETION_2026-10-10.md](preprocessing/PIPELINE_COMPLETION_2026-10-10.md) | 파일럿 전처리 확정과 임베딩 완료 기록 |
| [TEXT_LAYER_REPAIR.md](preprocessing/TEXT_LAYER_REPAIR.md) | PDF 글꼴 문자층 점검·복구(`collection/text_layer.py`) |

## rag — 검색·답변·서비스

| 문서 | 내용 |
| :-- | :-- |
| [RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md](rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md) | **현재 RAG 상세**: 청킹·임베딩·hybrid 검색·문맥·답변 검증·저장 (2026-10-14 오전 관측 기준) |
| [H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md](rag/H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md) | **H v2 색인 개발 평가와 개선**: GPT-5.6 Luna 고정 비교에서 완전 충족 기존 8/17 → H v2 11/17. 연속 표 2개 회수 후보(`data/versions/03_…`), 지침 전용 질문의 읽기 범위 수정(4.9.1), 통계량-표 헤더 검색(4.9.2), 공통 단위 숫자 쌍 검증 수정(4.9.3)과 제한 평가 |
| [UI_3PANE_AND_RA_FEATURES_PLAN_2026-10-13.md](rag/UI_3PANE_AND_RA_FEATURES_PLAN_2026-10-13.md) | 3분할 화면·멀티턴·RA 기능 계획과 사용자 결정 |
| [FINALIZATION_API_EVAL_2026-10-10.md](rag/FINALIZATION_API_EVAL_2026-10-10.md) | 파일럿 릴리스(10,088벡터) 확정·평가 기록 |
| [GUIDELINE_STATUS_2026-10-12.md](rag/GUIDELINE_STATUS_2026-10-12.md) | 공통 지침 공식 상태. 같은 이름의 `.json`은 **실행 중 `rag/ra_sources.py`가 읽는 파일**이므로 옮기거나 이름을 바꾸지 않는다 |

## project · research

| 문서 | 내용 |
| :-- | :-- |
| [RA_WORKSPACE_IMPLEMENTATION_PLAN_2026-10-15.md](project/RA_WORKSPACE_IMPLEMENTATION_PLAN_2026-10-15.md) | 현재 코드·서비스를 확인한 RA 파이프라인·단계 메모·협력사 이슈·적재 에이전트 운영창의 구현 과제, 재사용 범위, 의존관계와 완료 기준 |
| [SHARED_BASELINE_MIGRATION_2026-10-14.md](project/SHARED_BASELINE_MIGRATION_2026-10-14.md) | project_shared 이관 기록: 원본→대상 매핑, 경로 수정, 제외 목록, 검증, 제약 |
| [PGVECTOR_INTEGRATION_MAP_2026-10-14.md](project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md) | **PostgreSQL + pgvector 통합 때 고칠 파일·모듈 위치** |
| [LIMITATIONS_AND_NEXT_STEPS_2026-10-12.md](project/LIMITATIONS_AND_NEXT_STEPS_2026-10-12.md) | 한계점과 보완 순서 |
| [BIOSIMILAR_WORKFLOW_AND_POC_REVIEW_2026-10-04.md](project/BIOSIMILAR_WORKFLOW_AND_POC_REVIEW_2026-10-04.md) | 바이오시밀러 RA 실무와 PoC 방향(프로젝트 목적) |
| [PRESENTATION_BACKGROUND_BIOSIMILAR_2026-10-20.md](project/PRESENTATION_BACKGROUND_BIOSIMILAR_2026-10-20.md) | 발표 도입부 배경: RA 업무·문서의 어려움, RAG가 필요한 이유, 우스테키누맙·5제품, 성분·제품 수, 특허 만료 전망(수치·출처) |
| [RELATED_RESEARCH_REVIEW_2026-10-06.md](research/RELATED_RESEARCH_REVIEW_2026-10-06.md) | 참고 선행연구 |

## 옮기지 않은 문서로 가는 링크

위 문서들에는 날짜별 진행·평가 기록 등 **이관하지 않은 문서**로 가는 링크가 남아 있다(약 110개). `docs/` 하위 구조가 원본과 같으므로, 같은 상대 경로를 원본 프로젝트(`C:\Users\Admin\Documents\biosimilar_collection\docs\...`)에서 열면 된다. 제외 기준과 목록은 [이관 기록](project/SHARED_BASELINE_MIGRATION_2026-10-14.md) 6절에 있다.

## 문서 작성 규칙

- 새 문서는 `docs/` 아래 성격에 맞는 폴더에 둔다. 루트에는 `README.md`만 둔다. 코드 폴더의 `README.md`는 그 폴더 사용법만 짧게 쓴다.
- 문서 첫머리에 `작성`·`최종 수정`을 `YYYY-MM-DD HH:MM KST`로 쓰고, 수정하면 괄호에 바뀐 내용을 적는다.
- 경로는 프로젝트 루트 기준으로 쓴다(예: `rag/search.py`, `data/versions/...`).
