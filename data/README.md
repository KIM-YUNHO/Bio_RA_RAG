# data — 원본·전처리·임베딩 데이터

- 작성: 2026-10-14 13:46 KST
- 최종 수정: 2026-10-14 14:59 KST (수집 코드 경로를 `collection/`으로 표기. 14:45 03 H v2 표 회수 후보 추가, `versions.json`의 latest를 03으로 변경)

원본 프로젝트(`C:\Users\Admin\Documents\biosimilar_collection`)에서 **바이트 그대로 복사**한 데이터다. 복사 후 sha256과 폴더별 파일 수·크기를 원본과 대조했다([이관 기록](../docs/project/SHARED_BASELINE_MIGRATION_2026-10-14.md)).

## 1. 구성

```text
data/
├─ raw/  repaired/  parsed/  catalog/      수집 원본·글꼴 복구 PDF·페이지 텍스트·카탈로그 (collection/collect.py 규약 경로, 그대로 유지)
├─ registry/  reviewed/                     적재 에이전트 허가목록 스냅샷 · 시험 레지스트리(studies.json)
├─ collection.sqlite  summary.json          수집 DB(collection/build_indexes.py) · 수집 요약
├─ runs/                                    수집 실행 로그, model_quota(API 사용 장부), 승인 범위 corpus_plan.json
├─ guidelines/                              공통 지침 BM25 스냅샷 2개 (제품 데이터와 별도, RA 서비스가 사용)
└─ versions/                                ★ 전처리·임베딩 결과 (버전 × 단계)
   ├─ versions.json                         역할 포인터(pilot / latest / served) · 계보 · 옮기지 않은 버전 목록
   ├─ 01_pilot_baseline_2026-10-10/         파일럿 기준 = 현재 RA 서비스가 쓰는 데이터
   │  ├─ preprocessed/                      전처리 완료: 구성요소 추출 번들 89개 (원래 data/preprocessed/ 아래 상대 경로 유지)
   │  ├─ embedded/rag_finalization_2026-10-10_v2/   임베딩 완료 릴리스 (release_manifest.json + candidate/)
   │  └─ records/                           실행·검증·평가 기록, 당시 service_release.json
   ├─ 02_pipeline_h_2026-10-14/             pipeline_h v2 전체 실행 결과 (서비스 미반영)
   │  ├─ preprocessed/                      전처리 완료: extraction/, html_dom_preview/, dom_page_images/
   │  ├─ embedded/candidate/                임베딩 완료(구성요소): chunks + 벡터 + chunk_structure_links, embedding_reuse
   │  ├─ embedded/release/                  임베딩 완료(릴리스): 서비스·검토 도구가 읽는 형식
   │  └─ records/                           FINAL_REPORT.md, 감사·검증 JSON, logs/
   └─ 03_pipeline_h_v2_table_recovery_2026-10-14/   최신 = 02 + 연속 표 2개 단위 회수 (별도 검토 후보, 서비스 미반영)
      ├─ preprocessed/extraction/           전처리 완료: 02에서 2개 단위만 바뀐 추출 번들
      ├─ embedded/candidate_final/          임베딩 완료(구성요소)
      ├─ embedded/release_final/            임베딩 완료(릴리스) — 코드 4.9.1~4.9.3 제한 평가에 쓴 색인
      └─ records/                           후보 구성·무결성 감사, 연속 표 96개 회수 검사 요약
```

03의 평가 실행 기록(질문·보고서·원문 판정·테스트 로그)은 문서 링크가 그대로 열리도록 원래 경로인 `runs/h_v2_rag_development_2026-10-14_luna_v1/`에 두었다. 설명은 [H v2 개발 평가·개선](../docs/rag/H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md).

## 2. 세 버전 비교

| 항목 | 01 파일럿 기준 | 02 pipeline_h v2 | 03 v2 표 회수 후보 (최신) |
| :-- | :-- | :-- | :-- |
| 원래 이름 | `rag_finalization_2026-10-10_v2` | `pipeline_h/runs/ustekinumab_20261014_v2` | `data/runs/h_v2_rag_development_2026-10-14_luna_v1/native_candidate_v2/release_final` |
| 상태 | RA 서비스(8775/8875) 운영 색인 | 생성·검증 완료, 서비스 전환 안 함 | 별도 검토 후보, 서비스 전환 안 함 |
| 만든 코드 | [pipelines/backup_pilot](../pipelines/backup_pilot/README.md) | [pipelines/pipeline_h](../pipelines/pipeline_h/README.md) | 02 + `eval/h_v2_native_candidate.py`, `eval/finalize_h_v2_native_candidate.py` |
| 범위 | 5제품(SB17·ABP654·CTP43·BMAB1200·BAT2206) · EMA/FDA/HC · 92문서 | 동일 92문서 | 동일 92문서 |
| 페이지 | 4,160 | PDF 4,102 + HTML DOM 미리보기 99 | 02와 동일 |
| source unit | 46,292 | 68,092 | 68,092 (원문 단위 70,264개 중 2개 변경) |
| 청크·벡터 | 10,088 × 1024 | 11,536 × 1024 (v1 벡터 11,277 재사용, 259 신규) | 11,536 × 1024 (11,534 재사용, 2 신규: 캡션 청크 → 표 본문 청크) |
| `chunks.jsonl` sha256 | `bc2ef2ce…a626c` | `d89adca0…a5e3c` | `b34e3bfc…` (`versions.json`에 전체 값) |
| 개발 평가 (GPT-5.6 Luna, 같은 17문항) | 완전 충족 8/17 | 완전 충족 11/17 | 제한 재평가만 수행(전체 17문항 재생성 안 함) |
| 임베딩 | BGE-M3 dense, L2 정규화, cosine, float32 | 동일 | 동일 |

03은 02의 연속 표 후보 96개를 기존 native 표 검증기로 다시 검사해, 원본 테두리·native 문자·헤더가 확인되는 2개 단위에서만 닫힌 행을 회수한 결과다. OCR·VLM 호출은 없고 94개는 계속 보류한다.

## 3. 벡터 DB 적재 시 읽는 파일

릴리스 하나의 `candidate/` 안에서 다음 세 파일이 한 묶음이다.

- `chunks.jsonl`의 i번째 줄 = `emb/bge-m3/chunk_ids.json`의 i번째 ID = `emb/bge-m3/doc.npy`의 i번째 행(1024차원 float32, 정규화됨).
- 임베딩 입력은 각 청크의 `header + "\n" + text`였다. 같은 입력이면 벡터를 재사용해도 된다.
- 원문 추적 키는 `document_id`, `version_id`, `pdf_page`(+`pdf_page_label`)다. 카탈로그(`data/catalog/documents.jsonl`)와 이 키로 연결한다.
- `meta.json`에 모델·차원·유사도·입력 잘림 수(`truncated_inputs: 0`)가 있다.

## 4. 주의: 데이터 안의 절대 경로

해시 계약을 지키려고 데이터 내용을 고치지 않았다. 그래서 다음 필드에는 **원본 프로젝트의 절대 경로**(`C:\Users\Admin\Documents\biosimilar_collection\...`)가 그대로 남아 있다.

- `release_manifest.json`: `candidate`, `components[].run`, `components[].candidate`
- 청크: `provenance.parsed_path`, `provenance.preprocessing_run`, `provenance.source_refs[].path`, `study_candidates.anchors[].source_refs[].path`, (파일럿) `structure.figure_labels.fields[].evidence.source_refs[].path`
- 전처리: `source_units`·`extracted_assets`·`visual_*`의 `source_refs[].path`, `extraction_manifest.json`의 원본·파생본 경로

영향:
- **이 PC**에서는 원본 프로젝트가 남아 있으므로 RA 서비스·검토 화면(`rag/release.py`의 `ReleaseBundle`)이 그 경로를 그대로 읽어 동작한다(내용은 사본과 동일함을 해시로 확인).
- **다른 PC**에서는 `ReleaseBundle` 로드와 원문 페이지 렌더링이 실패한다. 청크 텍스트·메타데이터·벡터 자체는 경로와 무관하므로 **벡터 DB 적재에는 지장이 없다**.
- PostgreSQL + pgvector 통합 때 경로를 프로젝트 상대 경로로 바꿔 저장하는 것이 첫 작업이다([통합 지도](../docs/project/PGVECTOR_INTEGRATION_MAP_2026-10-14.md) 3절).

## 5. 옮기지 않은 데이터

계보에 있는 중간 릴리스, pipeline_h v1(2.8GB), 스테이징 수집본(`product_search/data`, 832MB), 과거 OCR·evidence·후보 색인 등은 복사하지 않았다. 목록과 이유는 [`versions/versions.json`](versions/versions.json)의 `identified_not_copied`와 [이관 기록](../docs/project/SHARED_BASELINE_MIGRATION_2026-10-14.md)에 있다. 모두 원본 프로젝트에 그대로 남아 있다.
