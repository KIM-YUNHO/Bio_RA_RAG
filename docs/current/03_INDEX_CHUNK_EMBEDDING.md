# 3. 색인 (청킹·임베딩·저장) — 현재 상태

- 작성: 2026-10-16 16:50 KST
- 수정: 2026-10-18 01:30 KST (서비스 검색을 pgvector로 전환, 새 릴리스 게시 명령)
- 기준: `GET /api/summary`(8885), 릴리스 04 `release_manifest.json`·`candidate/`, DB `rag.releases`·`rag.active_release`, `rag/retrieval/search.py`·`rag/retrieval/service_scope.py`·`rag/ra/ra_sources.py`
- 상위: [현재 서빙 상태](README.md)

## 1. 서비스 릴리스

| 항목 | 값 |
| :-- | :-- |
| 버전 ID | `04_pipeline_h_supplement_2026-10-14` |
| 경로 | `data/versions/04_pipeline_h_supplement_2026-10-14/embedded/release` |
| `chunks.jsonl` sha256 | `2b5684a27f444cfc08a4fe50c1a5047cbf53ee9c9577dd69807b220f25a7d905` (서비스 요약값과 일치) |
| 규모 | 111문서, 15,682청크, 벡터 15,682 × 1024 |
| 출처 | EMA 34 · FDA 52 · Health Canada 15 · ClinicalTrials.gov 10 / PDF 101 · HTML 10 |
| 서빙 시작 | 2026-10-14 22:28 KST부터 (`versions.json` `served_by_ra_service`) |

## 2. 제품별 규모와 기본 검색 범위

| 제품 | 이름 | 문서 | 청크 | 기본 검색 청크 | 페이지 |
| :-- | :-- | --: | --: | --: | --: |
| SB17 | Pyzchiva | 33 | 3,870 | 3,070 | 1,442 |
| ABP654 | Wezenla / Wezlana | 25 | 3,499 | 2,091 | 1,197 |
| CTP43 | Steqeyma | 25 | 4,031 | 2,831 | 1,509 |
| BMAB1200 | Yesintek | 20 | 3,265 | 2,306 | 1,175 |
| BAT2206 | Usymro / Starjemza | 8 | 1,017 | 797 | 368 |
| 합계 | | 111 | 15,682 | 11,095 | 5,691 |

페이지에는 HTML 10문서의 DOM 미리보기 쪽이 섞여 있다(원본 HTML의 쪽 번호가 아님). 모든 제품의 상태는 `partial`(전처리·색인된 범위만 검색 가능, 수치·의미 검토는 별도)이다.

문서 역할(`doc_group`)별 검색 포함 여부:

| 역할 | 문서 | 청크 | 검색 |
| :-- | --: | --: | :-- |
| `assessment` (EPAR·FDA 심사·HC SBD) | 16 | 3,897 | 기본 |
| `label` (제품정보·라벨·모노그래프) | 25 | 5,996 | 기본 (5,986) |
| `decision_summary` (EMA 개요·절차, HC RDS) | 19 | 180 | 기본 |
| `quality` (FDA Product Quality Review) | 5 | 1,032 | 기본 |
| `correspondence` (FDA 승인서한 등) | 25 | 423 | 문서 메타데이터상 비기본이지만 **RA 검색은 기본 포함**(`DEFAULT_PROFILE['extra_default_groups']`, 2026-10-16부터) |
| `other_review`, `rmp` | 11 | 1,874 | 비기본 — 계획기가 해당 역할을 고르거나 화면의 보조 문서 옵션을 켤 때 |
| `protocol`, `sap` (CT.gov) | 10 | 2,280 | 비기본 — 계획기가 protocol/sap 역할을 고를 때만(의뢰자 계획 자료로 표시) |

규칙 위치: 기본 검색 판정 `rag/retrieval/service_scope.py`(`default_search`), 필터 `rag/retrieval/search.py`(`filter_idx`), RA 범위 확장 `rag/ra/ra_retrieval.py`.

## 3. 청킹

| 설정 | 값 |
| :-- | :-- |
| 청커 버전 | `preprocessed-chunks-13-closed-row-blocks`, 스키마 `rag-chunk-3` |
| 본문 | 목표 768 / 최대 1,024 BGE-M3 토큰. 절·시험 경계 우선 |
| overlap | 같은 절 안에서 온전한 fragment만, 0~96토큰 |
| 표·그림 | 최대 2,048토큰. 표는 열 헤더 반복, 닫힌 행 묶음·rowspan 경계 보존, 캡션·각주 포함 |
| 임베딩 입력 상한 | 8,192토큰. 넘으면 오류(자르지 않음) |
| 원문 범위 | `representation_start/end` = 선택한 전처리 표현 문자열 위치. PDF 위치는 `source_refs`·bbox |
| 제외 | 미완료 페이지·검색 제외 표현·패널 중복 등은 `chunk_exclusions.jsonl`에 사유 기록 |

04 청크의 27%(4,227개)는 절 정보가 비어 있다. 절 제목을 보강한 재청킹 후보는 05(미서빙)다([02 전처리](02_PREPROCESSING.md) 3절).

## 4. 임베딩

| 항목 | 값 |
| :-- | :-- |
| 모델 | 로컬 `BAAI/bge-m3` (SentenceTransformers, CUDA fp16), 외부 API 호출 없음 |
| 출력 | dense 1,024차원, L2 정규화, float32 저장 |
| 문서 입력 | `header + "\n" + text` (header = 제품·기관·문서 제목·절 경로·캡션·페이지) |
| 질의 | 최대 512토큰 |
| 순위 점수 | `vec @ query` 내적. fp16이라 노름이 0.9998~1.0005로 퍼져 있으므로 pgvector에서는 `<#>`(음의 내적)·`vector_ip_ops`를 쓴다. 코사인 `<=>`는 top-10이 6~8% 질의에서 달라진다 |
| 재사용 | 전체 임베딩 입력 문자열이 같을 때만 기존 벡터 재사용 |
| 동일성 | `meta.json`의 `revision`은 `null`, `embedding_identity`에 가중치·토크나이저 해시 |
| BGE-M3 sparse·multi-vector | 쓰지 않음. 키워드 검색은 별도 BM25 |

## 5. 저장 위치와 서비스가 읽는 경로

```text
release/
├─ release_manifest.json          구성요소(03 + supplement_20261014), 해시, product_readiness
└─ candidate/
   ├─ chunks.jsonl                i번째 줄 = chunk_ids.json i번째 = doc.npy i번째 행
   ├─ chunks_summary.json · chunk_contexts.jsonl · contexts.jsonl · chunk_exclusions.jsonl
   ├─ source_units.jsonl · visual_* (원문 패널·표/그림 문맥 복원용)
   └─ emb/bge-m3/{doc.npy, chunk_ids.json, meta.json}
```

| 저장소 | 현재 |
| :-- | :-- |
| PostgreSQL `rag` 스키마 | **서비스가 검색하는 곳**(10-18부터). 01·03·04 적재(04: 2026-10-14 21:23 KST). `rag.active_release`: `pilot`→01, `latest`→04, `served_by_ra_service`→04. 청크·벡터는 릴리스별 파티션, HNSW(내적) 인덱스, UPDATE 금지 트리거 |
| 검색 방식 | `BIOSIM_SEARCH_BACKEND=pgvector`: 시작 때 청크만 DB에서 읽고(BM25·필터용), 벡터는 DB에 둔다. 그룹별 dense 상위 40은 `storage/rag_index.py` `PgVectors.top`이 그 그룹의 행만 내적(`<#>`)으로 전수 계산하는 정확 검색이며 동점은 순번 순. HNSW는 필터 없는 근사 검색(`nearest`)용. 릴리스 04에서 450회 비교 시 집합 449/450 일치(다른 1건과 순서 차이는 모두 점수 차 0인 동점), 그룹 검색 중앙값 17.6ms·p90 36ms. 저장된 계획 검색(`eval/plan_stability.py --saved-only`)의 결과 JSON이 파일 방식과 같았다 |
| 파일 | 원문 패널용 원본 PDF·페이지·문맥(`release/`의 contexts·visual 등)은 여전히 파일. `chunks.jsonl`·`doc.npy`는 DB에 올리는 원천이자, DB에 없는 릴리스를 읽을 때 쓴다 |
| 새 릴리스 게시 | `python -m storage.publish_release data/versions/<id>/embedded/release [--serve]`: 파일 일관성 검사 → `versions.json` 항목 추가 → DB 적재(한 트랜잭션) → 파일·numpy 대 pgvector 검증 → `--serve`면 `latest`·`served_by_ra_service` 역할 이동. `pipelines/pipeline_h/merge_release.py`·`rechunk_release.py`가 릴리스를 만든 직후 자동으로 호출한다(`--no-publish`로 끔). 서비스는 재시작 때 새 역할의 릴리스를 읽는다 |
| BM25 | DB로 옮기지 않음. 서비스 프로세스 안에서 SQLite FTS5로 만든다 |

매니페스트·출처 필드에 만든 PC의 절대 경로가 남아 있다. `rag/index/portable_paths.py`가 읽을 때 현재 프로젝트 경로로 바꾼다. 원본 PDF(`data/raw/`)와 전처리 산출물은 원문 패널 때문에 파일로 있어야 한다.

## 6. 공통 지침 색인 (제품 색인과 별도)

| 항목 | 값 |
| :-- | :-- |
| 스냅샷 | `data/guidelines/ra_guidelines_native_2026-10-12_v2` (21문서, 236청크) |
| 청킹 | native 텍스트 페이지 1개 = 청크 1개. 문자층 없는 페이지는 제외 사유 기록 |
| 검색 | BM25만. 임베딩·리랭커 없음 |
| 상태 레지스트리 | `docs/rag/GUIDELINE_STATUS_2026-10-12.json` — **서비스가 실행 중에 읽는 파일이라 옮기거나 이름을 바꾸지 않는다** |
| 제외 | `GL:FDA-SCIENTIFIC-CONSIDERATIONS-2015`(27청크). 검색 대상 20문서·209청크 |
| 현재 유효성 | `current_guideline_validity_verified: false` — 저장 지침이 지금도 유효한지는 확인하지 않았다 |

## 7. 원 기록

- [RAG 파이프라인 상세](../rag/RAG_PIPELINE_CHUNK_EMBEDDING_GUIDE_2026-10-14.md) §4~6·§8 (청킹·임베딩·지침 설계. 운영 수치는 10-14 오전 기준)
- [DB 인수 안내](../../storage/HANDOFF.md) (맨 위 23시 갱신 우선), [pgvector 통합 지도](../project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md)
- [data/README.md](../../data/README.md) §3 (벡터 DB 적재 시 읽는 파일), [버전 레지스트리](../../data/versions/versions.json)
- [공통 지침 상태](../rag/GUIDELINE_STATUS_2026-10-12.md)
