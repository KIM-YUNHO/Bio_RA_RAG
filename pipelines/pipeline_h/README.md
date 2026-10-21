# H 전처리·청킹·임베딩 운영 구성

> **project_shared 이관 메모 (2026-10-14 13:46 KST)** — 이 폴더가 프로젝트의 **표준(Main) 전처리 파이프라인**이다.
> - 패키지 경로가 `pipeline_h` → `pipelines.pipeline_h` 로 바뀌었다. 실행은 `python -m pipelines.pipeline_h.<모듈>`.
> - 완료된 v2 결과는 이 폴더의 `runs/`가 아니라 `data/versions/02_pipeline_h_2026-10-14/`에 단계별로 보관한다
>   (`preprocessed/extraction`, `embedded/candidate`, `embedded/release`, `records/`). 아래 본문의 `runs/ustekinumab_20261014_v2/...`는 그 위치를 뜻한다.
> - v1 run(2.8GB, 최종 검증 실패 후 v2로 보완)은 옮기지 않았다. 원본 프로젝트 `pipeline_h/runs/ustekinumab_20261014_v1`에 남아 있다.
> - Docling 어댑터와 `ra_prep` 후처리는 `vendor/compare_study_standalone/`에 있다(원본 `compare_study_standalone/`의 필요한 부분만).
> - 실행 환경 설정: `config.local.json`(이 PC용, 커밋 금지) / `config.example.json`(팀 공유용 템플릿).
> - 새 run은 `pipelines/pipeline_h/runs/<이름>`에 만든다. `prepare.py`는 파일럿 릴리스를 기준선으로 읽는다(`data/versions/01_pilot_baseline_2026-10-10/`).

기존 `preprocess/`, `rag/`, 운영 색인·서비스를 보존하고 새 산출물은 이 폴더에 저장한다. 모델 파일과 검증된 기존 청킹·임베딩 도구는 읽기 전용으로 재사용한다.

사용자가 확인한 범위는 EMA/FDA/Health Canada의 승인 92문서다. SB17·ABP654·CTP43·BMAB1200·BAT2206을 처리하며 기존 평가 보류 9문서, 대조약, 가이던스, PMDA·CTG는 이번 범위에서 제외한다.

## 유지할 구조

- 문서·버전·원문 SHA256, 제품·기관·문서 종류·작성 주체·날짜·검색 정책
- PDF 물리 페이지/인쇄 쪽, HTML 원문과 로컬 렌더 페이지의 구분
- 원시 블록 ID·bbox·읽기 순서·절 계층·원시 셀과 병합 범위
- 표·그림·캡션·각주·본문 참조·연속 표의 출처 연결
- 원래 후보·VL 후보·선택/거절 사유와 인식·구조·의미 검토 상태

## 기존 청킹·임베딩 전략

`rag.preprocessed_chunks`의 기존 계약과 `rag.chunking`을 사용한다. 본문 목표 768/최대1024토큰, 같은 절 내 overlap96, 표/그림2048토큰, BGE-M3 입력8192 상한이다. 절·시험·분석 전환을 지키며 표 헤더·닫힌 행·각주를 보존한다. 원문 문자열 범위와 헤더를 유지하고 임베딩에서 근거를 자르지 않는다.

임베딩은 로컬 `BAAI/bge-m3`, 1024차원·cosine·정규화 dense 벡터다. 기존 벡터는 `header + newline + text` 전체 입력이 동일하고 모델 revision이 동일할 때만 재사용한다. 새 임베딩에 외부 API를 사용하지 않는다.

현재 구현·실행 상태와 재개 지점은 [작업 기록](../../docs/preprocessing/pipeline_h/WORK_LOG.md)에 기록한다. 보완 설계는 [REPAIR_DESIGN](../../docs/preprocessing/pipeline_h/REPAIR_DESIGN.md), v2 완료 보고는 [FINAL_REPORT](../../docs/preprocessing/pipeline_h/FINAL_REPORT_ustekinumab_20261014_v2.md)다.

## 기능별 모델과 도구

| 기능 | 모델·도구 | 남기는 정보 |
| :-- | :-- | :-- |
| 범위·문서 메타데이터 | 기존 승인 목록, catalog, 기존 원문/파생본 등록 | 92문서 ID·버전·SHA·제품·기관·유형·날짜·검색/평가 정책, catalog 전체 스냅샷 |
| PDF 구조·텍스트 | Docling/Heron, pypdfium2 | 블록 ID·부모·읽기 순서·bbox, 원문 페이지 |
| 일반 OCR | RapidOCR, PP-OCRv6 det_small/rec_small, 방향 분류 ch_ppocr_mobile_v2.0_cls_mobile | OCR·재판독·검토 상태 |
| 기본 표 구조 | TableFormer accurate | 행·열·rowspan·colspan·원시 셀 bbox |
| 후처리 규칙 | 기존 ra_prep 코드 | 머리말·절·발화자 후보·연구 ID·재판독·표 복원 이력 |
| 독립 누락 점검 | PP-DocLayoutV3, 닫힌 테두리 규칙 | 동일 원문의 표·차트 영역과 충돌 이력 |
| 선별 전사 | PaddleOCR-VL-1.6-0.9B | 누락/불완전 표, 의심 기호의 원문 crop·전체 응답·보존 검사 |
| 그림 검색 | 원문 제목·본문 참조·일반 OCR/PyMuPDF 인쇄 문자 | 그림·패널·캡션 연결; 의미 설명·곡선 수치 생성 없음 |
| 청킹·임베딩 | 기존 청킹 코드, BGE-M3 | 원문 표현 범위·구조 경계·메타데이터·1024차원 벡터 |

`ra_prep`은 이 표의 후처리 코드 이름이다. Docling 소속 모델 이름이 아니다.

## 실행·재개

### DOM·좌표 보완 완료 버전

현재 수정 완료 결과는 `runs/ustekinumab_20261014_v2/release`다. v1의 전처리와 완료 임베딩11,445개는 보존했다. v2는 92문서, 11,536청크/벡터이며 v1 벡터11,277개를 그대로 재사용하고259개만 새로 계산했다. 원본 PDF4,102쪽과 원본 HTML10문서의 DOM 미리보기99쪽을 구분한다. HTML 미리보기 쪽 수는 원본 HTML의 페이지 번호가 아니다.

새 run은 입력 kind에 따라 `source_router`가 HTML을 원본 DOM 경로로 라우팅한다. HTML에는 Docling/Paddle의 시각적 표 재생성을 적용하지 않는다. 제목·목록·표·rowspan/colspan·sup/sub·원문 범위와 DOM node path를 보존한다. HTMLParser가 내용의 원본이며 Story PDF는 실제 측정된 미리보기 좌표를 공급한다. 단락 단위로 배치하여 페이지 밖으로 그리지 않으며 출력文字도 독립적으로 대조했다.

새 DOM 필드는 새 폴더의 어댑터가 엄격한 청크 출처 schema에 추가한다. 기존 원본 파일은 수정하지 않는다. 새 입력을 예전 체크포인트로 조용히 재분류하지 않으며, 기존 v1처럼 이미 선별된 run은 새 버전으로 보완해야 한다.

현재 v2의 작은 문자 좌표 경고16개는 원시 block·원문·페이지 이미지를 유지한다. 이 경고가 페이지의 다른 정상 본문까지 청킹에서 제외하지 않게 했다. 독립 문자 좌표로1개 항목을 복구했다. 미해결 경고는 추출 정확도를 인증하는 근거가 아니다.

`source_content_audit.json`, `independent_html_coverage.json`, `preview_literal_audit.json`, `final_postcheck.json`이 원문 보존·출처·벡터 연결 근거다. 원본 HTML의 독립 텍스트 노드1,835개에서 미포함0개를 확인했으며, 전체 추출 unit은 인덱스 포함 또는 명시적인 제외로 모두 처리 내역을 기록했다. `provenance_correction.json`은 HTML의 도구 이름만 정정할 때 임베딩 입력/ID/벡터가 변하지 않았다는 근거다.

```powershell
# (원본 프로젝트에서 v2를 만든 명령. project_shared에서는 v2 run 폴더 전체가 아니라 단계별 사본만 있으므로 재실행 대상이 아니다)
.\.venv\Scripts\python.exe -X utf8 -m pipelines.pipeline_h.repair_run --run pipelines/pipeline_h/runs/ustekinumab_20261014_v2
```

기존 chunker의 페이지별 unit 조회만 메모리에서 인덱싱해 반복 전체 검색을 줄였다. 기존4쪽 fixture의22청크가 변경 전후 완전히 같은 것을 확인했다. 토큰 예산·표 분할·overlap·구조 경계는 변경하지 않았다.

프로젝트 루트 PowerShell에서 실행한다. 같은 run은 체크포인트부터 재개하며, 중복 controller는 막는다.

```powershell
# 새 run 예시 (이름은 새로 정한다). --config 기본값은 pipelines/pipeline_h/config.local.json
.\.venv\Scripts\python.exe -X utf8 -m pipelines.pipeline_h.run --run pipelines/pipeline_h/runs/<새_run_이름>
```

현재 실행 중인 작업은 `runs/ustekinumab_20261014_v1/pipeline_status.json`, 문서 내 진행은 `docling_live_progress.json`, 문서 완료 집계는 `docling_progress.json`에서 확인한다. 모델 작업은 GPU를 공유하므로 Docling → PP 검출 → 선별 VLM → BGE 순으로 실행한다.

VLM은 동일 작업의 crop을 4개씩 묶고 이미지 면적 순으로 처리한다. 설치된 Transformers predictor에 배치용 왼쪽 padding을 적용하며, 결과마다 입력 경로와 출력 개수를 확인한다. 배치 오류는 작은 배치로 재시도한다. 요청 작업과 crop SHA-256이 모두 같은 이미지의 응답은 재사용하지만 문서별 source와 후보 검증은 유지한다. 원응답의 원래 입력 경로와 재사용 출처 해시도 저장한다. 배치당 시간을 영역 수로 나눈 값은 `execution.timing_is_amortized`로 구분한다.

현재 run의 `accelerated_config.json`, `acceleration_status.json`, `batch_benchmark/report.json`에 변경 설정·재개 이력·실측 근거가 있다. 기존 응답 74개를 보존한 뒤 표 4개를 시험했으며, 저장된 단건 실행 합계 126.68초 대비 새 4개 배치 36.21초였다. 네 표의 셀 내용·숫자·gate 판정은 모두 같았다. 반복 평균이나 전체 문서 정확도 검증은 아니다. 선별 영역 949개의 서로 다른 작업/crop 조합은 777개이며 중복 172개를 확인했다. `vlm_progress.json.completed`는 재사용을 포함한 처리 영역 수다.

완료 후 `extraction/`, `candidate/`, `release/`, `FINAL_REPORT.md`가 생성된다. 기존 서비스 포인터는 그대로 유지한다. 별도 원문 검토 화면(`review_viewer`)은 10-21 정리에서 제거했다. 릴리스 읽기는 `rag/bundle.py`의 `Bundle`로 한다.

전체 4,160쪽의 의미 정확도를 자동 무결성 검사와 혼동하지 않는다. 기존 개발 표본 재검증 결과와 전체 처리 범위·검토 제외·실제 단계 시간은 별도로 보고한다.
