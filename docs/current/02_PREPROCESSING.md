# 2. 전처리 — 현재 상태

- 작성: 2026-10-16 16:50 KST
- 기준: `data/versions/versions.json`, `pipelines/pipeline_h/`, `pipelines/pipeline_h/runs/supplement_20261014/final_summary.json`, 릴리스 04·05 `chunks.jsonl`
- 상위: [현재 서빙 상태](README.md)

## 1. 표준 구성: pipeline H

표준(Main) 전처리는 `pipelines/pipeline_h/`다(2026-10-14 확정). 파일럿 구성(`pipelines/backup_pilot/`, 릴리스 01을 만든 코드)은 동결 백업이다.

| 기능 | 모델·도구 |
| :-- | :-- |
| PDF 문자층 확인·라우팅 | PyMuPDF / pypdfium2, 페이지 라우팅 규칙 |
| 기본 레이아웃 | Docling Heron |
| 일반 OCR | 정상 문자층 우선, 필요 시 RapidOCR + PP-OCRv6 det/rec small |
| 기본 표 구조 | TableFormer accurate |
| 후처리 | `ra_prep`(프로젝트 코드: 재판독, 문자·표 보정, 머리말·절·시험 ID) — `pipelines/pipeline_h/vendor/compare_study_standalone/` |
| 누락·유형 충돌 점검 | PP-DocLayoutV3 + 닫힌 테두리 규칙(OpenCV) |
| 선별 전사 | PaddleOCR-VL-1.6-0.9B — 누락·불완전 표와 의심 기호 crop만 |
| 후보 채택 | 구조·숫자·라벨 90% 보존 검사를 통과한 후보만 추가·교체, 실패 시 원래 표 유지 |
| 그림 | 원본·캡션·인쇄 문자·본문 참조만 보존. 의미 설명·곡선 수치 생성 없음 |
| HTML(HC 결정문서 등) | `source_router`가 원본 DOM 경로로 보냄. 제목·목록·표·병합 셀·DOM node path 보존, Story PDF는 미리보기 좌표용 |

Qwen 표 재생성, 그림 의미 설명은 기본 경로에 없다. 모델 작업은 GPU를 공유하므로 Docling → PP 검출 → 선별 VLM → BGE 순으로 실행한다.

## 2. 서비스 색인 04의 전처리 계보

```text
02  H v2 전체 실행      92문서 (PDF 4,102쪽 + HTML DOM 미리보기 99쪽)  11,536청크   VLM 949회(채택 898)
 └ 03  연속 표 2개 회수  02 + 닫힌 행 2단위 회수 (OCR·VLM 호출 없음, 94개 계속 보류)   11,536청크
     └ 04  보충 19문서 병합  03 + supplement_20261014                              15,682청크  ← 서비스
              · 보호 해제 9문서 (EMA·FDA·HC, 이전 평가 보류) + CT.gov 프로토콜·SAP 10문서 (5개 시험)
              · 1,506쪽, 4,146청크, VLM 78회(채택 74)
              · 병합: pipelines/pipeline_h/merge_release.py (rag.release.compose)
```

| 항목 | 값 |
| :-- | :-- |
| 청킹 설정 | `preprocessed-chunks-13-closed-row-blocks` (02·보충 run 동일) |
| 검토 필요 unit | 02: 728개, 보충: 215개 — 원문·후보·거절 이유와 함께 보존, 검색 적격성과 별도 |
| 무결성 검사 | 원본 메타데이터 불일치 0, 모든 문서 색인 연결, bbox·원본/파생본 SHA·표현 범위·벡터 정규화·ID 연결 통과 |
| 결정 | 2026-10-14 사용자 결정: 9문서 보호 해제(이후 시각 전처리의 독립 평가 자료로 쓰지 않음), CT.gov 10문서 포함. PMDA·대조약 Stelara 제외 |

산출물 위치: `data/versions/02_…/preprocessed/extraction`, `03_…/preprocessed/extraction`, 보충 run은 `pipelines/pipeline_h/runs/supplement_20261014/extraction`. 04 자체에는 `preprocessed/`가 없고 03 + 보충 run을 참조한다.

## 3. 서빙하지 않는 전처리 결과

| 버전 | 상태 |
| :-- | :-- |
| 01 파일럿 (`01_pilot_baseline_2026-10-10`) | 이전 서비스 색인. 백업·비교용 |
| 02, 03 | 04의 입력. 단독 서비스 안 함 |
| **05 `05_section_titles_2026-10-15`** | 만들어 비교한 뒤 **채택하지 않은** 재청킹 후보(2026-10-15 02:34 생성, `versions.json` 미등록). 03 + 보충 run을 절 규칙 `sections-9`로 다시 청킹해 16,725청크(벡터 일부 재사용, 다수 신규 계산). 절 정보가 빈 청크 비율 04 27% → 05 13%. 서비스·DB 미적재 |

04는 기본 절 규칙 `sections-8`로 만들어졌다. `sections-9`의 한 단계 번호 제목 규칙은 기본으로 채택하지 않았다. 코드 4.10.2에는 새 빌드용 `sections-10`(독립 시험 제목 인식 보강)이 들어 있지만, 이 규칙으로 만든 색인은 아직 없다([문맥 연결 수정](../rag/CONTEXT_LINK_FIX_2026-10-16.md) 4절).

## 4. 현재 색인에 남아 있는 전처리 한계

- 의미 정확도는 인증하지 않았다. 확인한 것은 출처·구조·메타데이터·범위·벡터 무결성이다. 셀 정확도 수치(88/90)는 ABP654 15쪽 개발 표본의 값이다.
- 04 청크의 27%(4,227개)에 절 정보가 없다. 헤더에 절 경로가 안 붙어 검색 단서가 약하다.
- 이전 시험 제목을 잘못 상속한 헤더가 04 청크·벡터에 남아 있다. 4.10.2는 문맥 공급 단계에서만 차단한다([05 생성·검증](05_GENERATION_VERIFICATION.md) 2절).
- 연속 표 후보 94개 보류, 알려진 전사 오류(FDA Table A `FcγRIIIa`, HC Table 16 셀 병합) 미수정.
- 작은 문자 좌표 경고 16개는 원문·원시 block을 남긴 채 미해결.

## 5. 새 전처리 run

```powershell
# pipelines/pipeline_h/config.example.json → config.local.json 을 먼저 만든다
.\.venv\Scripts\python.exe -X utf8 -m pipelines.pipeline_h.run --run pipelines/pipeline_h/runs/<새_run_이름>
# 기존 릴리스에 문서를 보탤 때: prepare_supplement → run --supplement → merge_release
#   merge_release·rechunk_release는 릴리스를 만든 직후 storage.publish_release로 PostgreSQL + pgvector에 올린다(10-18).
#   서비스에 내보내기: python -m storage.publish_release data/versions/<id>/embedded/release --serve → 서비스 재시작
```

Docling·Paddle 작업은 별도 가상환경과 로컬 모델이 필요하다(`pipelines/pipeline_h/vendor/compare_study_standalone/setup.ps1`, `download_models.py`).

## 6. 원 기록

- [전처리 최종 선정](../preprocessing/FINAL_PREPROCESSING_DECISION_2026-10-14.md) — A~H 비교, H 구성과 정책
- [H v2 완료 보고](../preprocessing/pipeline_h/FINAL_REPORT_ustekinumab_20261014_v2.md), 보충 run `pipelines/pipeline_h/runs/supplement_20261014/FINAL_REPORT.md`
- [pipeline_h README](../../pipelines/pipeline_h/README.md), [보완 설계](../preprocessing/pipeline_h/REPAIR_DESIGN.md), [작업 기록](../preprocessing/pipeline_h/WORK_LOG.md)
- [파일럿 파이프라인 상세](../DATA_PREPROCESSING_PIPELINE.md) (01을 만든 과정)
