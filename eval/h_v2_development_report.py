"""Render source-based development assessments; never grade from UI status."""
import argparse
from collections import Counter
from pathlib import Path
import re

from eval.h_v2_development_eval import read, save, digest

ROOT=Path(__file__).resolve().parents[1]
LABEL={'correct':'완전 충족','partial':'부분 충족','no_answer':'핵심값 미응답',
       'incorrect':'오답','safe_control':'통제 통과'}


def adjudicate(run):
    notes=read(ROOT/'eval/h_v2_final_review_notes_2026-10-14.json')
    paired={r['case']:r for r in read(run/'paired/adjudication.json')['rows']}
    for phase in ('table_focus','transfer_utf8','saved_replay_4_9_3'):
        rows=[]
        for note in notes[phase]:
            sub=note.get('phase','v2')
            path=run/phase/sub/(note['case']+'.json')
            report=read(path)
            row={**note,'report':str(path.relative_to(ROOT)), 'report_sha256':digest(path),
                 'runtime_status':report['results'][0]['status']}
            if phase=='table_focus': row['before']=paired[note['case']]['v2_verdict']
            rows.append(row)
        save(run/phase/'adjudication.json',{'reviewer':notes['reviewer'],'independent_holdout':False,
            'pipeline_version':read(run/phase/'summary.json').get('pipeline_version',
                'ra-research-rag-4.9.2'),'fresh_generation':phase!='saved_replay_4_9_3',
            'counts':dict(Counter(r['verdict'] for r in rows)),'rows':rows})


def render(run):
    adjudicate(run)
    paired=read(run/'paired/adjudication.json');summary=read(run/'paired/summary.json')
    build=read(run/'native_candidate_v2/final_candidate_audit.json')
    chunks=read(run/'native_candidate_v2/candidate_build.json')['embedding_reuse']
    rel='../../'+run.relative_to(ROOT).as_posix()
    text=f'''# H v2 색인 RAG 개발 평가 및 개선 — 2026-10-14

## 1. 핵심 결과

**코드와 GPT-5.6 Luna를 고정한 개발 평가에서 기존 색인의 완전 충족은 8/17(47.1%), H v2는 11/17(64.7%)였다.** 차이는 +3문항, +17.6%p다. 기존 개발 질문과 이미 검토한 원문을 사용한 구현 에이전트의 원문 대조이며, 독립 평가나 RA 전문가 인증 점수가 아니다.

| 조건 | 기존 색인 | H v2 |
| :-- | --: | --: |
| 제품 문서 | 92 | 92 |
| 제품 벡터 | 10,088 | 11,536 |
| 완전 충족 | 8/17 | 11/17 |
| 부분 충족 | 5/17 | 3/17 |
| 요청 핵심값 미응답 | 4/17 | 3/17 |
| 최종 답변의 명백한 오답 | 0/17 | 0/17 |
| 잘못된 전제·없는 범위 통제 | 2/2 | 2/2 |

이후 별도 후보와 코드 개선을 검증했다. 전체 17문항을 최종 코드로 다시 생성하지 않았으므로, **최종 코드의 전체 정확도가 64.7%보다 높아졌다는 종합 점수는 제시하지 않는다.** 아래 제한 평가와 저장 응답 재처리를 구분한다.

§4.3의 통계표 검색 보정은 개발 질문의 문장 형태와 표 구조를 좁게 가정해, 색인 전체에서 개발 질문이 겨냥한 표 2개에서만 작동했다. project_shared의 코드 4.9.4에서 이를 일반화하고 모델 호출 없이 검색 단계를 다시 확인했다. 이어서 다른 제품·기관의 새 통계표 질문 9개를 생성했다(Luna 23회). 원문에 값이 있는 4개는 완전 충족 2·부분 충족 1·핵심값 미응답 1이었고, 요청 통계량이 원문에 없는 5개는 모두 값을 지어내지 않았다. 최종 오답은 0/9다(§5.5).

## 2. 고정 조건과 채점 기준

- 1차 비교 코드: `ra-research-rag-4.9.0`.
- 계획·초안·의미 검토 모델: 모두 **`gpt-5.6-luna`**, reasoning `none`. 사용자의 모델 지정에 따른다.
- 기존 색인: `data/versions/01_pilot_baseline_2026-10-10/embedded/rag_finalization_2026-10-10_v2`.
- 새 색인: `data/versions/02_pipeline_h_2026-10-14/embedded/release`.
- 공통 지침: `data/guidelines/ra_guidelines_native_2026-10-12_v2`; 양쪽에 동일 적용.
- 기존 개발 질문 17개 + 통제 2개 × 두 색인 = 38개 보고서. 같은 제품·기관 범위와 모델 설정을 사용했다.
- 모델·메시지·스키마·출력 예산·추론 설정까지 완전히 동일한 요청만 캐시를 재사용했다. 1차 비교의 새 호출은 {summary['new_provider_calls']}회, 단계별 캐시 재사용은 {sum(r.get('cache_calls',0) for r in summary['rows'])}회다.
- 정답·기대값·기존 채점은 생성 입력에 넣지 않았다. 별도 평가 LLM, 자동 재시도, 가용성 확인용 호출은 사용하지 않았다.
- 양 색인과 1차 코드 해시가 평가 동안 바뀌지 않았음을 확인했다. 각 후속 단계도 자체 코드·색인 스냅샷과 해시를 남겼다.

완전 충족은 고정된 모든 요구 항목의 값·단위·조건·출처가 맞는 경우다. 일부만 맞으면 부분 충족, 요청한 핵심 사실 없이 배경만 제시하면 핵심값 미응답이다. 보류·제거된 초안은 최종 오답으로 세지 않는다. 오답 0건은 이 작은 개발 표본에서의 관측이다.

C02는 기존 `required_facets`에 있던 대상자 수 238명까지 요구하는 보수적 기준을 유지했다. 두 응답 모두 건강한 성인 남녀를 설명했지만 인원수는 빠졌다. 프로그램의 `answered/partial/not_found`는 이 원문 채점과 별개이며, 1차 비교에서 `answered`는 양쪽 모두 1건뿐이었다.

문항별 한 번의 생성 결과를 비교했으므로 모델 변동이 포함된다. 모든 차이를 임베딩 수 증가나 한 가지 전처리 변경의 인과 효과로 단정할 수 없다. 모델 자체나 임베딩 모델·차원·정규화 방식을 바꿔 비교한 실험도 아니다.

## 3. 문항별 원문 판정

| 문항 | 기존 색인 | H v2 | 판정 이유 |
| :-- | :-- | :-- | :-- |
'''
    for r in paired['rows']:
        text+=f"| {r['case']} | {LABEL[r['baseline_verdict']]} | {LABEL[r['v2_verdict']]} | {r.get('reason_ko',r['reason'])} |\n"
    text+=f'''
최종 인용의 문자 범위와 해당 주장이 실제 검토받은 자체 출처 경계를 확인했다. 최종 주장에 대한 기존 수치·표 귀속 검사의 오류도 0건이었다. 이는 완전 충족 채점을 대체하지 않는다.

기록: [38개 실행 요약]({rel}/paired/summary.json), [원문 판정·보고서 해시]({rel}/paired/adjudication.json), [주장·인용·원문 검토 패킷]({rel}/paired/review_packets.json), [고정 프로토콜]({rel}/paired/protocol.json).

## 4. 병목과 구현한 개선

### 4.1 연속 표에서 닫힌 행만 별도 후보에 회수

SB17 통계표 본문이 추출됐어도 연속 표 검토 상태 때문에 검색 청크에는 캡션만 남는 문제가 있었다. 기존 native 표 검증기로 **연속 표 후보 96개 전부**를 검사해, 원본 테두리·native 문자 커버리지·헤더가 검증되는 **2개 단위**에서만 완결 행을 회수했다. 닫히지 않은 마지막 경계 행은 제외했고, 페이지 간 의미 연결과 수치 근거 자동 승인도 허용하지 않았다. 94개는 계속 보류한다.

질문·문서·페이지 ID나 정답 숫자를 기준으로 실행을 분기하지 않았다. 기존 물리적 검증 계약을 재사용했으며 OCR·VLM 추가 호출은 없었다.

- 원문 70,264개 단위 중 변경은 2개. 나머지 모든 필드와 전체 `source_refs`를 보존했다.
- 별도 후보: {build['documents']}문서, {build['chunks']:,}벡터. 캡션 청크를 표 본문 청크로 교체해 총 벡터 수는 같다.
- 기존 임베딩 {chunks['reused_vectors']:,}개 재사용, 변경 청크 {chunks['new_vectors']}개만 계산했다.
- HTML DOM과 H 추출 경로를 보존했다. 최종 후보의 parser 출처 라벨만 실제 생성 경로로 바로잡았으며 벡터와 청크 ID는 불변이다.
- 최초 로컬 구성 시 관련 없는 캡션 단위가 바뀌는 것을 감사에서 발견해 폐기했다. 수정한 후보만 모델 평가에 사용했다.

[전체 후보 회수 검사]({rel}/native_fragment_probe/summary.json), [임베딩 재사용·단위 감사]({rel}/native_candidate_v2/candidate_build.json), [최종 후보 무결성]({rel}/native_candidate_v2/final_candidate_audit.json).

### 4.2 지침 전용 질문에 제품 문서가 재유입되는 문제 수정

초기 문맥은 `reading_source_roles=['guideline']`를 지켰지만 부족한 항목의 보충 검색은 제품 승인서를 다시 추가했다. U04의 일반 지침 주장이 특정 제품에 귀속돼 최종 검사에서 제거되는 원인이었다. `rag/ra_research.py`의 보충 검색도 같은 읽기 범위를 따르게 수정했다. 지침과 제품을 함께 읽도록 명시한 질문의 기존 동작은 유지한다.

회귀 검사는 수정 전 제품 자료 유입을 재현했고 수정 후 통과했다. 코드 4.9.1의 제한 평가에서는 U04가 핵심값 미응답에서 완전 충족으로 바뀌었다. 이는 저장된 지침의 문서 상태에 대한 평가이며, 2026년 현재 외부 규제 상태를 새로 조사한 결과가 아니다.

### 4.3 요청한 통계량과 실제 표 헤더를 연결해 검색

표 본문을 회수해도 검색이 기하평균·비율 표를 앞세워 산술평균·SD를 놓쳤다. `rag/ra_statistical_tables.py`는 질문에서 요청한 endpoint와 Mean/SD/Median/Range를 읽고, 기존 표 바인딩이 확인하는 헤더 조합에 맞는 후보를 우선한다. 기존 hybrid 검색 결과와 검색 개수 제한은 유지한다.

제품·기관 범위를 이미 적용한 후보에서만 동작하며 다른 시험의 명시적 ID와 충돌하면 제외한다. ‘기하평균과 구분’이라는 비교 문구를 기하평균 요청으로 바꾸지 않는다. 비율·신뢰구간·ANCOVA·하위군·Part가 필요한 복잡한 질문은 기존 검색 경로에 맡긴다. 표 선택은 관련성 판단이며 값·군·통계량의 최종 출처 검사를 대신하지 않는다.

**일반화 한계(4.9.2~4.9.3).** 이 구현은 요청의 첫 문장만 읽고 '중앙값이나 기하평균과 구분' 같은 특정 문구를 지웠다. 지표는 Cmax·Tmax·t1/2·AUCinf·AUClast 5개로 고정했고, 헤더가 맞는 표를 동점으로 두고 문서 순서대로 골랐다. 색인 전체 표 후보 2,673개 중 산술평균·SD·중앙값 조건에 맞는 표는 개발 질문이 겨냥한 SB17 Table 8과 ABP654 Table 4뿐이었다. 수정 내용은 §5.5에 있다.

### 4.4 공통 단위의 숫자 쌍을 새 숫자로 오인하는 검증 수정

초안의 `17±3 ng/mL`를 검토기가 평균 `17 ng/mL`와 SD `3 ng/mL`로 나누면, 기존 단위 파서는 뒤의 3만 인식해 평균 17을 ‘초안에 없던 수치’로 제거했다. `1,234 및 567 ng·h/mL`도 같은 문제였다.

코드 **4.9.3**은 초안의 새 수치 추가 방지 검사에서만 명시적 숫자 쌍의 공통 단위를 인식한다. 임의 문장이나 다른 단위를 건너뛰며 단위를 전파하지 않는다. 원문 수치 검사·표 군/통계량 바인딩·자체 인용·의미 검토는 그대로 통과해야 한다. 원문 수치 파서 자체는 바꾸지 않았다.

## 5. 후속 제한 평가와 저장 응답 재처리

### 5.1 회수 후보 + 읽기 범위 수정, 코드 4.9.1

기존 질문 중 6개와 통제 2개를 선택했다. 양성 질문의 완전 충족은 **1/6 → 3/6**, 통제는 2/2 유지했다. 새 Luna 호출 6회이며 나머지는 완전히 같은 입력의 캐시를 재사용했다.

| 문항 | 원래 H v2 | 수정 후 | 이유 |
| :-- | :-- | :-- | :-- |
'''
    for r in read(run/'improved/adjudication.json')['rows']:
        text+=f"| {r['case']} | {LABEL[r['before']]} | {LABEL[r['after']]} | {r['reason']} |\n"
    text+=f'''
[제한 재평가 판정]({rel}/improved/adjudication.json), [실행 요약]({rel}/improved/summary.json).

### 5.2 통계 헤더 검색까지 수정, 코드 4.9.2

통계표 질문 4개를 다시 확인했다. 원래 H v2의 **2/4 완전 충족 → 3/4 완전 충족 + 1/4 부분 충족**이다. 새 Luna 호출 8회이며 동일 계획 단계 4개를 재사용했다.

| 문항 | 원래 H v2 | 수정 후 | 원문 판정 |
| :-- | :-- | :-- | :-- |
'''
    for r in read(run/'table_focus/adjudication.json')['rows']:
        text+=f"| {r['case']} | {LABEL[r['before']]} | {LABEL[r['verdict']]} | {r['reason']} |\n"
    text+=f'''
[통계표 판정]({rel}/table_focus/adjudication.json), [원문 검토 패킷]({rel}/table_focus/review_packets.json), [실행 요약]({rel}/table_focus/summary.json).

이 개선은 색인 전체에서 위 두 표에서만 일어날 수 있었다(§4.3, §5.5).

### 5.3 새로 고정한 질문 표현 4개, 코드 4.9.2

UTF-8 파일의 질문 4개에 새 Luna 호출 12회를 사용했다. **완전 충족 2/4, 부분 충족 2/4, 핵심값 미응답·최종 오답 각 0/4**다. 이미 노출된 문서의 새 질문 표현이며 독립 문서·미노출 정답 평가가 아니다.

| 문항 | 판정 | 원문 판정 |
| :-- | :-- | :-- |
'''
    for r in read(run/'transfer_utf8/adjudication.json')['rows']:
        text+=f"| {r['case']} | {LABEL[r['verdict']]} | {r['reason']} |\n"
    text+=f'''
[고정 UTF-8 질문](../../eval/h_v2_transfer_cases_2026-10-14.json), [판정]({rel}/transfer_utf8/adjudication.json), [원문 검토 패킷]({rel}/transfer_utf8/review_packets.json).

### 5.4 단위 검사 수정의 저장 응답 재처리, 코드 4.9.3

위 8개 보고서의 초안과 의미 검토를 그대로 재사용했다. 모델 입력 전체의 캐시 키가 일치해야만 실행하는 오프라인 클라이언트로 검사했으며, 네트워크 클라이언트나 새 모델 호출은 없다. 초안·선택 인용·검토 입력이 바뀌면 즉시 실패한다.

- 통계표 4개: T01의 평균값이 복구돼 **저장 응답 기준 4/4 완전 충족**이다. 다른 3개는 유지했다.
- 추가 질문 4개: **2/4 완전 충족 + 2/4 부분 충족**을 유지한다. G04의 세 군 평균·SD·단위는 복구됐지만, 요청한 기하평균과의 명시적 구분은 여전히 보류돼 완전 충족으로 올리지 않았다.
- G04에서 근거 불충분한 별도 기하평균 수치는 계속 차단했다. G02의 IV 유도 용량 누락도 남는다.

이는 결정적 검증 수정의 효과를 확인한 결과다. **4.9.3으로 새로 생성한 4/4 성능이나 전체 17문항 성능으로 해석하지 않는다.**

[저장 응답 재처리 요약]({rel}/saved_replay_4_9_3/summary.json), [원문 판정]({rel}/saved_replay_4_9_3/adjudication.json), [재처리 실행기](../../eval/h_v2_saved_response_replay.py).

### 5.5 통계표 검색 일반화 수정과 새 질문 평가, 코드 4.9.4

검토에서 통계표 검색 보정이 개발 질문의 문장 형태, 개발셋 PK 지표 5개, 문서 순서 선택에 의존한다는 지적이 나왔다. 재현해 보니 의미가 같은 바꿔 쓴 요청 11개 중 바르게 해석한 것은 0개였다. 8개는 인식하지 못했고, 3개는 잘못 해석했다. 예를 들어 '기하평균이 아닌 산술평균'은 기하평균 요청으로 뒤집혔다. 표 쪽에서도 PK 지표가 있는 표 59개 중 18개가 `C $ max $`·`AUC0-inf` 표기를, 18개가 통계량 역할·단위 판정을 통과하지 못했다.

project_shared의 `rag/ra_statistical_tables.py`, 호출부 `rag/ra_retrieval.py`, 버전 문자열(`rag/ra_pipeline.py`)만 수정했고, biosimilar_collection은 4.9.3 그대로다.

- **요청 해석:** 요청 전체를 문장 단위로 읽는다. 대조·제외 표지(`~과 구분`, `~이 아닌`, `~말고`, `~제외`, `~필요 없`, `not`, `rather than`, `instead of`, `distinguish … from`)가 가리키는 통계량만 뺀다. 나열된 지표는 뒤의 통계량을 함께 받는다. 통계량이 지표 앞에 오는 경우(`평균과 SD로 Cmax`, `mean and SD of Cmax`)와 다음 문장에 오는 경우도 연결한다. 질문 ID나 문구 목록은 쓰지 않는다.
- **지표 인식:** 공용 `contract_terms` 어휘(수식 표기, `최고혈중농도`, CL/F, V/F)에 검색 전용 별칭을 더해 질문과 표 헤더에 같이 쓴다. 추가한 별칭은 t½·반감기·half-life, AUC0-inf/∞, AUC0-t, AUCτ, Ctrough, 최고혈중농도 도달시간이다. 정상상태(`,ss`)와 용량 보정(`/D`) 지표는 별개로 둔다. `contract_terms`는 계약 매칭에도 쓰이므로 바꾸지 않았다.
- **헤더 서명:** 검색 관련도는 헤더가 정하는 통계량 역할만 보고 검증기의 단위 파서에는 기대지 않는다. Min/Max 행은 범위로 본다. 모형 보정 LS 기하평균은 기술통계 기하평균과 구분한다. 최종 수치·단위·군 검증은 바꾸지 않았다.
- **표 선택:** 범위 안의 표를 모두 비교한 뒤 고른다. 한 지표에 요청한 통계량을 모두 인쇄한 표만 그 지표를 덮는 것으로 본다. SD 한 행으로 평균·SD를 대신하지 않는다. 여러 표가 지표를 나눠 덮으면 함께 고른다. 질문과 다른 대상군·시점·분석집단·경로·용량을 인쇄한 표는 올리지 않는다. 요청하지 않은 하위군·보정·사후 분석 표는 뒤로 보낸다. 동점이면 해당 과업 질의의 dense 유사도로 정한다(`rag/ra_retrieval.py`).

검증은 저장된 플래너 출력과 같은 색인을 고정하고 검색 코드만 바꿔 실행했다. LLM 호출은 0회이며, 로컬 bge-m3로 질의만 임베딩했다.

| 측정 | 4.9.3 | 4.9.4 | 4.9.4, 모듈 끔 |
| :-- | :-- | :-- | :-- |
| 개발 통계표 질문 8건(T01~T04 × 저장 플래너 2종), 대상 표 k=5 포함 | 8/8, 전부 1위 | 8/8, 전부 1위 | 2/8 |
| 같은 8건, k=12 포함 | 8/8 | 8/8 | 8/8 (2~9위) |
| 같은 플래너 질의 + 요청 문장만 바꾼 9개, k=5 포함 | 3/9 | 9/9, 전부 1위 | 2/9 |
| 같은 9개, k=12 포함 | 9/9 | 9/9 | 9/9 |
| 저장 보고서 23건·검색 그룹 54개의 결과가 4.9.3과 동일 | — | 54/54 | 38/54 |
| 색인 전체 매칭 표(범위): Cmax 평균·SD / Tmax 중앙값·범위 / t1/2 평균·SD | 1(1) / 1(1) / 1(1) | 5(5) / 6(6) / 5(5) | — |

모듈의 효과는 k=5 문맥과 순위에 있다. 모듈을 끄더라도 k=12에서는 대상 표가 2~9위로 들어온다. 4.9.4에서 새로 걸린 Cmax 평균·SD 표 4개는 Health Canada 제품 모노그래프 Table 14다. 실제로 `Median (Range)`·`Mean (± SD)` 표이지만, 건선 환자 대상 오리지널 ustekinumab 자료(출처 C0379T04 CSR)이며 바이오시밀러 시험 자료가 아니다. 시험 귀속은 기존 출처·시험 검사가 판단한다. '건강인'처럼 다른 대상군을 지정한 요청에서는 이 표를 올리지 않는다.

한계:

- 바꿔 쓴 질문과 단위 테스트 변형은 구현 에이전트가 작성했다. 독립 질문이 아니다.
- 개발 17문항 전체는 4.9.4로 다시 생성하지 않았다. 새 질문 결과는 아래에 따로 적었다.
- 검증기 쪽 발견(미수정): `ra_table_citations.binding_quantities`가 `Geometric Least Square Mean` 헤더를 산술평균(`mean`)으로 분류한다. `ra_quantities`가 `hr*ng/mL`·`μg·h/mL`을 한 단위로 읽지 못해, 해당 표의 값은 검색돼도 수치 검증에서 막힐 수 있다. Min/Max 행도 검증기의 `range` 역할로 바인딩되지 않는다.
- 면역원성·유효성 표(n(%), LS 평균 변화량, 신뢰구간)는 이 모듈의 범위 밖이며 기존 검색 경로를 쓴다.

**새 통계표 질문 9개 생성, 코드 4.9.4.** S01~S09는 4.9.4 작동 결과를 보기 전에 [고정](../../eval/statistical_table_holdout_cases_2026-10-14.json)했다. 개발 질문과 다른 제품·기관·표현이지만, 구현 에이전트가 같은 승인 말뭉치에서 작성했으므로 독립 평가가 아니다. 새 Luna 호출은 23회(입력 72,998·출력 5,350 토큰, 115초)이고 상한은 27회였다. 실행 중 코드·색인 해시는 바뀌지 않았다. 실행 뒤에는 소스의 보이지 않는 문자(U+2044) 하나를 이스케이프로 바꾼 것 외에 변경이 없고, 바뀐 코드로 다시 돌린 검색 재현 결과는 이전과 완전히 같았다.

| 문항 | 판정 | 원문 판정 |
| :-- | :-- | :-- |
| S01 | 원문 부재·정확 처리 | FDA Table 8은 Cmax를 기하평균으로만 제시한다. 지어내지 않았지만 기하평균만 있다는 사실은 답하지 않았다. |
| S02 | 완전 충족 | AUC0-inf 기하평균 세 군과 단위가 원문과 일치한다. 모듈이 해당 표를 1위로 올렸다. |
| S03 | 원문 부재·정확 처리 | CT-P43 1.2의 Cmax 산술평균·SD는 없다. 1.1 Part 2의 mean [SD] 값을 옮기지 않았다. |
| S04 | 완전 충족 | T1/2 평균(SD) 세 군과 각주의 산술평균 표기가 일치한다. 헤더가 Geometric Mean인 표라 모듈은 매칭하지 않았고 기존 검색이 찾았다. |
| S05 | 원문 부재·정확 처리 | FDA 문서에는 AUCinf·Cmax의 산술평균·SD가 없고 Table 4는 기하 LS평균만 있다. 값을 지어내지 않았다. |
| S06 | 핵심값 미응답 | 모듈이 모노그래프 Table 14를 1위로 올렸고 초안의 tmax 중앙값·범위도 맞았다. 그러나 인용이 표 캡션에만 걸려 수치 검증에서 제거됐다. |
| S07 | 원문 부재·정확 처리 | EMA Table 4의 AUCinf는 GM(Geo CV%)뿐이다. 영어 요청에서도 기하평균을 산술평균으로 바꾸지 않았다. |
| S08 | 원문 부재·정확 처리 | BAT2206 심사 문서가 없고, 라벨의 오리지널 ustekinumab 자료를 BAT2206 시험에 귀속하지 않았다. |
| S09 | 부분 충족 | 비율·신뢰구간 통제 문항으로 모듈은 작동하지 않았다. 90% CI 0.82–0.98은 맞지만 비율 0.90이 군 인용 검증에서 제거됐다. |

원문에 값이 있는 4개 중 완전 충족 2·부분 1·미응답 1, 요청 통계량이 원문에 없는 5개는 5/5 정확 처리, 최종 오답 0/9다. 남은 실패 두 건(S06, S09)은 맞는 표를 검색한 뒤 인용·검증 단계에서 값이 빠진 경우로, §6의 1·6번 과제와 같은 원인이다. S04는 각주로 통계량을 정의한 표를 모듈이 읽지 못하는 한계를 보여 준다. [판정](../../data/runs/h_v2_rag_development_2026-10-14_luna_v1/statistical_holdout/adjudication.json), [원문 검토 패킷](../../data/runs/h_v2_rag_development_2026-10-14_luna_v1/statistical_holdout/review_packets.json), [실행 요약](../../data/runs/h_v2_rag_development_2026-10-14_luna_v1/statistical_holdout/summary.json).

[비교 요약](../../data/runs/statistical_table_generalization_2026-10-14/compare_4.9.3_vs_4.9.4_vs_4.9.4-off.json), [4.9.4 실행 기록](../../data/runs/statistical_table_generalization_2026-10-14/4.9.4.json), [4.9.3 실행 기록](../../data/runs/statistical_table_generalization_2026-10-14/4.9.3.json), [검증 실행기](../../eval/statistical_table_generalization.py), [회귀 테스트](../../tests/test_ra_statistical_table_retrieval.py).

## 6. 다음 개선 우선순위

1. **값과 조건을 같은 인용에 보존:** E03·G02의 유도 용량/조건처럼 초안 이후 선택 인용과 검증에서 빠지는 항목을 추적한다. 답변 길이보다 원문 값-조건 묶음의 보존을 먼저 개선한다.
2. **조건부 지침과 통계량 구분:** E04의 사례별 적용 조건, G04의 Mean/SD와 기하평균 구분이 보류되는 원인을 자체 인용 단위에서 해결한다. 의미 검사 완화로 점수를 올리지 않는다.
3. **사용자에게 보이는 충족 상태:** 수치·출처가 맞아도 전체 범위 검사 때문에 `partial`과 확인 요청이 남는 사례가 있다. 사실 지지 여부와 요청 항목별 누락을 더 정확히 표시한다.
4. **남은 연속 표 94개:** 반복 헤더·원본 좌표·닫히지 않은 경계 행을 검증하는 일반 복구를 확장한다. 페이지 간 값 결합은 검증 전까지 보류한다.
5. **독립 평가와 검색 지표:** 이번 개발 문항과 다른 질문·문서를 고정하고 사람 RA 검토를 붙인다. recall@k, 필요한 표의 문맥 포함률, 생성 후 누락률, 최종 완전 충족을 분리해 측정한다. 이번에는 독립 검색 정답셋·전문가 채점·통계적 유의성·운영 지연을 측정하지 않았다.
6. **검증기의 통계량 역할·단위 해석:** LS 기하평균 역할 오분류, 복합 단위(`hr*ng/mL`, `μg·h/mL`), Min/Max 범위 바인딩을 공용 검증 코드에서 별도 회귀 테스트와 함께 고친다. 통계표 검색이 맞는 표를 가져와도 값이 이 단계에서 빠질 수 있다(§5.5).
7. **표 각주와 인용 범위:** S04처럼 각주로 통계량을 정의한 표(`b Presented as arithmetic mean ± SD`)를 헤더 서명에 반영한다. S06처럼 인용이 캡션에만 걸리는 경우 같은 표의 값 셀까지 인용을 넓히는 방법을 검토한다.

현재 확인한 실패는 임베딩 모델 교체보다 표 본문의 검색 가능 여부와 검색 이후 값 손실에 집중돼 있다. 따라서 청크 크기·L2 정규화·임베딩 차원을 먼저 바꾸는 근거는 이번 결과만으로 부족하다.

## 7. 호출·검증·운영 상태

| 단계 | 새 Luna 호출 | 입력 토큰 | 출력 토큰 | 품질 판정 포함 |
| :-- | --: | --: | --: | :-- |
'''
    phases=[(p,read(run/p/'summary.json')) for p in
            ('paired','improved','transfer','table_focus','transfer_utf8')]
    calls=sum(s['new_provider_calls'] for _,s in phases)
    assert calls==123
    for name,s in phases:
        text+=f"| {name} | {s['new_provider_calls']} | {s['new_input_tokens']:,} | {s['new_output_tokens']:,} | {'제외: 입력 인코딩 오류' if name=='transfer' else '포함'} |\n"
    logbytes=(run/'tests_full_4_9_3.log').read_bytes()
    testlog=logbytes.decode('utf-16' if logbytes.startswith(b'\xff\xfe') else 'utf-8',errors='replace')
    assert 'Ran 1150 tests' in testlog and 'OK (skipped=4)' in testlog
    text+=f'''
Luna 호출은 **총 123회**, 이 중 정상 입력 품질 평가 단계 111회, 인코딩 오류로 제외한 단계 12회다. 이와 별도로 코드 4.9.4의 새 통계표 질문 단계(`statistical_holdout`)에서 23회를 썼다(§5.5). 저장 응답 재처리는 추가 호출 0회다. 이 숫자는 프로젝트 실행의 기록이며 계정 전체 사용량을 뜻하지 않는다. 금액은 계산하지 않았다.

`transfer` 4문항을 PowerShell 경유로 만들 때 한글이 `?`로 손상된 구현 실수가 있었다. 해당 12회는 전부 품질 점수에서 제외·보존하고, UTF-8 파일로 다시 실행한 `transfer_utf8`만 채점했다. 원래 19문항 비교는 정상 원문으로 실행됐다. 이후 입력 인코딩 검사도 추가했다. [제외 기록]({rel}/transfer/EXCLUDED_ENCODING_ERROR.json).

사용자 모델 정정 전 시작한 mini 실행은 즉시 중단했다. 장부상 27시도 중 26완료·1예약 상태이며, 중단 당시 진행 중 호출의 최종 사용량은 확인하지 않았다. Luna 점수·호출 합계와 분리해 [중단 기록](../../data/runs/h_v2_rag_development_2026-10-14_v1/INTERRUPTED_BY_MODEL_CORRECTION.json)에 보존했다.

코드 4.9.3의 전체 회귀는 **1,150개 실행, 1,146개 통과·4개 건너뜀, 실패 0**이다. 읽기 범위·통계표 검색·단위 쌍의 새 회귀를 포함한다. 저장 응답 8개도 정확한 인용 범위와 자체 검토 출처 경계를 통과했다. [전체 검사 로그]({rel}/tests_full_4_9_3.log).

project_shared의 최종 코드 4.9.4 전체 회귀는 **1,172개 실행, 전부 통과**다. 같은 날 다른 작업이 함께 바꾼 로더·DB 코드와 테스트를 포함한다. 그 이전 중간 실행에서 `test_ingestion_db` 2개가 로컬 PostgreSQL 마이그레이션 변경과 겹쳐 실패한 적이 있으나, 최종 실행에서는 통과했다. [4.9.4 검사 로그](../../data/runs/statistical_table_generalization_2026-10-14/tests_full_4_9_4.log).

**서비스 전환·재시작은 하지 않았다.** project_shared의 최종 디스크 코드는 `ra-research-rag-4.9.4`이고 biosimilar_collection은 `ra-research-rag-4.9.3`이며 기존 운영 색인과 원래 H v2는 그대로다. 검토 가능한 별도 최종 후보는 다음 경로다.

```text
{(run/'native_candidate_v2/release_final').relative_to(ROOT).as_posix()}
```

후속 전체 생성 평가와 독립 검증이 남아 있다. 서버가 현재 이 코드를 실행 중이라는 뜻도 아니다.

도구: [개발 평가](../../eval/h_v2_development_eval.py), [원문 검토 패킷](../../eval/h_v2_review_packets.py), [표 회수 검사](../../eval/probe_h_v2_native_fragments.py), [별도 후보 구성](../../eval/h_v2_native_candidate.py), [최종 출처 확정](../../eval/finalize_h_v2_native_candidate.py), [통계표 검색](../../rag/ra_statistical_tables.py), [통계표 검색 일반화 검증](../../eval/statistical_table_generalization.py), [원문 화면·PDF 해시 대조]({rel}/source_visual_review.json).
'''
    target=ROOT/'docs/rag/H_V2_RAG_DEVELOPMENT_EVAL_2026-10-14.md'
    target.write_text(text,encoding='utf-8')
    save(run/'final_artifacts.json',{'report':str(target.relative_to(ROOT)),'report_sha256':digest(target),
        'pipeline_version':re.search(r"VERSION = '([^']+)'",(ROOT/'rag/ra_pipeline.py').read_text(encoding='utf-8'))[1],
        'new_luna_calls':calls,'quality_phase_calls':111,
        'excluded_encoding_calls':12,'new_replay_calls':0,'production_pointer_changed':False,
        'rag_code_sha256':{str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'rag').glob('*.py'))},
        'tests':{'code':'ra-research-rag-4.9.3','log':'tests_full_4_9_3.log','run':1150,'passed':1146,'skipped':4,'failed':0}})
    print(target)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    render(p.parse_args().run.resolve())
