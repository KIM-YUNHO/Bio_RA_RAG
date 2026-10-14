"""Summarize preserved baseline, stage1 and completed ordered-update evaluation."""
from collections import Counter
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/runs/rag_updates_2026-10-09'


def read(p):return json.loads(p.read_text(encoding='utf-8'))


def main():
    base=read(ROOT/'data/runs/rag_evaluation_2026-10-09/analysis.json')
    scope=read(OUT/'stage1_scope/analysis.json')
    final=read(OUT/'final_22/analysis.json')
    assert final['n_responses']==22 and final['all_frozen_files_unchanged']
    calls=[];timings=[]
    for p in sorted((OUT/'final_22/responses').glob('*.json')):
        if '.job.' in p.name or '.request.' in p.name:continue
        r=read(p);own=[*r.get('understanding_telemetry',[])]
        own.extend(c for u in r['results'] for c in u.get('generation_telemetry',[]))
        assert sum(c.get('prompt_tokens',0) for c in own)==r['usage']['input_tokens']
        assert sum(c.get('completion_tokens',0) for c in own)==r['usage']['output_tokens']
        calls.extend(own);timings.append({'id':p.stem,'seconds':r['seconds'],'stages':r.get('timings')})
    telemetry={'calls':len(calls),'finish_reasons':dict(Counter(c.get('finish_reason') for c in calls)),
        'statuses':dict(Counter(c.get('status') for c in calls)),
        'token_usage_reconciled_with_all_recorded_calls':True,
        'input_tokens':sum(c.get('prompt_tokens',0) for c in calls),
        'output_tokens':sum(c.get('completion_tokens',0) for c in calls),
        'preflight_budget_violations':sum(c['estimated_prompt_tokens']+c['max_output_tokens']+c['framing_safety_tokens']>c['context_tokens'] for c in calls),
        'prompt_estimate_minus_actual':sorted({c['estimated_prompt_tokens']-c['prompt_tokens'] for c in calls})}
    result={'baseline':{k:v for k,v in base.items() if k not in ('rows','automatic')},
        'scope_only':{k:v for k,v in scope.items() if k not in ('rows','automatic')},
        'final':{k:v for k,v in final.items() if k not in ('rows','automatic')},'telemetry':telemetry,
        'timings':timings,'limits':['20 answerable development questions, not independent expert gold.',
            'Baseline retrieval70% is a lower bound because2 errors lost retrieval; final95% is fully observed.',
            'Baseline failed-call usage is missing; token totals are not a controlled efficiency comparison.',
            'The fixed-evidence schema A/B preceded the final quote-only schema and is not a second-model comparison.']}
    (OUT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    table=['## 최종22문항 결과','','동일20개 답변 가능 질문과60개 사실군, 별도2개 대조군을 비교했다. 원문 기반 에이전트 판정이며 전문가 검토는 대기다.','',
        '| 지표 | 수정 전 | 범위 수정만 | 최종 통합 |','|---|---:|---:|---:|']
    metrics=[('완전 정답률',lambda a:f"{a['complete_accuracy']:.1%} ({a['verdict_counts'].get('correct',0)}/20)"),
        ('필수 사실군 회수',lambda a:f"{a['fact_group_recall']:.1%} ({a['correct_fact_groups']}/60)"),
        ('정답/동등 근거 Hit@5',lambda a:f"{a['qualified_page_hit@5']:.1%}"),
        ('정답/동등 근거의 답변 문맥 포함',lambda a:str(a['qualified_context_hits'])+'/20'),
        ('부분 정답',lambda a:str(a['verdict_counts'].get('partial',0))),
        ('미응답',lambda a:str(a['verdict_counts'].get('missed',0))),
        ('오답',lambda a:str(a['verdict_counts'].get('incorrect',0))),
        ('실행 오류',lambda a:str(a['verdict_counts'].get('error',0)))]
    for name,fmt in metrics:table.append('| '+name+' | '+' | '.join(fmt(a) for a in [base,scope,final])+' |')
    table += ['',
        '기준/범위 수정 평가의 검색 지표는 오류로 검색 기록이 사라진 질문을 포함한 하한이다. 최종은20질문 모두 검색 결과가 관측됐다. 원래 엄격한 페이지 라벨만 적용한 최종 Hit@5는90%, 원문 판정으로 인정한 동등 근거를 포함하면95%다. 두 대조군은 모두 통과했다.',
        '',
        f"최종 호출{telemetry['calls']}개는 모두정상 stop 종료였고 입력{telemetry['input_tokens']:,}/출력{telemetry['output_tokens']:,}토큰이 호출 로그 합계와 일치했다. 예약한 입력·출력 예산 위반은0건이었다. 최종 지연은 P50 {final['automatic']['latency_seconds']['p50']:.2f}초, P95 {final['automatic']['latency_seconds']['p95']:.2f}초로, 수정 전 P50 {base['automatic']['latency_seconds']['p50']:.2f}초/P95 {base['automatic']['latency_seconds']['p95']:.2f}초 대비 속도 개선은 확인되지 않았다. 기준 평가의 실패 호출 토큰은 누락돼 토큰 총량을 그대로 효율 개선율로 해석할 수 없다.",
        '',
        '**부분 개선이며 전반적인 품질 문제는 해결되지 않았다.** 완전 정답은5→7개로 늘었지만 오답은1→2개다. FDA 표 제목(D01), 적격 조건을 포함한 각주(D05), 비교 방향 표시(D08)가 회복됐다. 반면 Q003은 다른 시험의 반복 투여 일정을, Q013은 사후 보정 값을 주 분석 대신 표시하는 새 오답이 발생했다. Q005는 범위 수정 단계에서는 맞았지만 최종에서는 원문 재서술로 탈락했다. Q010·D07은 정답 근거가 있어도 모델이 미응답했다. D06의 저장 출력 재생 개선은 실제 실행의 조합된 가짜 인용을 해결하지 못했다.',
        '',
        '문자 인용 통과율31/31=100%와 의미 정확도35%는 다르다. 본문·표의 원문을 찾는 단계보다, 찾은 근거에서 시험·분석·군·분모를 구분하고 연속 원문을 반환하는 단계가 남은 주요 실패 지점이다. 최종 미응답/부분/오답을 OCR 전체 문제로 묶거나, 모델 변경 효과를 수치로 단정할 근거는 없다.',
        '',
        '## 이어서 적용할 방향',
        '',
        '1. 시험·분석·대상군 귀속을 원문 절/표 범위에서 검토해 연결한다. 현재 검색 후보는 답변 프롬프트의 원문 메타데이터에 충분히 전달되지 않고, 미확정 후보를 확정값처럼 적용할 수 없다. 특히 주 분석/보정 분석, 단회/반복 투여를 구분한 근거가 필요하다.',
        '2. 모델이 수치·HTML·조건을 다시 쓰는 부담을 줄인다. 검증된 문장·셀·행/열 제목 후보를 먼저 만들고 모델이 그 식별자를 선택하는 출력 방식을 다음 후보로 비교한다. 잘못 고른 후보를 정답으로 승격하지 않도록 시험/분석/군의 적합성 판정은 별도로 유지한다.',
        '3. 동일 고정 근거와 간소화된 출력에서 더 강한 모델을 비교한다. 이번에는 로컬 모델 하나만 사용했으므로 모델 자체와 프롬프트 복잡도의 효과가 분리되지 않았다. 그 비교 전 임베딩 교체나 OCR 전량 재실행을 우선할 근거는 약하다.',
        '4. ABP/CTP의 일부 페이지, BMAB의 품질 문서, BAT의 개요 수준을 상품별 임상 성능으로 확대 해석하지 않는다. 보호 평가 자료를 제외한 임상 문서의 전처리·시험 등록부 보완 후 상품별 독립 질문과 전문가 판정을 마련한다.',
        '',
        '원문 판정과 사례별 회귀는 [최종 analysis.json](../../data/runs/rag_updates_2026-10-09/final_22/analysis.json), [case_results.csv](../../data/runs/rag_updates_2026-10-09/final_22/case_results.csv), [비교·호출 계측](../../data/runs/rag_updates_2026-10-09/comparison.json)에 보존했다.',
        '']
    report=ROOT/'docs/evaluation/RAG_UPDATES_2026-10-09.md'
    text=report.read_text(encoding='utf-8').split('## 최종22문항 결과')[0]
    text=text.replace('최종22문항 평가를 수행 중이며 완료 후 결과를 추가한다.','동일22문항의 최종 평가와 원문 판정을 완료했다.')
    report.write_text(text+'\n'+'\n'.join(table),encoding='utf-8')
    progress=read(OUT/'progress.json')
    progress['final_regression']={'status':'complete','artifact':'final_22',
        'complete_accuracy':final['complete_accuracy'],'fact_group_recall':final['fact_group_recall'],
        'known_semantic_errors':2,'code_and_live_index_unchanged':True}
    progress['remaining_quality_work']='Source study/analysis attribution, constrained source selection, second-model comparison and wider product clinical coverage.'
    (OUT/'progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(telemetry,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
