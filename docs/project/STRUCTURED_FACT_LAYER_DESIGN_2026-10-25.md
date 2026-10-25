# 구조화 사실 계층 설계 — RAG로 풀지 않는 질문

- 작성: 2026-10-25 KST
- 상태: 설계안. 코드·서비스 변경 없음
- 범위: 질문 중 공식 목록 조회, 문서에서 미리 추출해 검수한 사실, 코드 계산으로 답할 부분의 데이터 모델, 구축·검수, 라우팅·표시, 평가, 구현 순서
- 근거로 읽은 것: `ingestion/sources.py`, `ingestion/licence_status.py`, `ingestion/db/migrations/001~002`, `rag/ra/ra_timeline.py`, `rag/ra/ra_derived.py`, `rag/ra/ra_models.py`, `data/catalog/*.jsonl`, `data/reviewed/observations.jsonl`, [평가 현재 상태](../current/07_EVALUATION.md)

## 1. 왜 필요한가

허가일, MAH, 유효기간, 라벨 용량, PK 표 값처럼 답의 형식이 정해져 있고 공식 출처가 있는 질문도 지금은 검색·생성 경로로 답한다. 그 결과는 다음과 같다.

- 손실이 가장 많은 곳이 허가 날짜·기간 타임라인이다([07_EVALUATION](../current/07_EVALUATION.md) §2.3).
- 같은 질문을 두 번 돌렸을 때 같은 정답 항목을 맞히는 비율이 0.891이다. 날짜·숫자 질문에서 답이 흔들리면 검토자가 결과를 믿지 않는다.
- 공식 목록에 필드로 있는 값도 문서 검색을 거치므로, 검색·순위·생성 중 어느 한 곳만 실패해도 답이 틀린다.

원칙은 하나다. **답의 형식이 정해져 있고 출처가 분명하면 검색·생성 대신 조회한다.** LLM은 질문 해석과 설명 문장에만 쓴다.

기대 효과:

| 항목 | 효과 |
| :-- | :-- |
| 정확도 | 값마다 정답 대조 테스트가 가능하다 |
| 재현성 | 같은 질문, 같은 스냅샷이면 같은 답이다 |
| 속도·비용 | 검색·리랭커·생성 호출이 빠진다 |
| 평가 | 조회 경로와 RAG 경로의 성능을 따로 보고한다 |
| 모니터링 | 스냅샷 간 차이가 곧 변경 감지다(§5.4) |

## 2. 층 정의

| 층 | 답의 성질 | 예 | 출처 | 갱신 | 검증 |
| :-- | :-- | :-- | :-- | :-- | :-- |
| **L1 레지스트리 사실** | 공식 목록의 필드로 존재 | EU 판매허가일·MAH·허가 상태, FDA 라이선스 유형·최초 허가일·교체 가능 지정일, HC 제품 상태 | EMA medicines JSON, Purple Book, HC DPD | 스냅샷마다 | 스냅샷 대조 단위 테스트 |
| **L2 문서 사실** | 문서 안에만 있으나 형식이 고정됨 | EMA 신청일, BLA 접수일, NDS 접수일·NOC일, 유효기간, 보관 조건, 첨가제, 라벨 용량표, PK 표 값, PMR/PMC | EPAR, SmPC, FDA 승인 레터·심사 문서, HC SBD·제품 모노그래프 | 문서 버전이 바뀔 때 | 추출 시 원문 대조 + 검수 상태 |
| **계산** | L1·L2 값의 연산 | 접수부터 허가까지 기간, 제품 간 차이 | L1·L2 사실 ID | 질문 시점 | 코드 계산만 허용 |
| **L3 서술·판단** | 형식으로 고정할 수 없음 | 심사 경위, 정당화 논리, 지침 해석, 비교의 한계 | 문서 전반 | — | RAG + 인용 검사 |

L2에서도 규칙이나 LLM으로 값을 뽑지만 그것은 **릴리스를 만들 때 한 번**이다. 질문이 오면 조회만 한다.

### 2.1 L2에 넣을 기준

아래 네 가지를 모두 만족할 때만 L2 사실 유형으로 만든다. 하나라도 아니면 L3에 둔다.

1. 자주 묻는다(평가 문항·사용 기록에 반복해서 나온다).
2. 값의 형식이 정해져 있다(날짜, 기간, 수치+단위, 짧은 목록).
3. 어느 문서의 어느 절에서 나오는지 규칙으로 말할 수 있다(예: SmPC 6.3절).
4. 공개 뒤 잘 바뀌지 않거나, 바뀌면 문서 버전으로 구분된다.

## 3. 이미 있는 자산

| 자산 | 위치 | 이 설계에서의 역할 |
| :-- | :-- | :-- |
| 제품·허가 표 | `registry.products`, `registry.licences` (마이그레이션 001) | 제품 마스터의 기반 |
| 이름 표 | `registry.entity_names` (002): 이름 유형(INN·브랜드·개발코드), 관할, `licence_id`, 출처 스냅샷 | 질문의 제품명 해석 |
| 원본 스냅샷 | `registry.source_snapshots` (002) + `ingestion/sources.py`: URL·SHA-256·기준일·받은 시각 | L1의 원본. 모든 L1 값이 여기를 가리킨다 |
| 허가 상태 조회 | `ingestion/licence_status.py` + `registry.licence_status_snapshots` (추가만 허용) | **L1 조회의 원형.** EU·US·CA를 LLM 없이 조회한다 |
| 카탈로그 | `data/catalog/ema_authorised_biosimilars.jsonl`, `purple_full_selected.jsonl`, `products.jsonl` | L1 필드 확인용 사본 |
| 타임라인 규칙 | `rag/ra/ra_timeline.py`: EPAR·SmPC 9절·FDA 심사 표지·HC SBD에서 날짜 필드를 정규식으로 읽고, 기간은 코드로 계산 | **L2 규칙 추출의 원형.** 지금은 질문 시점에 청크에서 읽고, 서비스에서는 꺼져 있다(`--approval-timeline`) |
| 계산 유틸 | `rag/ra/ra_derived.py`: 날짜 해석, `date_difference` 등 | 계산 층 |
| 검수된 관측값 | `data/reviewed/observations.jsonl` (8행): 시험·평가변수·통계량·비교약·분석 범위·값·CI·n·근거 문서 버전·쪽·표·검수 상태 | **L2 레코드 스키마의 원형** |
| CT.gov 결과 | `corpus.studies`, `corpus.study_results`: `posted_results_not_regulator_reviewed` 표시 | 규제기관 검토 전 자료라는 상태 표시의 선례 |
| 계획기 의도·facet | `rag/ra/ra_models.py`: `TaskIntent.intent`(`fact_lookup`, `document_info` 등), `ANSWER_FACETS` | 라우팅을 붙일 자리 |

새로 만드는 것은 사실 테이블(§4.3~4.5), 날짜 유형 사전(§4.2), 라우터(§6)다. 나머지는 기존 자산을 옮기거나 일반화한다.

## 4. 데이터 모델

### 4.1 제품 마스터

질문의 제품명이 공식 목록의 행과 연결되지 않으면 L1이 동작하지 않는다. 기존 `registry.products`·`registry.licences`·`registry.entity_names`에 식별자 표 하나를 더한다.

```sql
CREATE TABLE registry.product_identifiers (
    product_id    text NOT NULL REFERENCES registry.products(product_id),
    id_type       text NOT NULL CHECK (id_type IN ('dev_code', 'ema_product_number', 'bla_number', 'din',
                                                  'nct_id', 'us_proper_name', 'mfds_item_seq')),
    value         text NOT NULL,
    jurisdiction  text CHECK (jurisdiction IN ('EU', 'US', 'CA', 'JP', 'KR')),
    snapshot_id   bigint REFERENCES registry.source_snapshots(snapshot_id),
    PRIMARY KEY (id_type, value)
);
```

예: SB17 → `ema_product_number` EMEA/H/C/006183, `bla_number` 761373·761425, `us_proper_name` ustekinumab-ttwe, `nct_id` NCT04772274·NCT04967508. 한 제품에 BLA가 둘 이상일 수 있다(Purple Book 카탈로그에서 Pyzchiva 761373·761425, Wezlana 761285·761331). 그래서 제품 → 식별자는 1:N이다.

### 4.2 날짜 유형 사전

"허가일"은 기관마다, 같은 기관 안에서도 뜻이 여러 개다. 질문의 "허가일"을 아래 코드 중 하나로 해석하고, 답에는 항상 날짜 유형 이름을 함께 표시한다.

| 코드 | 뜻 | 층 | 출처 필드 | 비고 |
| :-- | :-- | :-- | :-- | :-- |
| `eu_submission` | EMA 판매허가 신청 제출일 | L2 | EPAR "submitted on … an application for marketing authorisation" | EMA JSON의 `start_of_evaluation_date`는 ustekinumab 11개 제품 모두 비어 있다 |
| `eu_chmp_opinion` | CHMP 긍정 의견일 | L1 | `opinion_adopted_date` | |
| `eu_first_authorisation` | EU 최초 판매허가일 | L1 (+L2 교차 확인) | `marketing_authorisation_date` / SmPC 9절 "Date of first authorisation" | **EU 기본값** |
| `eu_latest_ec_decision` | 가장 최근 EC 결정일 | L1 | `european_commission_decision_date` | **허가일이 아니다.** 변경 결정도 포함한다 |
| `us_bla_received` | BLA 접수일 | L2 | 심사 문서 표지 "Received Date", 최초 승인 레터 | 재제출은 `us_bla_resubmission_received`로 따로 둔다 |
| `us_first_licensure` | 최초 허가일 | L1 (+L2 교차 확인) | Purple Book에서 `Submission Type = Original` 행의 `Approval Date` 최솟값 / 승인 패키지 Orig1 | **US 기본값** |
| `us_interchangeable_designation` | 교체 가능 지정일 | L1 | Purple Book `Inter. Approval Date` | 최초 허가일과 다를 수 있다 |
| `us_supplement_approval` | 보충 승인일 | L1 | Purple Book `Supplement` 행 | 함량·제형 추가 등 |
| `ca_nds_filed` | NDS 접수일 | L2 | HC SBD "Date Filed" | |
| `ca_noc` | NOC 허가일 | L2 (L1 후보) | HC SBD "Authorization Date" | HC NOC 데이터베이스는 아직 연결하지 않았다 |
| `kr_permit` | 식약처 품목허가일 | L1 후보 | 의약품안전나라·공공데이터 API(확인 필요) | `licence_status.py`에서 KR은 아직 미확인으로 보고한다 |

수집해 둔 데이터에서 이미 보이는 함정:

| 함정 | 실제 값 | 규칙 |
| :-- | :-- | :-- |
| EMA 필드 혼동 | Pyzchiva: 의견 2024-02-22, 판매허가 2024-04-19, `european_commission_decision_date` 2026-06-30 | EC 결정일 필드를 허가일로 쓰면 2년이 틀린다. 허가일은 `marketing_authorisation_date` |
| Purple Book 행 혼합 | Pyzchiva: 최초 2024-06-28, 보충 2024-12-20·2025-06-27, 교체 가능 지정 2025-04-30 | 최초 허가일 = Original 행의 최솟값. 교체 가능 지정일은 별도 유형 |
| 행마다 다른 라이선스 유형 | Wezlana: Original 행은 `351(k) Interchangeable`, Supplement 1 행은 `351(k) Biosimilar` | 제품 단위 값은 행 단위 규칙으로 집계하고, 원본 행을 근거로 함께 보인다 |
| 비어 있는 필드 | EMA `start_of_evaluation_date` 공란 | 공란은 "기록 없음"이다. L2로 넘긴다 |
| 한 제품, 여러 BLA | Pyzchiva BLA 761373·761425 | 최초 허가일은 제품의 모든 BLA를 합쳐 Original 행의 최솟값. BLA별 값도 근거로 보인다 |

### 4.3 L1 사실 테이블

```sql
CREATE SCHEMA IF NOT EXISTS facts;

CREATE TABLE facts.registry_facts (
    fact_id        bigserial PRIMARY KEY,
    product_id     text NOT NULL REFERENCES registry.products(product_id),
    jurisdiction   text NOT NULL,
    fact_type      text NOT NULL,          -- §4.2 날짜 코드, mah, licence_type, marketing_status, authorisation_status …
    value_text     text,
    value_date     date,
    source         text NOT NULL,          -- EMA medicines JSON | Purple Book | HC DPD
    source_record  jsonb NOT NULL,         -- 원본 행의 키(예: BLA·Product Number·Supplement)와 값
    rule           text NOT NULL,          -- 예: 'min(Approval Date) where Submission Type = Original'
    snapshot_id    bigint NOT NULL REFERENCES registry.source_snapshots(snapshot_id),
    built_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON facts.registry_facts (product_id, jurisdiction, fact_type, built_at DESC);
```

- 스냅샷마다 다시 만들고 지우지 않는다. 최신 값은 `built_at`으로 고른다.
- 한 제품·유형에 출처가 둘이면(예: EU 허가일의 L1과 L2) 둘 다 저장하고 §6.4 규칙으로 표시한다.

### 4.4 L2 사실 테이블

`data/reviewed/observations.jsonl`의 필드를 일반화한다.

```sql
CREATE TABLE facts.document_facts (
    fact_id        bigserial PRIMARY KEY,
    product_id     text NOT NULL REFERENCES registry.products(product_id),
    jurisdiction   text NOT NULL,
    fact_type      text NOT NULL,          -- §4.4.1
    subject        text,                   -- 시험 ID, 적응증, SmPC 절 등
    qualifiers     jsonb NOT NULL DEFAULT '{}',  -- 군, 분석 집단, 시점, 통계량, 체중 구간, 제형 …
    value_text     text NOT NULL,          -- 원문 표기 그대로
    value_num      numeric,
    value_date     date,
    unit           text,
    value_role     text NOT NULL CHECK (value_role IN ('actual', 'planned', 'proposed', 'applicant_statement',
                                                       'agency_conclusion', 'label_text')),
    document_id    text NOT NULL,
    version_sha256 text NOT NULL,
    pdf_page       integer,
    quote          text NOT NULL,          -- 원문에서 그대로 자른 구간
    quote_start    integer,
    quote_end      integer,
    table_ref      jsonb,                  -- 표 번호·행·열(표에서 나온 값일 때)
    method         text NOT NULL CHECK (method IN ('rule', 'llm', 'human')),
    method_ref     text NOT NULL,          -- 규칙 ID 또는 프롬프트·모델 버전
    review_status  text NOT NULL CHECK (review_status IN ('unreviewed', 'machine_checked', 'human_verified', 'rejected')),
    reviewer       text,
    reviewed_at    timestamptz,
    review_note    text,
    release_id     text NOT NULL
);
CREATE INDEX ON facts.document_facts (product_id, jurisdiction, fact_type);
```

`value_role`이 핵심이다. 계획 용량과 실제 용량, 신청자 설명과 기관 결론을 같은 칸에 섞지 않는다. 이 구분은 이 프로젝트가 정의한 오류 유형(계획/실제 혼동, 귀속 오류)을 데이터 단계에서 막는다.

#### 4.4.1 첫 사실 유형

| `fact_type` | 출처·위치 | `qualifiers` | 추출 방식 |
| :-- | :-- | :-- | :-- |
| `eu_submission`, `us_bla_received`, `us_bla_resubmission_received`, `ca_nds_filed`, `ca_noc` | §4.2 | — | 규칙(`ra_timeline.RULES` 이전) |
| `shelf_life` | SmPC 6.3, US PI 16절, HC 모노그래프 보관 절 | 제형, 개봉 전/후, 조건 | 규칙 + 표 |
| `storage_conditions` | SmPC 6.4, PI 16절 | 제형 | 규칙 |
| `excipients` | SmPC 6.1, PI 11절 | 제형 | 규칙(목록) |
| `dosing_regimen` | 라벨 용법·용량 절과 표 | 적응증, 대상(성인/소아), 체중 구간, 유도/유지, 경로 | 표 추출 + 검수 |
| `pk_parameter` | EPAR·FDA 임상약리 표 | 시험, 군, 파라미터, 통계량(산술평균/중앙값/기하평균), 분석 집단 | 표 추출 + 검수 |
| `pk_equivalence` | 같은 곳 | 시험, 비교 쌍, 파라미터, CI 수준 | 기존 `observations.jsonl` 이전 |
| `study_design` | EPAR 임상 절, CT.gov | 시험, 항목(대상군·용량·경로·비교약·추적 기간) | LLM 추출 + 원문 대조 + 검수 |
| `pmr_pmc` | FDA 승인 레터 | 요구사항 번호, 종류, 기한 | 규칙 + LLM |

### 4.5 계산 사실

계산은 저장하지 않고 질문 시점에 만든다. 결과에는 입력 사실 ID와 방법을 붙인다(아래 값은 형식 예시).

```json
{"kind": "derived", "operation": "date_difference",
 "inputs": [{"fact_id": 1021, "fact_type": "ca_nds_filed"}, {"fact_id": 1022, "fact_type": "ca_noc"}],
 "result": {"days": 352, "text": "11개월 17일"}, "generated_by_model": false}
```

`ra_derived.py`의 `date_difference`를 그대로 쓴다. 정의 문구(예: "심사 중단 기간을 구분하지 않은 달력 차이")는 `ra_timeline.answer`의 `definitions`를 옮겨 쓴다.

## 5. 구축

### 5.1 L1 동기화

1. `sources.fetch`로 스냅샷을 받는다(이미 있다). 주기 제안: EMA JSON 매일, Purple Book 매월(원본이 월 단위), DPD 매주.
2. 파서가 제품 마스터의 식별자로 행을 골라 `facts.registry_facts`를 만든다. 규칙(`rule`)은 코드 상수로 두고 테스트한다.
3. 직전 스냅샷의 사실과 비교해 §5.4 변경 이벤트를 만든다.

### 5.2 L2 추출

릴리스(`rag.releases`)를 만들 때 사실 유형별 추출기를 돌린다.

1. **규칙 추출을 먼저 한다.** `ra_timeline.RULES`를 `facts/extractors/timeline.py` 같은 독립 모듈로 옮긴다. 질문 시점이 아니라 릴리스 시점에 모든 문서를 대상으로 돌린다. SmPC·PI 절 번호(6.1, 6.3, 6.4, PI 11·16절)는 절 제목 규칙으로 찾는다.
2. **LLM 추출은 규칙이 실패한 곳에만 쓴다.** 출력은 JSON 스키마로 고정하고, 값마다 원문 인용(`quote`)을 필수로 한다. 인용은 원문에 같은 위치로 그대로 있어야 한다(`ra_timeline.answer`의 `cite()` 검사와 같은 방식).
3. **기계 검사를 한다.** 통과하면 `machine_checked`가 된다.
   - 인용 구간이 원문에 그대로 있는가
   - 값이 해석되는가(날짜·숫자·단위)
   - 단위를 정규화했는가(`kg/m²` → `kg/m2` 등, 기존 `ra_quantities` 규칙)
   - 순서가 맞는가(허가일 ≥ 접수일)
   - 출처끼리 일치하는가(EMA JSON 판매허가일 = SmPC 9절 최초 허가일, Purple Book 최초 허가일 = 승인 패키지 Orig1 날짜)
4. **검수 대기열을 만든다.** 기계 검사를 통과하지 못한 값, 그리고 `dosing_regimen`·`pk_parameter`·`study_design`은 사람이 검수한다. 검수 화면은 기존 근거 원문 패널(`viewer.js`, `evidence-view.js`)을 재사용한다: 값 옆에 원문 쪽 이미지와 강조 영역을 띄우고 승인/거절/수정을 기록한다.

### 5.3 버전

- L2 사실은 `version_sha256`과 `release_id`에 묶인다. 문서가 새 버전으로 바뀌면 그 문서의 사실만 다시 추출하고 이전 값과 비교한다.
- 이전 사실은 지우지 않는다. "2026-03 기준 유효기간"처럼 기준일 질의가 가능해야 한다(내부 문서 묶음의 기준일 설계와 같은 원칙).

### 5.4 변경 이벤트

```sql
CREATE TABLE facts.change_events (
    event_id      bigserial PRIMARY KEY,
    detected_at   timestamptz NOT NULL DEFAULT now(),
    product_id    text NOT NULL,
    jurisdiction  text NOT NULL,
    fact_type     text NOT NULL,
    before        jsonb,
    after         jsonb,
    snapshot_from bigint REFERENCES registry.source_snapshots(snapshot_id),
    snapshot_to   bigint REFERENCES registry.source_snapshots(snapshot_id)
);
```

예: 새 교체 가능 지정, 허가 상태 변경, 새 보충 승인, 적응증 문구 변경(EMA `therapeutic_indication`), `latest_procedure_affecting_product_information` 변경. 이 표를 화면에 보여 주면 별도 개발 없이 "변경 모니터링" 기능이 된다.

## 6. 질문 처리

### 6.1 라우팅

계획기는 이미 작업마다 의도(`fact_lookup` 등)와 요청 사실(facet)을 낸다. 여기에 **facet마다 경로**를 하나 더 정한다.

| 경로 | 조건 | 처리 |
| :-- | :-- | :-- |
| `registry` | facet이 L1 사실 유형에 대응 | `facts.registry_facts` 조회 |
| `document_fact` | facet이 L2 사실 유형에 대응 | `facts.document_facts` 조회 |
| `derived` | 기간·차이 요청 | 입력 사실 조회 후 코드 계산 |
| `rag` | 나머지 | 기존 RAG 경로 |

구현 방법:

1. 계획기 출력 스키마에 `fact_type`(닫힌 목록, 해당 없으면 `null`)을 추가한다. 목록은 §4.2·§4.4.1의 코드다. 문장을 자유롭게 쓰게 하지 않고 고르게 한다.
2. 코드가 `fact_type` → 경로를 결정한다. LLM이 경로를 정하지 않는다.
3. 조회 결과가 비어 있으면(아직 추출하지 않았거나 검수에서 거절) 해당 facet만 `rag`로 넘기고 답에 "구조화 사실 없음 → 문서 검색"이라고 표시한다.
4. 제품·기관 범위는 기존 계획(제품 × 기관)을 그대로 쓴다.

### 6.2 섞인 질문

한 질문에 여러 경로가 섞일 수 있다. 예: G1 "FDA가 Yesintek을 교체 가능으로 승인한 시점과 그 경위" → 시점은 `registry`(`us_interchangeable_designation`), 경위는 `rag`. 답은 **표(조회·계산) + 서술(RAG)** 두 부분으로 만들고, 각 부분에 출처를 따로 붙인다.

### 6.3 표시

- 조회·계산 결과는 **표 템플릿으로 코드가 그린다.** LLM이 다시 쓰지 않는다.
- LLM은 표를 읽기 전용 문맥으로 받아 설명 문장만 쓴다. 설명에 표·인용에 없는 숫자·날짜가 나오면 기존 숫자 검사(`ra_quantities`)로 걸러낸다.
- 칸마다 보여 줄 것: 값, 날짜 유형 이름, 출처(목록 이름 또는 문서·쪽), 기준일(스냅샷 날짜), 검수 상태(`human_verified`가 아니면 "검수 전" 표시).

### 6.4 충돌과 부재

| 상황 | 표시 |
| :-- | :-- |
| 출처끼리 값이 다름 | 하나를 고르지 않고 둘 다, 각자의 정의·출처와 함께 |
| 같은 문서에 같은 필드가 여러 값 | `ra_timeline`처럼 규칙상 우선값을 쓰고 다른 값을 함께 표시 |
| 값이 없음 | "기록 없음"(출처에 없음)과 "해당 없음"(예: 교체 가능 지정이 없는 제품)을 구분 |
| 범위 밖 | 해당 제품·기관 문서가 범위에 없으면 그렇게 말함(기존 `ra_timeline` 문구 재사용) |

### 6.5 모호한 질문의 기본값

"허가일"만 있고 날짜 유형이 없으면 기관별 기본값을 쓰고 정의를 적는다: EU = `eu_first_authorisation`, US = `us_first_licensure`, CA = `ca_noc`. 다른 유형(의견일, 교체 가능 지정일)도 있으면 한 줄로 함께 알린다.

## 7. 평가 문항에 적용하면

### 7.1 답변 29문항

| 문항 | 내용 | 경로 |
| :-- | :-- | :-- |
| Q2 | EU 최초 판매허가일·MAH | L1 |
| Q4 | FDA BLA 접수일·최초 승인일 | L2(접수일) + L1(최초 허가일) |
| Q5 | 캐나다 NDS 접수일·NOC일·기간 | L2 + 계산 |
| Q7 | EMA 신청일·EU 허가일·기간 | L2 + L1 + 계산 |
| Q1 | 전체 기관 허가 타임라인·기간 | L1 + L2 + 계산 |
| Q3, Q6 | 유효기간, MAH | L2(`shelf_life`) + L1 |
| G4 | FDA 승인일·EU 허가일·교체 가능 지위 | L1 |
| G1 | 교체 가능 승인 시점과 경위 | L1(시점) + L3(경위) |
| C01, C03, T05, U01, U02 | 라벨 용법·용량 | L2(`dosing_regimen`) |
| T01~T04 | PK 표 통계량 | L2(`pk_parameter`) |
| E01, E02 | 시험 용량·경로, 층화 조건 | L2(`study_design`) |
| N01 | 거짓 전제 확인 | L2 값과 전제 비교 + L3 설명 |
| E03 | 두 시험 비교 | L2(설계 사실) + L3(비교 서술) |
| C02, C04, E04, U03, U04, G2, G3 | 대리 비교약 조건, 지침 해석, PREA, 지침 상태, 교체 가능성 근거 | L3 |

29문항 중 **22문항이 전부 또는 일부를 L1·L2로 답할 수 있다.** 순수 L3는 7문항이다.

### 7.2 검색 골든셋 100문항의 묶음

| 묶음 | 경로 |
| :-- | :-- |
| `eu_first_authorisation`, `fda_approval_date`, `mah` | L1 |
| `ema_submission`, `hc_noc_date` | L2 |
| `shelf_life`, `excipients`, `storage` | L2 |
| `crohn_iv_induction` | L2(`dosing_regimen`) |
| `fda_postmarketing` | L2(`pmr_pmc`) |
| `CHEMR_*` (23) | 대부분 L3 |

L1·L2로 옮긴 문항은 검색 골든셋에서 빼고 따로 채점한다. 남은 골든셋이 RAG가 실제로 풀어야 할 문항이 된다.

## 8. 평가

| 대상 | 지표 | 방법 |
| :-- | :-- | :-- |
| L1 | 정확도(정확 일치), 채움률(제품 × 사실 유형 중 값이 있는 비율) | 사람이 확인한 기대값과 단위 테스트. 목표 100% |
| L2 추출 | 정밀도·재현율, 기계 검사 통과율, 출처 간 일치율 | 사실 유형별 수작업 라벨 표본 |
| L2 검수 | 검수 대기 건수, 거절 비율, 건당 검수 시간 | 검수 기록 |
| 라우팅 | 경로 선택 정확도 | 문항마다 정답 경로를 라벨링(§7 표) |
| 종단 | 29문항 점수, 재실행 일치율 | 라우팅 켬/끔 비교. L1·L2 부분은 일치율 1.0이어야 한다 |

보고할 때는 경로별로 나눈다. "전체 점수"만 보고하지 않는다.

## 9. 구현 순서

| 단계 | 내용 | 완료 기준 |
| :-- | :-- | :-- |
| 1 | 날짜 유형 사전, `facts.registry_facts`(EMA JSON·Purple Book), 계획기 `fact_type`, `registry` 경로 | Q2·G4와 골든셋 `eu_first_authorisation`·`fda_approval_date`·`mah`가 L1로 답하고 단위 테스트 통과. 두 번 실행해 같은 답 |
| 2 | `facts.document_facts`, `ra_timeline` 규칙을 릴리스 시점 추출기로 이전, `shelf_life`·`excipients`·`storage` 규칙 추출, 계산 경로 | Q1·Q3~Q7 재평가. 타임라인 손실이 줄었는지 손실 분해(`eval/loss_attribution.py`)로 확인 |
| 3 | 검수 대기열·화면, `dosing_regimen`·`pk_parameter`(기존 `observations.jsonl` 이전) | C01·C03·T01~T05·U01·U02를 검수된 값으로 답함. "검수 전" 표시 동작 |
| 4 | `facts.change_events`와 화면 | 스냅샷 두 개 사이의 실제 변경(예: 교체 가능 지정)이 이벤트로 잡힘 |
| 5 | HC NOC DB, 식약처 허가 정보 연결 | `ca_noc`·`kr_permit`이 L1으로 채워짐 |

1~2단계만으로도 손실 1위(타임라인)와 재실행 흔들림을 직접 다룬다.

## 10. 정해야 할 것

| 항목 | 선택지 | 제안 |
| :-- | :-- | :-- |
| 검수 전 L2 값을 답에 쓸지 | 쓰지 않음 / "검수 전" 표시하고 씀 | 표시하고 씀. 단 `dosing_regimen`·`pk_parameter`는 검수 후에만 |
| 사실이 없을 때 | 답하지 않음 / RAG로 넘김 | RAG로 넘기고 경로를 표시 |
| 기관별 "허가일" 기본값 | §6.5 | §6.5대로 하되 화면에 정의를 항상 표시 |
| 검수 담당 | 개발자 / 도메인 검토자 | 개발자 1차, 의미 판단이 필요한 항목은 도메인 검토자 |
| 저장 위치 | PostgreSQL `facts` 스키마 / 파일 | PostgreSQL. 기존 `registry`·`rag` 스키마와 같은 DB(`biosim_ra`) |

## 11. 하지 않는 것

- 심사 경위, 정당화 논리, 지침 해석을 사실 테이블에 넣지 않는다. 이것들은 형식이 고정되지 않아 L3에 둔다. 다만 판단 단위 선례(쟁점 → 신청자 정당화 → 기관 결론)를 구조화하는 일은 별도 설계 대상이다.
- LLM이 계산한 숫자, 날짜 형식을 바꾼 값을 저장하지 않는다.
- 공식 목록 값과 문서 값이 다를 때 한쪽을 조용히 고르지 않는다.
