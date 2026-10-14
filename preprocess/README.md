# RA 문서 전처리: Paddle + 로컬 Qwen

현재 처리/개발 기준(2026-10-09)은 [운영 원칙](../docs/PIPELINE_PRINCIPLES.md)이다. 검증된 native → 일반 PaddleOCR → 표 구조 복구 → 남은 복잡 영역 Qwen 순서로 처리한다. 여러 탐지 영역이나 낮은 문자 신뢰도만으로 VLM에 넘기지 않는다. 승인된 범위는 EMA/FDA/HC 5상품·92문서이며 보류 9문서는 제외한다. [최신 원문 검토](../docs/preprocessing/CASCADE_AND_RAG_SOURCE_REVIEW_2026-10-09.md)의 199개 큐는 재분류할 이전 후보이며 일괄 실행 대상이 아니다. 아래 날짜별 실행과 `table-grid-2`는 당시 구현 이력이고, 새 계약 적용 여부는 후속 기록으로 구분한다.

2026-10-08 파이프라인 검증 결과: [청킹·메타데이터·표 제목·RAG 비교](../docs/rag/PIPELINE_REVIEW_2026-10-08.md). 반복 머리말·쪽번호는 원본 추출에 보존하고 검색 청크에서 제외한다. 내용 없는 상위 제목은 하위 본문과 묶으며, 다른 절·시험이나 표·그림 경계를 넘어 합치지 않는다. 앞 페이지의 마지막 실질 내용이 표 캡션이고 다음 페이지 상단에 해당 표가 이어질 때 제목을 연결한다. 중간 본문·새 캡션·다른 열이 있으면 연결하지 않는다.

완료된 추출의 카탈로그 메타데이터를 갱신하려면 다음처럼 새 출력에 재구성한다. 문서 ID와 원본 SHA-256이 모두 일치해야 하며, 검색 정책·평가 분할은 기존 값을 유지한다. 명시된 유효한 문서 날짜와 최종 URL만 가져오고 다운로드 날짜를 문서 날짜로 사용하지 않는다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m preprocess regroup --run data/preprocessed/ra_workflow_native_v1_2026-10-08/labels --out data/preprocessed/my_review --metadata-catalog data/catalog/documents.jsonl
.\.venv\Scripts\python.exe -X utf8 -m preprocess chunk --run data/preprocessed/my_review --out data/rag/candidates/my_review
```

현재 실행 진입점: [로컬 PDF 워크플로우](../docs/preprocessing/LOCAL_PDF_WORKFLOW.md). `python -m preprocess workflow --input <PDF/폴더> --out <출력> --no-qwen`으로 전체 페이지 기본 추출·청킹·후보 검색 인덱스·보고서를 먼저 만든다. Qwen/추가 라벨 OCR 보완은 별도 출력에 페이지 캐시를 제공해 실행한다. `--resume`으로 같은 코드·조건의 실행을 재개한다. 2026-10-08 실행 범위는 기존 5문서·523페이지 전체로 고정했다. 아래 개별 명령은 단계별 도구와 이전 구현 기록이다.

최신 문맥 보완·재검증: [연결 누락·표 내부 각주·목차 처리](../docs/preprocessing/VISUAL_CONTEXT_REVALIDATION_2026-10-08.md). `visual-context-3`는 원문 기호·참조와 레이아웃 라벨 충돌을 제한적으로 보완하고, 원문 대조를 통과한 표 각주를 별도 링크로 기록하며, 점선 목차의 본문 참조 오탐을 제외한다. 완료 추출은 별도 빈 폴더에 `regroup`한 뒤 `chunk`하면 적용된다. 기존 버전 계약도 검증 가능하다. 국문 질의 영문화는 `rag/search.py --rewrite-korean --local-only`로 선택할 수 있으며 형식·문자 보존 검사 실패 시 원질의를 사용한다. 실제 인덱스와 맞는 `--rag-dir`을 지정하며, 이는 모델의 의미 정확도나 미검토 후보 사용을 승인하는 옵션이 아니다.

데이터 추가·개정본 연결: [증분 데이터셋 파이프라인](../docs/preprocessing/DATASET_V2_PIPELINE_2026-10-08.md). `python -m preprocess.dataset`으로 카탈로그 기반 명세·추가분 조사·개발 모델 큐를 만들고 평가 보호 문서를 제외한다. 기존 77개와 추가 42개는 별도 버전의 인벤토리로 연결했다.

최신 그림 규칙: [축·범례 라벨의 검색 연결](../docs/preprocessing/FIGURE_LABEL_RETRIEVAL_2026-10-08.md). `process`와 `enrich`는 문자 대조를 통과한 필드를 자동으로 검색용 표현에 포함한다. 누락 라벨은 `figure-labels --targeted --tight`로 로컬 Paddle OCR을 보충하고, 확인하지 못한 필드는 검토 상태로 남긴다.

최신 표 문맥 규칙: [제목·각주·출처 복원](../docs/preprocessing/TABLE_CONTEXT_REPAIR_2026-10-08.md). 새 `process`에 자동 적용하며, 완료된 추출은 `regroup --run <기존 폴더> --out <빈 별도 폴더>`로 모델 호출 없이 보완할 수 있다.

문서 안내: [전처리 파트](../docs/README.md#전처리). 현재 규칙은 [1번 표·그림 보완 결과](../docs/preprocessing/STEP1_VISUAL_RULES_2026-10-07.md), 그룹 구현과 이전 평가는 [대표 표·그림 평가](../docs/preprocessing/PREPROCESSING_QUALITY_REVIEW_2026-10-07.md), 설치와 최초 실행은 [Paddle + Qwen 구현 기록](../docs/preprocessing/PADDLE_QWEN_IMPLEMENTATION_2026-10-07.md)을 참조한다. [Docling 비교안](../docs/preprocessing/NON_PADDLE_COMPARISON_OPTIONS_2026-10-07.md)은 별도 평가 후보로 남긴다.

업로드별 진행 상태와 문서별 검토 보고서는 [2번 상태·결과 표시 구현](../docs/preprocessing/UPLOAD_STATUS_2026-10-07.md)을 참조한다. `process`와 `enrich`는 상태 JSON과 종료 보고서를 자동 저장한다.

허용된 표현의 [기존 청커 연결](../docs/rag/PREPROCESSED_CHUNK_INTEGRATION_2026-10-07.md)은 `chunk --run <전처리 폴더> --out <후보 폴더>`로 실행한다. BGE-M3 토크나이저를 로컬에서 사용하며 별도 OCR/VLM 호출은 없다.

`process`는 임의의 PDF 업로드를 추출하고, `inventory`는 PDF/HTML의 후보 영역을 조사한다. 두 명령의 출력과 상태는 별도다.

## PDF 추출과 선택적 보완

기본 `parser_backend=cascade`는 **네이티브 PDF 추출 → 일반 PP-OCR → 복잡한 영역의 선택적 Qwen** 순서로 처리한다. PP-DocLayoutV3는 위치와 영역 종류를 판별하며, PP-OCR mobile의 탐지·문자 인식은 문서 VLM을 띄우지 않는다. 정상 텍스트와 네이티브 표는 재인식하지 않는다. 단순 스캔 표는 완전한 격자 또는 일정한 열에 정렬된 행과 OCR 토큰으로 복원한다. 일반 문자의 낮은 신뢰도만으로 표 전체를 생성하지 않는다.

그림의 문자 라벨은 일반 OCR만으로 원본 crop에 연결하며, 축·범례 역할을 추측하지 않는다. 다중 패널과 복잡한 표의 구조가 일반 OCR 이후에도 해결되지 않은 경우에만 Qwen 후보가 된다. 비공개 셀과 연속 표의 문맥 문제만 있는 경우에는 재판독 호출 대신 검토 상태로 남긴다. 분기 기준은 `visual_router.py`, 단순 표의 구조 판별은 `simple_grid.py`에 있으며 상품명·문서 ID를 사용하지 않는다.

기존 실행을 재현하는 `parser_backend=paddle_vl`은 이전 PaddleOCR-VL 경로다. 신규 로컬 설정은 `cascade`를 사용한다. 경량 모델은 공식 [문자 탐지](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det_safetensors)와 [라틴 문자 인식](https://huggingface.co/PaddlePaddle/latin_PP-OCRv5_mobile_rec_safetensors)을 사용하며, 커밋·파일 해시는 `models/paddle/light_ocr_manifest.json`에 보존한다. 현재 경량 인식은 영어·라틴 문자 대상이다.

차트의 곡선 값을 숫자 표로 만드는 기능은 기본적으로 끈다. 차트 라벨 참고는 같은 Paddle 모델의 `ocr` 작업으로 읽고, 표 형태 줄·숫자만 있는 줄을 제외해 Qwen 설명의 문자 대조에 사용한다. OCR 프롬프트에서도 추정 표가 생성될 수 있어 전체 원시 결과를 따로 보존한다. 이 대조도 독립적인 정답 검증은 아니다. Qwen은 원본 그림 crop과 페이지 개요를 받아 대상 영역만 설명한다. 가까운 작은 패널 제목은 crop에 포함하고 그 후보 연결을 기록한다.

번호 캡션으로 연결되는 여러 패널은 한 `figure_group` 부모 crop으로 묶어 Qwen이 전체 그림을 한 번 설명한다. 물리 패널과 bbox는 그대로 보존한다. 다른 캡션·제목·본문이 연결을 막거나 후보가 모호하면 미연결로 둔다. 앞 페이지의 표 캡션과 다음 페이지의 표, 연속 표 조각도 후보 관계로 보존하되 빠진 셀 의미를 자동 확정하지 않는다.

그림 응답은 패널별 위치·축 이름·선형/로그 스케일 객체로 기록한다. 표 응답은 행·셀 텍스트와 빈 셀·비공개·판독 불가 상태를 받아 코드가 HTML로 변환한다. 이 재판독 격자는 병합 구조를 복원하지 않으므로 원본 병합이 있는 후보는 검토 대상으로 둔다. 기존 Paddle HTML은 보존한다. 연속 표에는 동일 원본·논리 그룹의 이전 표 crop을 문맥으로 제공하고 현재 빈 셀을 채우지 않는다. 원본의 비공개 표시는 후보에서 누락돼도 검토 상태를 유지한다.

```powershell
# 새 PDF/폴더: --pages를 생략하면 모든 물리 페이지를 처리한다.
.\.venv\Scripts\python.exe -B -m preprocess process --input "C:\path\document.pdf" --out data/preprocessed/upload_001

# 누락된 축·범례만 로컬 GPU OCR로 보충하고 별도 검색 후보를 생성
.\.venv\Scripts\python.exe -X utf8 -B -m preprocess figure-labels --run data/preprocessed/upload_001 --out data/preprocessed/upload_001_labels --targeted --tight --max-ocr-calls 20
.\.venv\Scripts\python.exe -X utf8 -B -m preprocess chunk --run data/preprocessed/upload_001_labels --out data/rag/candidates/upload_001_labels

# Paddle 단계만 실행하고 Qwen 작업을 큐에 남긴다.
.\.venv\Scripts\python.exe -B -m preprocess process --input "C:\path\pdf_folder" --out data/preprocessed/upload_002 --no-qwen
.\.venv\Scripts\python.exe -B -m preprocess enrich --run data/preprocessed/upload_002 --max-qwen-calls 20
.\.venv\Scripts\python.exe -B -m preprocess validate --run data/preprocessed/upload_002

# 처리 중/종료 후 상태 확인: 모델 호출 없음
.\.venv\Scripts\python.exe -X utf8 -B -m preprocess status --run data/preprocessed/upload_002 --format text

# 허용된 전처리 표현만 기존 청커에 연결
.\.venv\Scripts\python.exe -X utf8 -B -m preprocess chunk --run data/preprocessed/upload_002 --out data/rag/candidates/upload_002

# 완료된 추출의 표 제목·각주 문맥만 별도 폴더에서 재구성: OCR/VLM 호출 없음
.\.venv\Scripts\python.exe -X utf8 -B -m preprocess regroup --run data/preprocessed/upload_002 --out data/preprocessed/upload_002_context
.\.venv\Scripts\python.exe -X utf8 -B -m preprocess chunk --run data/preprocessed/upload_002_context --out data/rag/candidates/upload_002_context

# 현재 규칙으로 같은 제한 범위를 새 폴더에 실행
.\.venv\Scripts\python.exe -B -m preprocess process --catalog data/catalog/documents.jsonl --document EMA:63563 --pages 35,44,50 --out data/preprocessed/paddle_qwen_current_sample --max-qwen-calls 3
```

기본 설정은 [`configs/preprocess.local.json`](../configs/preprocess.local.json)이다. `--input`은 PDF 파일·폴더 여러 개를 받으며 문서 ID나 규제기관 allowlist를 사용하지 않는다. 업로드 원본은 `originals/`에 SHA-256 사본으로 보존한다. 현재 모델 추출 명령은 PDF만 지원한다. HTML 조사는 아래 `inventory`를 사용한다.

같은 명령을 같은 출력 폴더에서 재실행하면 원본·모델 파일·라이브러리·설정·페이지 이미지 해시가 맞는 Paddle 결과를 재사용한다. Qwen 캐시는 모델 digest·프롬프트·입력 이미지·문맥·설정에 묶인다. 실패한 추론과 잘리거나 스키마가 잘못된 Qwen 응답은 재시도한다. 호출 한도에 도달하면 `pending_budget`으로 남기며 `enrich`로 이어갈 수 있다. 출력 폴더 하나는 한 작업만 사용하고, 동시 업로드는 서로 다른 폴더에 저장한다.

Qwen 프롬프트·응답 스키마·계약 해시는 그림과 표 작업별로 버전 관리한다. 그림 프롬프트만 변경하면 표 계약 해시는 유지된다. 2026-10-09 코드의 계약은 그림 `qwen-figure-6` / `figure-panels-1`, 표 `qwen-table-7` / `table-grid-3-spans`다. 새 표는 행/열 위치와 병합 span을 보존하며 원본 구조가 확인되지 않은 병합 후보는 검토 상태로 둔다. 새 작업의 `visual-region-input-2`는 구성 영역·일반 OCR sidecar·선택 표현·문맥·응답 계약을 해시에 포함한다. 이전 `table-grid-2` 응답과 199개 큐를 새 실행에 그대로 사용하지 않는다. [적용 상태](../docs/preprocessing/PRINCIPLES_IMPLEMENTATION_2026-10-09.md)에서 남은 작업을 확인한다.

Paddle 작업 프로세스를 종료한 뒤 Qwen을 실행해 GPU 메모리를 순차 사용한다. 기본 설정은 Paddle 시작 전에 지정된 Qwen 모델도 메모리에서 해제한다. 다른 서비스가 같은 Qwen 인스턴스를 공유한다면 `release_qwen_before_parser`를 조정한다.

| 추출 산출물 | 내용 |
|---|---|
| `extracted_assets.jsonl` | 본문·표·그림, 원본 페이지/bbox, Paddle 원문, Qwen 후보와 검사 결과, 선택 결과 |
| `source_units.jsonl` | 청킹 연결용 표현 단위와 출처; `retrieval_eligible=true`만 검색 입력 후보 |
| `visual_groups.jsonl` | 공통 캡션·패널·표 조각의 부모 관계, 원본 출처, 위치 기반 연결 후보 상태 |
| `extraction_pages.jsonl` | 페이지 성공/실패, 원본 텍스트, 모델 추론 시간·CUDA 메모리, 원시 결과 경로 |
| `enrichment_queue.jsonl` | Qwen 작업 종류·라우팅 이유·처리 상태 |
| `extraction_review_queue.jsonl` | 부분 실패, 채택되지 않은 표·그림 후보 |
| `paddle_raw/`, `qwen_raw/`, `qwen_attempts/` | 출처에 묶인 원시 모델 결과와 모든 응답 시도 |
| `page_images/`, `crops/` | 모델 입력 페이지와 그림/표 crop |
| `extraction_manifest.json`, `extraction_integrity.json` | 실행 설정·모델 해시·원본 등록·산출물 무결성 |
| `processing_status.json` | 실행 중 단계·문서별 페이지 상태·Qwen 작업 상태 기록 |
| `processing_report.json`, `processing_report.md` | 문서별 완료·대기·실패·검토 상태, 한국어 사유, 출처와 산출물 위치 |

표의 Qwen 후보가 구조·문자 대조·응답 검사에 걸리면 자동 채택하지 않는다. 원래 Paddle 표와 보완 후보를 모두 보존한다. 비공개 셀이나 연속 조각 검토가 필요한 표는 검색 입력에서 차단한다.

그림은 원문 캡션과 필드별 문자 대조를 통과한 라벨을 검색 후보로 남기고 `description_review_required`를 유지한다. 캡션 없는 단일 그림에도 같은 대조 규칙을 적용하며, 남길 문자가 없으면 검색 본문은 빈 상태다. 물리 패널은 별도 검색 후보에서 제외해 같은 Figure의 부모 관계를 유지한다. 생성한 설명은 `enrichment`에 보관하고 검색 본문에는 포함하지 않는다. 모든 모델 결과의 `numeric_evidence_eligible`은 `false`다. 라벨 역할·각주 적용 범위·여러 페이지에 걸친 표의 연결·그림 의미와 수치 정확성은 자동 검사만으로 인증하지 않는다.

`representation_range`는 생성된 표현 안의 문자 범위다. PDF 원문 문자 offset을 뜻하지 않는다. `chunk`는 이 표현을 `rag-chunk-3`의 별도 범위 계약으로 기존 청커에 연결하고 부모·패널 관계를 보존한다. 기존 파싱/OCR의 `rag-chunk-2` 계약은 유지하며 활성 인덱스 갱신은 별도 작업이다.

`status`는 읽기 전용이며 `--write`를 붙이면 보고서만 다시 저장한다. `--write --out <보고서 폴더>`로 기존 추출 폴더 밖에 내보낼 수 있다. 처리 완료와 검토 완료를 구분하고, 설명 검토가 남은 그림의 캡션 검색 후보도 함께 표시한다. 종료 결과 조회는 저장된 추출 파일 체크섬을 확인한다. 강제 종료된 작업은 마지막 `running` 상태가 남을 수 있어 마지막 갱신 시각을 함께 확인한다.

## 대표 표·그림 평가 재현

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m preprocess.quality render --out data/preprocessed/quality_new/references
.\.venv\Scripts\python.exe -X utf8 -B -m preprocess.quality run --out data/preprocessed/quality_new/current --max-qwen-calls 20
.\.venv\Scripts\python.exe -X utf8 -B -m preprocess.quality_eval --run data/preprocessed/quality_new/current --out data/preprocessed/quality_new/current/quality_report.json
```

평가 기준은 특정 원본 5문서·16페이지의 부분 점검이며 전문가 검토나 전체 정확도 측정은 아니다. 동일 원본 Paddle 캐시만 `--seed-cache <이전 실행 폴더>`로 재사용할 수 있고 해시는 다시 검증한다. 일반 업로드에는 `process --input`을 사용한다. 첫 실행 시간은 `quality_execution.json`에 보존하고 이후 재실행은 `execution_history/`에 기록한다.

## 모델 준비와 온프레미스 실행

기존 `.venv`는 실행 제어와 PDF 처리를 맡고, 새 `.venv-paddle`은 추론만 맡는다. PaddleX의 NumPy 제약 때문에 환경을 분리했다. 검증한 Windows Python 3.12.14 환경은 [`requirements-paddle.lock.txt`](../configs/env/requirements-paddle.lock.txt)에 기록했다. CUDA 12.6 torch/torchvision 빌드를 사용한다.

```powershell
# 인터넷이 허용된 배포 준비 환경에서만 수행
uv venv .venv-paddle --python 3.12
uv pip install --python .venv-paddle/Scripts/python.exe -r requirements-paddle.lock.txt --extra-index-url https://download.pytorch.org/whl/cu126 --index-strategy unsafe-best-match
.\.venv-paddle\Scripts\python.exe -B -m preprocess.prepare_models

# 가중치와 매니페스트를 옮긴 뒤 네트워크 없이 검증
.\.venv-paddle\Scripts\python.exe -B -m preprocess.prepare_models --verify-only
```

완전한 오프라인 설치에는 위 환경의 Windows/Python 버전에 맞는 wheel 저장소도 준비하고 `--no-index --find-links <wheel 폴더>`로 설치한다. Ollama에는 별도로 로컬 vision 모델을 준비한다. 현재 설치되어 실제 실행한 모델은 `qwen3.5:9b`이며 digest를 각 추출 결과에 저장한다. 자동 pull은 하지 않는다.

준비 스크립트는 공식 모델의 고정 커밋을 내려받고 `models/paddle/model_manifest.json`에 파일 해시를 기록한다. 런타임은 `PP-DocLayoutV3_safetensors/`와 `PaddleOCR-VL-1.6/`의 로컬 파일만 사용한다. HF 오프라인 모드와 Paddle worker의 외부 연결 차단을 켜고, Qwen HTTP 주소는 localhost/loopback으로 제한한다. 프록시와 redirect도 사용하지 않는다.

## 인벤토리 조사

`inventory`는 PDF 모든 페이지의 텍스트 품질, 선/문자 배치 기반 표, 이미지 배치, 벡터 도형, 캡션을 조사한다. HTML은 DOM 표·이미지 위치를 조사한다. 이 명령 자체는 모델 추론을 수행하지 않는다.

### 실행

프로젝트 루트에서 기존 Python 환경으로 실행한다. 추가 패키지는 필요하지 않다.

```powershell
.\.venv\Scripts\python.exe -B -m preprocess inventory --catalog data/catalog/documents.jsonl --out data/preprocessed/inventory_2026-10-07 --offline --workers 2
.\.venv\Scripts\python.exe -B -m preprocess inventory --input C:\path\document.pdf --out data/preprocessed/new_document --offline
.\.venv\Scripts\python.exe -B -m preprocess resume --run data/preprocessed/inventory_2026-10-07
.\.venv\Scripts\python.exe -B -m preprocess validate --run data/preprocessed/inventory_2026-10-07
```

- `--input`은 파일/폴더 여러 개와 PDF/HTML을 지원한다. 폴더는 재귀적으로 조사한다. 같은 파일의 중복 사본은 한 번 등록하고 원래 경로들을 보존한다. 업로드 파일은 출력 폴더의 `originals/`에 SHA-256 사본으로 저장한다.
- `--catalog`의 상대 파일 경로는 프로젝트 루트 기준이다. 다른 루트는 `--root`로 지정한다. `--document`를 반복하면 선택 문서를 조사한다.
- 기본 `--table-strategies lines text`는 설명형·정수 표와 무테 표 후보도 다룬다. `text` 방식은 다단 본문도 후보로 잡을 수 있어 격자의 정확성을 승인하지 않는다. 선 격자 없이 문자 정렬만으로 잡힌 후보는 `layout_classify`로 보내며 표 추출로 직행하지 않는다.
- `--workers` 기본값은 2다. PyMuPDF 작업은 스레드 대신 별도 프로세스를 사용한다.
- 인벤토리는 항상 오프라인이다. `--offline`은 명시적인 운영 설정을 위한 옵션이며, 옵션을 생략해도 다운로드/외부 API 호출은 없다. HTML 이미지 URL도 가져오지 않는다.
- 출력 폴더는 한 작업용으로 사용한다. 재실행은 새 선택 범위의 집계 파일을 만들며 입력·설정이 같은 페이지의 checkpoint를 재사용한다. 별도 범위의 결과를 보관하려면 별도 출력 폴더를 사용한다.
- 문서/페이지 실패가 있으면 CLI는 종료 코드 1을 반환하고 실패 사유를 저장한다. 성공한 페이지의 checkpoint는 유지하며 실패 페이지는 재실행한다.
- `validate`는 저장된 스키마, 문서/페이지/자산 연결, 영역 범위, 현재 원본 해시를 읽기 전용으로 검사한다. 탐지나 추출 정확도를 측정하는 검사는 아니다.

## 산출물

| 파일 | 내용 |
|---|---|
| `documents.jsonl` | 문서별 후보·OCR 필요·조사 상태 |
| `pages.jsonl` | 모든 PDF 물리 페이지 또는 HTML 파일 전체의 신호·품질·상태 |
| `assets.jsonl` | 후보 영역·유형·출처·다음 처리 경로·레거시 결과 연결 후보 |
| `inventory.schema.json` | `ra-inventory-1` 문서/페이지/자산 계약 |
| `inventory_summary.json` | 범위·후보·실패·호출 수·실행 시간 |
| `inventory_report.md` | 문서별 사람이 읽을 수 있는 요약 |
| `review_queue.jsonl` | 실패 문서와 불확실 시각 영역의 후속 작업 목록 |
| `manifest.json`, `checkpoints/` | 재개 설정과 원본/파생 PDF·설정·레거시 출력 해시에 묶인 페이지 결과 |

PDF 좌표는 `pymupdf_unrotated_crop_points_top_left`이고 단위는 point다. 페이지 cropbox·mediabox·원래 회전을 저장한다. 탐지 시 메모리상 회전을 정규화하며 원본 PDF는 저장하거나 변경하지 않는다. HTML은 디코딩된 Unicode 원문 문자 범위와 노드 위치를 사용하고 PDF 페이지/좌표를 생성하지 않는다. 기존 HTML 섹션 번호도 다시 매기지 않는다.

`source_refs`는 조사에 사용한 원본 또는 기존 글꼴 복구 PDF의 경로와 SHA-256을 가진다. 원본 버전 해시와 파생 PDF 해시를 구분한다. 기존 OCR/VLM 파일은 원본 버전·영역이 맞는 연결 **후보**이며 현재 설정에서 재사용 승인을 뜻하지 않는다.

## 해석과 한계

- `heuristic_inventory_complete`는 모든 페이지의 휴리스틱 조사를 수행했다는 뜻이다. 모델 기반 레이아웃 검사나 내용 추출이 완료됐다는 뜻이 아니다.
- 후보가 없으면 표·그림 존재 여부를 null/미확정으로 둔다. 모든 PDF 페이지에 `needs_layout_check=true`를 유지한다.
- 후보 개수는 탐지된 물리 영역이다. 논리 표의 개수·표 조각을 연결한 결과·탐지 정확도가 아니다. 겹치는 서로 다른 후보와 중첩 영역이 남을 수 있다.
- 캡션만 잡히면 `bbox_status=caption_only`다. 그 좌표를 표/그림 본체의 범위로 사용하지 않는다.
- 작은 이미지·장식처럼 보이는 벡터도 불확실 후보로 남긴다. 이 단계에서 자동 삭제하지 않는다.
- 모든 자산은 `extraction_status=not_run`, `validation_status=unverified_candidate`, `numeric_evidence_eligible=false`다.
- 각주 적용 범위·연속 표 연결·셀 구조·그림 설명은 다음 단계에서 처리한다. `continued` 캡션은 힌트만 기록한다.
- 암호 PDF·읽기 실패·손상된 PDF는 실패로 기록한다. 언어별 OCR 지원 여부는 OCR adapter 도입 때 판정한다. 현재 기하 조사 성공을 언어 판독 성공으로 해석하지 않는다.

## 검증

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

인벤토리 검사는 원본 무결성·중복 입력·checkpoint·회전 좌표·HTML 출처 등을 확인한다. `tests/test_preprocessing_hybrid.py`는 역할별 라우팅·모델 실패/예산·재시도·수치 생성·미채택 후보·산출물 변조·회전 bbox·암호화 업로드 격리를 검사한다. 실제 문서의 탐지 재현율/정밀도와 전사 정확도는 별도 정답셋으로 평가해야 한다.
# 완료된 추출의 재검사와 고정 평가

`python -m preprocess.refine --run <완료된 개발 추출> --out <새 결과>`는 Paddle 결과를 재사용하고 문맥·표 검사를 갱신한 뒤 변경/대기 영역만 로컬 Qwen으로 처리한다. 원본과 평가 보호 정책은 유지한다. `--no-qwen`으로 모델 호출 없이 재검사할 수 있다.

`python -m preprocess.visual_trial prepare/run`은 보호 자료를 별도 고정 평가에만 실행하는 경로다. 원래 데이터셋을 개발 큐로 재배정하지 않는다. 소스·코드·모델과 원본 기준 사전 주석을 봉인해야 실행한다. 에이전트 주석은 전문가 정답으로 승인하지 않는다.

실행 결과와 제한: [표·그림 검토·고정 평가·후보 검색](../docs/preprocessing/VISUAL_REVIEW_AND_TRIAL_2026-10-08.md).
# 제목·캡션·본문 참조 연결

`visual-groups-6`은 일반 본문으로 분류된 긴 표 제목도 native 원문·표 첫 전체 폭 셀의 제목·가까운 위치가 일치하는 유일 후보이면 복구한다. 인접 페이지의 2열 표는 빈 왼쪽 셀과 미완료 괄호의 양쪽 경계 원문이 확인될 때 연속 조각 후보로 연결한다. 레이아웃 라벨·표 HTML·빈 셀은 유지한다. 이 후보의 본체는 검토 대기이며, 제목 텍스트가 검색돼도 실제 검색 가능한 표 청크가 없으면 역링크로 본체를 복원하지 않는다.

새 업로드의 기본 그룹화에 적용되며, 완료된 추출은 `python -m preprocess regroup --run <완료된 추출> --out <새 폴더>`로 추가 OCR/VLM 없이 갱신한다. [실제 RMP 보완·153페이지 개발 재검증](../docs/preprocessing/VISUAL_CONTEXT_RMP_REVALIDATION_2026-10-08.md)에 조건·한계·결과 경로를 기록했다. 독립 평가와 전문가 판정은 별도로 남아 있다.

새 `process` 결과에는 `visual_context_units.jsonl`, `visual_context_links.jsonl`,
`visual_context_targets.jsonl`와 해시 계약을 함께 저장한다. 완료된 이전 추출은
`python -m preprocess visual-context --run <run> --out <new-sidecar>`로 모델 호출 없이
연결할 수 있다. 입력을 덮어쓰지 않고 새 폴더를 사용한다.

청킹의 `--visual-context-mode linked`가 기본이며, `none`/`headings`로 개발 비교한다.
원문·청크 본문 범위는 유지하고 별도 출처 문맥을 header의 토큰 예산 안에 넣는다.
`rag/search.py --visual-context`는 본문 참조에서 관련 시각 객체를 복원하며,
기존 검색 제외·평가 정책을 양쪽에 적용한다. 추가 OCR/Qwen 라우팅은 유지한다.

구현·범위·검증: [문맥 연결 개발 결과](../docs/preprocessing/VISUAL_CONTEXT_IMPLEMENTATION_2026-10-08.md).

`visual-groups-5` / `visual-context-2`는 단독 번호 캡션, 왼쪽 여백 캡션,
같은 행의 명시적 그림 참조, 설명 뒤 `(See Figure X)` 범위를 처리한다.
여러 페이지에 걸친 그림은 자동으로 합치지 않으며, 미연결 캡션은
`visual_context_summary.json`의 `orphan_caption_source_unit_ids`에 기록한다.
이전 v1 연결 산출물은 당시 규칙으로 검증할 수 있다.
검토·제한·최종 후보: [보류 17건 보완 결과](../docs/preprocessing/VISUAL_CONTEXT_HELD_REVIEW_2026-10-08.md).

고정 조건의 추가 평가: [5문서·30페이지 문맥 연결 및 영문·국문 검색](../docs/preprocessing/VISUAL_CONTEXT_GENERALIZATION_2026-10-08.md).
본문 연결 22/23, 캡션 24/24이며 평가 후보는 118개다. `vision_footnote`로 잘못 분류된
사용법 문장의 연결 누락, 표 내부 각주의 역할 미분리와 국문 검색 실패를 후속 개발 항목으로 기록했다.
이번 결과에 맞춰 production 규칙을 변경하지 않았고, 활성 검색 인덱스는 유지한다.
