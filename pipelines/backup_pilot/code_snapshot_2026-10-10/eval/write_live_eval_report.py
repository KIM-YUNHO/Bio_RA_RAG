"""Render the completed, source-adjudicated live evaluation as a Korean report."""
from collections import Counter
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from eval.current_server_eval import read
from eval.live_eval_analyze import analyze


def percent(value): return f'{100*value:.1f}%'


def render(folder, output):
    data=analyze(folder,require_complete=True)
    cases={r['id']:r for r in read(folder/'cases.json')}
    retrieval=read(folder/'retrieval_aggregate.json')
    auto=data['automatic']; verdicts=data['verdict_counts']; n=data['n_answerable']
    unknown=data['retrieval_unknown_after_unit_error_n']
    completed=read(folder/'completed.json')
    verdict_names={'correct':'완전 정답','partial':'부분 답변','incorrect':'오답',
        'missed':'미응답','error':'실행 오류','correct_abstention':'보류 적절',
        'incorrect_abstention':'보류 실패','correct_exclusion':'제외 정책 정상'}
    def result(lang,scope,mode): return next(r for r in retrieval if r['language']==lang and r['scope']==scope and r['mode']==mode)
    lines=[
        '# 현재 서버 RAG 성능평가 — 2026-10-09',
        '',
        f'현재 실행 중인 서버에 한국어 질문 22개를 직접 보내 평가했다. 답변 가능한 {n}문항 중 완전 정답은 {verdicts.get("correct",0)}개({percent(data["complete_accuracy"])}), 부분 답변 {verdicts.get("partial",0)}개, 미응답 {verdicts.get("missed",0)}개, 오답 {verdicts.get("incorrect",0)}개, 실행 오류 {verdicts.get("error",0)}개였다. 이 수치는 원본을 대조한 에이전트 판정이며 전문가 확정 점수가 아니다.',
        '',
        '검색에 성공해도 필요한 사실·조건을 답변으로 전달하지 못하는 문제가 확인됐다. 우선 수정 대상은 화면에서 선택한 검색 범위를 지키는 계획 수립, 답변 근거 예산 배분, 표의 행·열 및 치료군 조건 보존, 생성 실패 처리다.',
        '',
        '## 평가 조건',
        '',
        '- 대상: SB17/Pyzchiva의 EMA·FDA·PMDA·Health Canada·ClinicalTrials.gov 5문서, 523페이지, 1,260청크. 기본 검색은 790청크다.',
        '- 실제 서버: `http://127.0.0.1:8765`, `rag-pipeline-2`, BGE-M3 + BM25 + 캡션 후보 검색, 검색 5페이지.',
        '- 답변: 로컬 Ollama `qwen3.5:9b`, Q4_K_M. 설정 `num_ctx=8192`, 실제 호출 `num_predict<=2200`, temperature0, seed0. 인용 검사 탈락 시 동일 근거로 재생성 1회.',
        '- 실제 질의: 과거 개발 QA14문항 + 진단8문항. 20문항은 답변 가능, 1문항은 잘못된 90mg 투여군 전제, 1문항은 기본 제외 프로토콜의 검색 범위 통제다. 프로토콜 포함·PMDA 일본어 표·캐나다 라벨·한국어 바꿔 쓰기·그림·페이지 간 표 제목을 포함했다.',
        '- 실제 요청에는 질문과 화면의 제품·기관·문서·포함 옵션만 보냈다. 정답·근거 주석은 평가자 쪽에만 두고 인덱스에 추가하지 않았다.',
        '- 평가 중 서버 설정·인덱스·코드를 고정했다. 모델 digest와 소스 파일 해시, 요청·응답 전문을 보관했다. 응답 재사용 없이 실제 HTTP 작업으로 실행했다.',
        '- 별도로 기존26문항의 영어26/한국어26/한국어 변형14/일본어2, 총68개 검색 표현을 같은 질의 벡터로 평가했다. 이 실험은 질의 해석·답변을 거치지 않는 검색 진단이다.',
        '',
        '## 실제 질의응답 결과',
        '',
        '| 지표 | 결과 |',
        '|---|---:|',
        f'| 완전 정답 / 답변 가능 질문 | {verdicts.get("correct",0)}/{n} ({percent(data["complete_accuracy"])}) |',
        f'| 부분 답변 / 미응답 / 오답 / 실행 오류 | {verdicts.get("partial",0)} / {verdicts.get("missed",0)} / {verdicts.get("incorrect",0)} / {verdicts.get("error",0)} |',
        f'| 조건을 보존한 필수 사실 그룹 회수 | {data["correct_fact_groups"]}/{data["required_fact_groups"]} ({percent(data["fact_group_recall"])}) |',
        f'| 기존 지정 페이지 Hit@5 하한 | {percent(auto["page_hit@5"])} |',
        f'| 확인된 대체 페이지 포함 Hit@5 하한 | {percent(data["qualified_page_hit@5"])} |',
        f'| 대체 페이지 포함 MRR@5 하한 | {data["qualified_mrr@5"]:.3f} |',
        f'| 답변 근거에 관련 정답 페이지 포함 | {data["qualified_context_hits"]}/{n} (오류 시 미관측 포함) |',
        f'| 반환 항목의 원문 문자 대조 | {auto["literal_valid_items"]}/{auto["accepted_items"]} |',
        f'| 반환 항목의 원본 영역 링크 | {auto["items_with_review_links"]}/{auto["accepted_items"]} |',
        f'| 에이전트 검토상 요청 관련 원문에 뒷받침된 항목 | {data["requested_scope_supported_items"]}/{data["reviewed_items"]} |',
        f'| 화면 요청 기관과 계획 기관 충돌 | {data["request_scope_conflicts"]}/22 |',
        f'| 화면 요청 제품 밖으로 계획 확대·대체 | {data["request_product_conflicts"]}/22 |',
        f'| 재생성 시도 / 더 많은 항목 복원 | {auto["repair_attempts"]} / {sum(r.get("repair_accepted",0) for r in read(folder/"automatic.json")["rows"])} |',
        '',
        f'생성 오류 {unknown}건에서는 서버가 검색 페이지·근거를 최종 응답에 남기지 않아 검색 결과를 관측할 수 없다. 오류를 정답률 분모에서 빼지 않았고, 검색 Hit/MRR는 전체 답변 가능 질문을 분모로 한 **확인된 성공의 하한**으로 표시했다. 이를 모두 확인된 검색 실패로 해석하면 안 된다.',
        '',
        '원문 검사 통과율은 의미 정확도가 아니다. 현재 서버는 검사에 통과한 항목만 표시하므로 이 값은 구조적으로 높게 나온다. 수치는 맞지만 군별 귀속이 누락된 경우, 일부 비교만 답한 경우는 부분 답변으로, 비교 방향이 뒤집힌 경우는 오답으로 판정했다. 필수 사실 그룹은 수치와 요청 조건을 함께 충족해야 하며, 원문에 맞는 관련 설명만으로는 충족 처리하지 않았다. Q010의 세 수치는 출처상 맞지만 화면 답변에 군명이 없어, 관련 원문 지지 항목은3개이고 조건을 모두 갖춘 사실 그룹은0개다.',
        '',
        '원래 질문 CSV의 지정 페이지가 정답 위치를 모두 포함하지 않는다. 예를 들어 Q004의 Cmax/AUC0-inf는 p160에도 있어 이 답변을 검색 실패로 단정할 수 없다. 평가 전에 존재하던 `labels_manual_v0.1.csv`의 grade2/nonpartial 대체 위치를 별도로 집계했다. 페이지 적중은 한 페이지에 질문의 모든 사실이 있다는 보증이 아니다.',
        '',
        '## 질문별 판정',
        '',
        '| ID | 질문 | 판정 | 필수 사실 그룹 | 근거·실패 설명 |',
        '|---|---|---|---:|---|'
    ]
    for row in data['rows']:
        review=row['judgment']; case=cases[row['id']]
        question=case['question'].replace('|','/')
        notes=', '.join(review.get('failure_categories',[])) or '원문·범위 일치'
        rank=row['qualified_page_rank']
        status=('검색 미관측' if not row['retrieval_observed'] else f'정답 페이지 {rank}위' if rank else '정답 페이지 top5 없음')
        lines.append(f'| {row["id"]} | {question} | {verdict_names.get(review["verdict"],review["verdict"])} | {review["correct_fact_groups"]}/{row["required_fact_groups"]} | {status}; {notes} |')
    controls=[r for r in data['rows'] if r['expected']!='answerable']
    lines += ['', '보류·제외 통제는 완전 정답률20문항과 별도다. '+ '; '.join(f'{r["id"]}: {verdict_names.get(r["judgment"]["verdict"],r["judgment"]["verdict"])}' for r in controls)+'. 각각1문항으로 안전성 일반화를 주장할 수 없다.',
        '', '## 검색만 분리한 진단', '',
        '| 표현 / 기관 명시 | BM25 Hit@5 | dense Hit@5 | hybrid+caption Hit@5 | hybrid+caption MRR@10 |',
        '|---|---:|---:|---:|---:|']
    for lang,label in [('en','영어26'),('ko','한국어26'),('ko_b','한국어 변형14'),('ja','일본어2')]:
        a,b,c=[result(lang,'explicit_agency',mode) for mode in ('bm25','dense','hybrid_caption')]
        lines.append(f'| {label} | {a["page_hits@5"]}/{a["n"]} | {b["page_hits@5"]}/{b["n"]} | {c["page_hits@5"]}/{c["n"]} ({percent(c["hit@5"])}) | {c["mrr@10"]:.3f} |')
    lines += ['', '한국어 질의의 표면 표현에 민감하다. 검색만 분리한 한국어 hybrid는 dense와 Hit@5가 같지만 MRR가 낮다. 이 실험은 서버의 영문화·범위 해석·문서군 선호·답변 생성 효과를 포함하지 않으므로 실제 질의응답 점수와 직접 같은 값으로 비교하면 안 된다.',
        '', '| 검색 범위 / hybrid+caption | 영어26 Hit@5 | 한국어26 Hit@5 | 한국어 변형14 Hit@5 |', '|---|---:|---:|---:|']
    for scope,label in [('default','기본 검색'),('explicit_all','기본 제외 문서 포함 / 기관 미지정'),('explicit_agency','기본 제외 문서 포함 / 기관 명시')]:
        vals=[result(lang,scope,'hybrid_caption') for lang in ('en','ko','ko_b')]
        lines.append('| '+label+' | '+' | '.join(f'{v["page_hits@5"]}/{v["n"]}' for v in vals)+' |')
    lines += ['', '검색 불가능한 정답도 분모에 남겼다. 기본 검색에서는 프로토콜·캐나다 라벨이 제외되므로 포함 옵션과 같은 조건으로 비교해야 한다. 기관 명시 실험 수치로 전체 범위 검색 성능을 대신하지 않았다.',
        '', '## 응답 시간과 사용량', '',
        f'- 실제22요청의 서버 측 시간: 중앙값 {auto["latency_seconds"]["p50"]:.2f}초, P95 {auto["latency_seconds"]["p95"]:.2f}초, 평균 {auto["latency_seconds"]["mean"]:.2f}초.',
        f'- 보고된 입력 토큰 {auto["input_tokens"]:,}, 출력 토큰 {auto["output_tokens"]:,}. 실패한 호출의 토큰이 누락돼 총 사용량의 하한이다.',
        '- 질문을 순차 처리했고 실제 모델 로드·질의 해석·질의 임베딩·답변·제한된 재생성을 포함했다. 검색 전에 끝난 범위 해석 실패도 시간 통계에 포함한다. 동시 부하나 장시간 운영 성능 측정은 아니다.',
        '', '## 다음 수정 우선순위', '',
        '1. **선택한 검색 범위 유지:** Q001/Q005에서 화면의 EMA 범위를 모델이 sponsor로 바꾸었고, D07은 SB17을 ABP654로 대체했으며 D06은 다른 제품을 추가했다. 제품·기관·문서 선택을 계획의 경계로 적용하고, 질문과 충돌하면 빈 결과와 이유를 명시해야 한다.',
        '2. **정답 근거의 전달 보장:** Q014는 p45를 찾고도 앞선3청크가9,937자를 차지해 답변 문맥에서 빠졌다. 검색한 각 페이지의 핵심 근거를 먼저 확보한 뒤 관련 문맥을 배분하고, 표 전체·출처 범위를 보존해야 한다.',
        '3. **조건을 담은 답변 항목:** Q010처럼 군명이 빠지거나 Q008처럼 다른 표의 숫자를 가져오는 문제를 평가해야 한다. D08은 SB17/EU 수치를 맞게 인용했지만 화면에는 “SB17 대비 EU”라고 써서 비교 방향을 뒤집었다. 행·열·평가변수·분석집단·비교군·시점과 수치의 연결을 표시·검증하고, 제목/본문이 있는 D01과 조정 분석값이 있는 Q011의 미응답을 회귀 사례로 둔다. D05는 적격성 조건을 생략했고, D06은 HTML 줄바꿈(`<br>`)으로 나뉜 평균±표준편차 값이 문자 검증에서 탈락했다. 표 구조를 보존한 값 검증과 표시되는 조건의 완전성을 함께 확인해야 한다.',
        '4. **생성 완료·오류 관측:** 완료되지 않은 생성 응답의 finish reason, 입력·출력 토큰, 검색 페이지와 근거를 남겨야 한다. 답변 스키마·인용 길이·문맥 예산을 조정한 뒤 같은 고정 사례로 오류·지연을 다시 측정한다.',
        '5. **검색 회귀와 독립 평가:** Q012의 p46 표 순위를 개선하고 한국어 변형 질의를 점검한다. 그 다음 아직 튜닝에 쓰지 않은 문서와 전문가 정답으로 확인한다. 현재 인덱스에 없는 제품의 holdout을 검색 실패로 섞거나 현재 개발 점수를 독립 일반화 성능으로 주장하지 않는다.',
        '', '## 판정 범위와 재현', '',
        '완전 정답/필수 사실 채점은 기존 AI_DRAFT Gold와 원문을 대조한 에이전트 초안이다. Q008의 PK-set N=67과 실제 분석 n=62/64/60, Q012의 ANCOVA 표 행·열, D07의 용량별 중앙값/평균은 원본 페이지 이미지도 확인했다. 전체22문항의 사람 확정·독립 이중 판정은 하지 않았다.20문항에서 한 문항은5%p이므로 작은 점수 차이의 일반화에 한계가 있다.',
        '', '원본: [EMA PDF](../../data/raw/fea275487bb971962201b77aec3d2e12cee7d545be026b378f1980fa272f6e76.pdf), [FDA PDF](../../data/raw/a45db692abad62f38efbef242f728cf2ea3fc894a48c16fbdc8201417ef4a6fa.pdf), [Health Canada PDF](../../data/raw/692365dcf3c0b32a2fd7b8c43a8d99545f0a263f36f10866ce6f0fd3d4e2a403.pdf). 페이지는 물리 PDF1-based 기준이다.',
        '', f'실행 완료: `{completed["completed_at"]}` (UTC). 결과 폴더: `data/runs/rag_evaluation_2026-10-09/`.',
        '', '- `frozen.json`, `additional_freeze.json`, `model_inventory.json`: 서버·코드·모델·레이블 해시.',
        '- `cases.json`, `rubric.json`, `reference_audit.json`: 질문·평가자 전용 정답·필수 사실·출처 검증.',
        '- `responses/`: 실제 HTTP 요청·작업·응답 전문. `automatic.json`: 자동 원문·페이지·시간 지표.',
        '- `source_judgments.json`, `analysis.json`, `case_results.csv`, `review_checklist.txt`: 질문별 원문 판정·실패 유형·집계.',
        '- `retrieval.json`, `retrieval_aggregate.json`: BM25/dense/hybrid 및 범위별 검색 진단.',
        '- `completed.json`:22응답 완료 및 서버·코드 변경 여부. `final_checks.json`: 최종 서비스/인덱스 확인.',
        '', '```powershell',
        '# 같은 SB17 5문서 서버를 실행한 상태에서, 새 결과 폴더를 지정한다.',
        '.\\.venv\\Scripts\\python.exe -X utf8 eval/current_server_eval.py --out data/runs/my_live_eval',
        '# 이번 원문 판정을 포함한 최종 집계를 재현한다.',
        '.\\.venv\\Scripts\\python.exe -X utf8 eval/live_eval_analyze.py --run data/runs/rag_evaluation_2026-10-09 --require-complete',
        '.\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -p test_current_server_eval.py',
        '```',
        '', '새 실행에서는 이번과 같은 수준의 원문 판정을 별도로 작성해야 답변 정확도를 산출할 수 있다. 정답을 서버에 보내 자동으로 정답률을 높이는 방식은 사용하지 않는다.', '']
    output.write_text('\n'.join(lines),encoding='utf-8')
    print(output)


if __name__=='__main__':
    render(ROOT/'data/runs/rag_evaluation_2026-10-09',ROOT/'docs/evaluation/RAG_PERFORMANCE_2026-10-09.md')
