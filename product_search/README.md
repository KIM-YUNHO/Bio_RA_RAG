# Biosimilar Discovery & Collection (`product_search/`)

- 수정: 2026-10-08 02:58 KST. 아래 보완 단계는 모두 프로젝트 `collect.py`로 옮겼다. 이 폴더는 같은 코드를 `product_search/data/`에 대해 실행하는 입구다. 규칙 파일 `document_kinds.json`·`guidelines.json`은 프로젝트 루트로 옮겼고 `--annotate`는 없앴다(성분·대조약은 수집할 때 기록한다). 규칙은 [수집 기준](../docs/collection/temp/COLLECTION_STANDARD.md). Yesintek·Usymro는 프로젝트 `data/`에 정식으로 추가했으며 여기 데이터는 2026-10-07 시험 수집본이다.
- 수정: 2026-10-07 17:47 KST. 적재 점검을 반영해 다음을 추가했다. FDA 보충신청 서한·최신 라벨, Purple Book 허가 상태, 게시된 시험 결과, 문서 층 분류, 지침 수집(`guidelines.py`), 공백 점검(`coverage.py`), SQLite 이전 버전 정리. 근거는 [적재 문서 점검과 보완](../docs/collection/temp/COLLECTION_GAP_REVIEW_2026-10-07.md).

성분명 또는 제품명을 검색하여 글로벌 규제기관(EMA, FDA, Health Canada) 및 ClinicalTrials.gov의 바이오시밀러 승인·임상 문서를 탐색하고,
해당 제품들의 원문 PDF·HTML 문서 수집, 텍스트 파싱, SQLite DB 적재를 자동으로 수행하는 독립 모듈 및 웹 UI입니다.

---

## 0. 사용 흐름 (2026-10-08 03:25 KST)

`product_search/data/`는 **스테이징**이다. 받아서 확인한 제품만 프로젝트 `data/`로 옮긴다. 규칙 전체는 [적재 파이프라인](../docs/collection/INGESTION_PIPELINE.md) 3·4·7절이다.

```bash
uv run python -m product_search.discover ustekinumab          # 성분명 → 제품 목록 (제품명·두 번째 EU 허가명으로도 검색)
uv run python -m product_search.collect_products usgena       # 스테이징 수집 (대표 허가 Uzpruvo의 문서만 받음)
uv run python -m product_search.promote                       # 스테이징 제품 목록
uv run python -m product_search.promote AVT04                 # 확인표: 허가, 기관별 문서·쪽수·원문 URL, 시험, 빠진 관할
uv run python -m product_search.promote AVT04 --apply         # 프로젝트로 옮김 (재다운로드 없음) → targets.json, SQLite, build_indexes, verify
```

- **제품 = 개발코드 하나.** EMA 목록은 허가마다 한 줄이다. 그래서 같은 제품의 EU 허가 두 개(Yesintek·Usrenty, Uzpruvo·Usgena, Otulfi·Fymskina)를 한 제품으로 묶고 `licences`에 적는다. 문서는 대표 허가(EU 허가일이 가장 이른 것; 같은 날이면 미국·캐나다 이름과 같은 것)만 받는다. `product_id`는 검색 순서와 무관하게 개발코드다. ustekinumab 검색 결과는 11개 행에서 8개 제품이 되었다.
- **대조약(원 의약품)도 찾는다**(2026-10-08 03:50). 성분명 검색 결과 맨 앞에 `kind: reference` 제품이 나오고(EMA 비바이오시밀러 허가 + Purple Book 351(a)), 바이오시밀러는 `reference_product_id`로 그것을 가리킨다. `stelara`로 검색하면 대조약만 나온다. 대조약은 시험을 찾지 않는다.
- **한계:** 캐나다 제품명이 EU·미국과 다르면 찾지 못한다(AVT04의 Jamteki). PMDA는 검색하지 않는다. 확인표에 "not found"로 나온다.
- 지금 스테이징에 있는 것: AVT04(Uzpruvo/Selarsdi), DMB3115(Imuldosa), CNTO1275(Stelara, 프로젝트로 옮김). 둘 다 2026-10-08 시험 수집이며 프로젝트로 옮기지 않았다. 이전 ID 규칙으로 받은 `BMAB1200_YESINTEK`와 BAT2206 시험본은 지웠다(두 제품은 프로젝트에 정식으로 있음).

## 1. 주요 기능

1. **성분명 검색 (`ingredient`)**:
   - 예: `ustekinumab`, `denosumab`
   - EMA 공식 허가 목록, US FDA Purple Book 351(k) 목록, FDA GSRS API를 결합하여 해당 성분의 모든 바이오시밀러 제품을 식별하고 매핑합니다.
   - 각 제품별 개발 코드(EPAR 본문 빈도 분석 기반), EMA 허가명, US 브랜드, Health Canada 브랜드, 임상시험(NCT ID)을 자동 도출합니다.
2. **제품명 검색 (`product`)**:
   - 예: `yesintek`, `usymro`, `pyzchiva`, `wezlana`
   - 단일 제품의 허가 관할별 식별자와 임상 코드를 정확히 조회하고 대상 제품만 필터링합니다.
3. **데이터 수집 & 적재 (`collect`)**:
   - 기존 `collect.py`의 다운로드·체크섬 캐싱·파싱·FTS5 전문검색 인덱스 구조를 그대로 활용합니다.
   - 대상 기관:
     - **EMA**: 공개심사보고서(EPAR), 제품정보(SmPC/라벨), 위험관리계획(RMP), 절차정보, 의약품 개요
     - **FDA / Purple Book**: 351(k) 카탈로그 행, Drugs@FDA 승인 패키지 심사 문서(통합심사, 품질심사, 라벨 등)
     - **ClinicalTrials.gov**: 임상 레코드 메타데이터, 3상 프로토콜 및 통계분석계획(SAP) PDF
     - **Health Canada**: 결정요약(SBD), 제품설명서(Product Monograph) PDF
   - 수집된 모든 페이지는 SQLite FTS5(`page_search`) 전문검색 테이블에 자동 인덱싱됩니다.
   - `collect.py` 단계 외에 제품마다 다음을 함께 받습니다.
     - **FDA 서한·라벨 (`fda_letters`)**: Drugs@FDA 문서 링크의 승인 서한 전부(심사 패키지가 있는 원 신청은 제외)와 최신 라벨 1건. 보충신청(제형·적응증·상호교환성)과 패키지가 아직 공개되지 않은 제품의 미국 근거를 채웁니다.
     - **Purple Book 상태 (`products.jsonl`의 `us_licensure`)**: 바이오시밀러/상호교환 구분, 승인일, 상호교환 승인일, 최초 상호교환 독점권 만료일, 기준월.
     - **게시된 시험 결과 (`study_results.jsonl`, SQLite `study_results`)**: ClinicalTrials.gov 결과의 측정 항목별 원값. 의뢰자가 게시한 값이며 규제기관이 검토한 값이 아닙니다.
     - **성분·대조약 (2026-10-07 18:05 KST 추가)**: 모든 제품 문서 행에 `ingredient`를 넣습니다. 지침은 성분이 없어 `null`입니다. 제품 행에는 `ingredient`, `us_proper_name`(예: ustekinumab-kfce), `reference_product`(Purple Book의 대조약, 예: Stelara)를 넣습니다. EU에서만 허가된 제품은 EMA 목록에 대조약 정보가 없어 `reference_product`가 `null`입니다. 수집할 때 `collect.py`가 기록합니다.
     - **문서 층 (프로젝트 루트 `document_kinds.json`)**: 모든 문서에 `family`(기능), `author`(작성 주체), `tier`, `default_search`를 붙입니다. 기본 검색에 넣는 층은 core·supporting·guideline이고, 제품정보·시험계획·RMP·서신은 질문 유형이 맞을 때만 켭니다.
4. **지침 수집 (`guidelines.py`)**: 프로젝트 루트 `guidelines.json`의 EMA·FDA·Health Canada 바이오시밀러 지침을 받습니다. 상태(Draft/Final/Adopted), 참조번호, 발행·게시일은 수집할 때 각 기관 페이지에서 읽습니다. 지침 문서는 `product_id`가 없고 `role`은 `guideline`입니다.
5. **공백 점검 (`coverage.py`)**: 프로젝트 코퍼스(`data/`, 읽기만 함)와 `product_search/data/`의 제품별·관할별 문서 기능, 미국 허가 상태, 시험 첨부·결과·PK 항목을 표로 보여주고 공백을 나열합니다. 결과는 `data/catalog/coverage.json`에 저장합니다.
6. **대화형 웹 UI (`app.py`)**:
   - 브라우저 기반 검색, 제품별/일괄 다운로드 시작, 실시간 진행 로그 스트리밍, 수집 문서 및 원본 PDF 열람/다운로드를 지원합니다.

---

## 2. 데이터 저장 및 적재 구조

`product_search/data/` 디렉토리에 프로젝트 표준 레이아웃과 동일하게 저장됩니다:

- `data/raw/`: SHA-256 해시 기반 content-addressed 파일 저장소 (중복 다운로드 방지)
- `data/catalog/`:
  - `products.jsonl`: 수집된 바이오시밀러 제품 카탈로그 (식별자, 개발코드, 허가기관 매핑)
  - `documents.jsonl`: 수집된 규제 문서 메타데이터 (출처, role, SHA-256, URL, 페이지 수)
  - `studies.jsonl`: 연계된 임상시험 메타데이터 (NCT ID, Phase, 스폰서, 프로토콜 첨부 현황)
  - `download_cache.json`: URL별 HTTP 헤더, etag, sha256 캐시
- `data/parsed/`:
  - `<document_slug>.pages.jsonl`: 문서별 페이지 단위 텍스트 및 레이아웃 바운딩 박스
  - `<document_slug>.layout.txt`: Poppler `pdftotext -layout` 레이아웃 보존 텍스트
- `data/collection.sqlite`:
  - `products`: 제품 마스터 테이블
  - `studies`: 임상시험 마스터 테이블
  - `document_versions`: 문서 버전 테이블
  - `pages`: 페이지별 텍스트 및 메타데이터
  - `page_search`: FTS5 가상 테이블 (전문 검색 인덱스)

---

## 3. 실행 방법

### A. 웹 UI 서버 실행
```bash
uv run python -m product_search.app --port 8085
```
- 브라우저에서 `http://localhost:8085/` 접속
- 성분명(예: `ustekinumab`) 또는 제품명(예: `yesintek`, `usymro`) 검색 후 [수집] 또는 [일괄 수집] 버튼 클릭

### B. CLI 명령어로 탐색
```bash
# 성분명으로 제품군 탐색
uv run python -m product_search.discover ustekinumab

# 제품명으로 특정 제품 탐색
uv run python -m product_search.discover yesintek
uv run python -m product_search.discover usymro
```

### C. CLI 명령어로 데이터 수집 실행
```bash
# 제품명으로 바로 수집 (탐색 후 다운로드 & SQLite 적재)
uv run python -m product_search.collect_products yesintek usymro

# 지침 수집 (전체 또는 ID 지정)
uv run python -m product_search.guidelines
uv run python -m product_search.guidelines FDA-QA-REV4

# 제품별 공백 점검
uv run python -m product_search.coverage
```

---

## 4. 수집 완료 현황 (Yesintek & Usymro)

- **Yesintek** (Biocon Biologics / 개발코드 `Bmab1200`):
  - EMA EPAR (125쪽), SmPC (175쪽), RMP (80쪽), 개요 등 6건
  - FDA 승인 패키지: 승인서, 라벨, 품질심사(ChemR), 다학제심사(MultidisciplineR) 등 7건 (332쪽)
  - ClinicalTrials.gov: NCT05335356 3상 프로토콜 PDF (104쪽), SAP PDF (86쪽)
  - Health Canada: SBD (18섹션), Product Monograph 2건 (186쪽)
- **Usymro** (Gedeon Richter / Bio-Thera 개발코드 `BAT2206`):
  - EMA EPAR (100쪽), SmPC (118쪽), RMP (86쪽), Procedural (4쪽) 등 6건
  - FDA Starjemza (BLA 761419) Purple Book & Drugs@FDA 카탈로그 연계
  - ClinicalTrials.gov: NCT04371185 (1상), NCT04728360 (3상) 연계
- **통계 (최초 수집)**: 총 2개 제품, 24개 공식 규제 문서, 1,437페이지 파싱 및 SQLite FTS5 적재 완료 (오류 0건).
- **재수집 (2026-10-07 17:42 KST)**: 28개 문서, 1,549페이지, 오류 0건.
  - Yesintek: S002 승인서한과 최신 라벨 추가.
  - Starjemza: 심사 패키지가 없어 FDA 문서가 0건이었는데, 승인서한과 라벨이 추가됨.
  - 미국 허가 상태(Purple Book 2026-09): Yesintek 상호교환 2025-04-30, Starjemza 상호교환 2025-05-22.
  - 시험 결과: 20개 항목(Yesintek 3상). 1상 PK 시험(NCT06738277, NCT04371185)은 결과 게시도 첨부도 없어 PK 수치는 심사보고서에서만 확인할 수 있습니다.
- **지침 (2026-10-07 17:44 KST)**: EMA 6, FDA 7, Health Canada 1. 문서 21개, 239쪽.
- **전체**: 카탈로그와 SQLite 모두 문서 49개, 1,788쪽.
