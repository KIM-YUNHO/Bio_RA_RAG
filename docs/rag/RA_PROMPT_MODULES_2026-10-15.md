# RA 생성 프롬프트 모듈 구성 (2026-10-15)

생성 단계의 시스템 프롬프트는 `rag/ra_prompt.py`에서 조립한다. 공통 핵심(CORE) 뒤에 계획기(LLM)가 읽은 질문 성격과 근거 사실로 고른 모듈을 붙이고, 질문에 가장 특화된 지시를 맨 끝에 둔다. 기존의 단일 프롬프트(`rag/ra_answer.py`의 `SYSTEM`과 요구 항목용 문구)는 `RA_PROMPT_VARIANT=current`로만 쓰는 되돌리기용이다.

## 두 경로

| 경로 | 언제 | 조립 |
|---|---|---|
| 요청 항목 경로 (기본, 모든 질문) | 항상 | CORE + 모듈 + (계획기 facet이 있으면) CHECKLIST + REQUESTS + (계산 요청 시) DERIVED + (칸 예산 > 8) ENUMERATION |
| 요구 항목 경로 (되돌리기용) | `RA_REQUIREMENT_MODE=slots`일 때만. facet × 제품 × 항목마다 claim 1개 칸 | CORE + 모듈 + REQUIREMENT |

계획기가 읽은 facet과 항목(군, 구간, 시점)은 `answer_checklist`로 생성에 전달한다. 빠뜨리지 말아야 할 목록일 뿐 답변 칸이 아니다. claim 수는 기존 상한 안에서 자유이고, 목록에 없는 내용도 원 질문을 보고 답한다. 칸 방식은 칸이 모자라거나(여러 군) 계획기가 facet을 빠뜨리면 답할 자리가 없어서 기본값에서 내렸다(2026-10-15 측정: 요구 항목 문항 16개 × 2회, 칸 128점, 체크리스트 132점, 이전 정규식 137점).

## 모듈과 선택 조건

| 모듈 | 내용 | 선택 조건 (`ra_prompt.modules`) |
|---|---|---|
| CORE | 원문만 사용, 인용 ID 복사, 증거 먼저, 수치·날짜·일정 표기를 원문 그대로, 원문에 없는 표·문서 번호 금지, 계산값 금지, claim 역할 선언, 요청 항목만, 문서 전체의 없음 단정 금지 | 항상 |
| PROTOCOL | ClinicalTrials.gov 프로토콜/SAP는 계획으로 기술 | 근거에 protocol/sap 문서가 있을 때 |
| GUIDELINE | QTPP, 분석·임상 브리징, 선례로 일반 요건 추론 금지, 지침별 채택·시행일과 범위 | 근거에 지침이 있거나, 계획에 지침 문서 역할 또는 reference_selection·analytical_bridge·clinical_bridge·document_status_scope가 있을 때 |
| STUDY | 용량·경로·시험 식별, 선정 기준 대 실측, 실제 대 제안 용량 | 계획에 시험·용량 관련 facet(dose_route, comparator, design, population, follow_up, eligibility, dosing_schedule, stratification, purpose, assay_targets) 또는 주제 pk_study·design이 있을 때 |
| COMPARISON | 제품별 사실 먼저, 차이는 양쪽 인용으로 | 비교 의도이거나 열거형이 아닌 다제품 질문일 때 |
| TABLE | 군별 값·행·열·단위, 평균·SD·중앙값·기하평균 구분 | 근거에 표가 있고 계획에 table_statistics·statistic_identity가 있을 때 |
| 작업 의도 | `ra_intent.GUIDANCE` | 계획의 작업 의도마다 |
| REQUESTS / REQUIREMENT | 응답 형식 (경로별) | 경로에 따라 |
| DERIVED | 계산은 파생값 칸으로 선언, 원문 표기 그대로 피연산자 값 기재 | 계획에 calculation facet이 있을 때 |
| ENUMERATION | 모든 제품×기관 조합에 대해 사실 또는 gap | 칸 예산 > 8 (제품×기관 수) |
| CHECKLIST | answer_checklist의 각 항목을 claim 또는 gap으로 다룸. 목록은 상한이 아니라 상기용 | 계획기 facet에서 만든 체크리스트가 있을 때 |

## 질문 성격은 계획기가 읽는다 (2026-10-15 추가)

질문의 의미 분류는 정규식이 아니라 계획기(LLM)가 닫힌 선택지로 출력한다. 작업마다:

- `answer_facets`: 요청한 사실의 종류(`ra_models.ANSWER_FACETS`). detail은 표 통계의 endpoint와 Part 번호에만 쓴다.
- `topics`: 묻는 임상시험 내용(`ra_models.TOPICS`). 고정 주제 질의(`ra_topic_queries`)에 쓴다.
- 프로토콜/SAP 문서를 읽을지는 계획기가 고른 문서 역할(source_roles)로 정한다.

코드는 고유명사만 확인한다. 제품명은 레지스트리, endpoint·Part 번호·항목은 질문 원문에 실제로 있는지 본다. 기관은 계획기가 enum(EU/US/CA/sponsor)과 질문 속 단어(`name`)를 함께 주고, pydantic 검증이 그 단어가 질문에 있는지만 확인한다(기관 이름 목록 없음). 인용 형식이 원문과 달라도 역할이나 기관을 지우지 않는다. 후속 질문은 이전 계획의 범위를 계획기에 후보로 주고, 새 기관은 사용자가 쓴 단어가 있을 때만 인정한다. 완료 판정은 계획기 facet 기준이며, 값으로 대조할 수 있는 facet(dose_route, dosing_schedule, table_statistics)만 완료를 닫는다. 질문에 적힌 숫자·경로는 그대로 필수 조건이다. 이 필드가 없는 예전 저장 계획은 기존 정규식 경로(`ra_obligations.legacy`, 패턴 기반 모듈 선택)로 재생한다.

## 규칙을 추가하거나 바꿀 때

1. 어떤 실패에서 나온 규칙인지, 같은 일을 하는 코드 검사(`ra_quantities`, `ra_source_constraints`, `ra_review` 등)가 있는지 먼저 확인한다. 코드 검사와 충돌하는 지시는 넣지 않는다.
2. 모든 질문에 필요한 원칙이면 CORE, 특정 유형에만 필요하면 해당 모듈에 넣는다. 새 유형이면 모듈과 선택 조건을 추가한다.
3. 선택 조건은 모듈을 **켜는** 데만 쓴다. 근거나 범위를 배제하는 데 쓰지 않는다. 새 질문 유형은 정규식 대신 계획기 선택지(facet·topic)에 추가하고, 계획기 프롬프트(`ra_tasks.FACETS_PROMPT`)에 정의를 쓴다.
4. 검증: 계획·근거를 고정한 짝지은 비교(`eval/prompt_variants.py`, `eval/paired_dev.py`)로 개발 19문항과 열거형 Q1~Q7을 돌린다. 생성 편차가 있으므로 2회 이상 반복한다.

## 채택 근거 (2026-10-15)

- 생성만 비교(25문항 × 2회, 같은 근거): 현재와 같은 수준. Q1만 소폭 높음(편차 범위 안).
- 검토 포함 비교(25문항, 같은 계획·근거): 실행당 기대값 4.93 → 5.24, 나빠진 문항 없음. 검토 탈락 실행당 1.70 → 1.48. 숫자·수량 검사 탈락이 크게 줄어듦("원문 표기 그대로" 규칙의 효과로 판단).
- 시스템 프롬프트 길이: 질문 유형에 따라 필요한 모듈만 붙어 기존(약 1,200토큰, 72줄)보다 짧다.
- 기록: `data/runs/prompt_rework_2026-10-15/` (rule_inventory.md, stage1, stage2_modular).

## 되돌리기

`RA_PROMPT_VARIANT=current`로 서비스를 시작하면 이전 단일 프롬프트를 쓴다. 한 릴리스 동안 문제가 없으면 이전 프롬프트 코드는 지운다.
