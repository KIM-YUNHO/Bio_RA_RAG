import sys,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from preprocess.ingest import sha256
from preprocess.inventory import write_json
from preprocess.workflow import timestamp
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8-sig'))
OLD=ROOT/'data/runs/rag_finalization_2026-10-10_v1';RUN=ROOT/'data/runs/rag_finalization_2026-10-10_v2'
RELEASE=ROOT/'data/rag/releases/rag_finalization_2026-10-10_v2'
FINAL=RUN/'openai_regulatory_followup'
VALIDATION=RUN/'safety_validation'
notes=read(RUN/'manual_source_review.json')
verification=read(RUN/'service_safety_verification/verification.json');complete=read(RUN/'completion.json');service=read(RUN/'service_state.json')
assert verification['passed'] and service['passed'] and complete['execution_complete']
assert all(sha256(ROOT/n)==h for n,h in verification['code_sha256'].items())
assert service['python_verification_sha256']==sha256(RUN/'service_safety_verification/verification.json')
assert read(VALIDATION/'completed.json')['code_sha256']==verification['code_sha256']
retrieval_audit=read(RUN/'final_regulatory_retrieval/completed.json')
assert retrieval_audit['api_calls']==0 and not retrieval_audit['generation_performed']
assert retrieval_audit['verification_sha256']==sha256(RUN/'service_safety_verification/verification.json')
assert sha256(RELEASE/'release_manifest.json')==complete['release_manifest_sha256']==service['release_manifest_sha256']
assert read(FINAL/'completed.json')['n']==6
snapshot=RUN/'final_code_snapshot';snapshot.mkdir(exist_ok=True)
for name,digest in verification['code_sha256'].items():
 dest=snapshot/name;dest.parent.mkdir(parents=True,exist_ok=True)
 if not dest.exists():shutil.copyfile(ROOT/name,dest)
 assert sha256(dest)==digest
write_json(snapshot/'index.json',{'created_at':timestamp(),'files':verification['code_sha256'],'verification_sha256':sha256(RUN/'service_safety_verification/verification.json')})
ledger=read(ROOT/'data/runs/model_quota/2026-10-10.json');own=[a for a in ledger['attempts'] if Path(a['evaluation_out']).is_relative_to(OLD) or Path(a['evaluation_out']).is_relative_to(RUN)]
stages=[]
for folder in (OLD/'openai_development',OLD/'openai_development_v2',OLD/'openai_transfer',RUN/'openai_transfer_final',RUN/'openai_transfer_followup',FINAL):
 calls=[a for a in own if Path(a['evaluation_out'])==folder]
 stage={'stage':str(folder.relative_to(ROOT)),'new_api_attempts':len(calls),'prompt_tokens':sum(a.get('prompt_tokens') or 0 for a in calls),'completion_tokens':sum(a.get('completion_tokens') or 0 for a in calls),
  'execution_complete':(folder/'completed.json').exists(),'frozen_sha256':sha256(folder/'frozen.json') if (folder/'frozen.json').exists() else None}
 if (folder/'completed.json').exists():stage['completed_sha256']=sha256(folder/'completed.json')
 stages.append(stage)
def records(folder,decisions):
 rows=[]
 for case,note in decisions.items():
  path=folder/'responses'/f'{case}.json';r=read(path);items=[i for a in r['results'] for i in a['items']]
  if note['core_source_supported']:assert items
  rows.append({'case':case,**note,'response':str(path.relative_to(ROOT)),'response_sha256':sha256(path),
    'statuses':[a['status'] for a in r['results']], 'facts':[{'value':i['value'],'source_label':i['source_label'],'conditions':i.get('conditions',[]),
      'sources':[{'document_id':a['evidence'][k]['document_id'],'version_id':a['evidence'][k]['version_id'],'pdf_page':a['evidence'][k]['pdf_page'],'quote':i['quote']} for k in i['evidence'] if k in a['evidence']]} for a in r['results'] for i in a['items']]})
 return rows
dev=records(OLD/'openai_development_v2',notes['development']);transfer=records(FINAL,notes['transfer'])
validation=records(VALIDATION,notes['transfer'])
adjud={'version':'finalization-agent-source-review-1','reviewer':'Codex source-based development review; no expert certification','completed_at':timestamp(),
 'development':{'release':'rag_finalization_2026-10-10_v1','cases':dev,'answerable_core_supported':sum(r['core_source_supported'] for r in dev),'answerable_total':11,'safe_abstention_controls':1,'final_code_rerun':False},
 'new_source_transfer':{'release':RELEASE.name,'cases':transfer,'core_supported':sum(r['core_source_supported'] for r in transfer),'total':6,
  'expected_source_versions':read(OLD/'openai_transfer/prepared.json')['cases'],'independent_holdout':False,'source_review_assets':str((OLD/'source_review/sources.json').relative_to(ROOT)),
  'interpretation':'Source-selected development transfer followed by fixes, with exact request-cache reuse. Does not measure independent/general clinical accuracy.'},
 'post_api_service_validation':{'cases':validation,'new_api_calls':0,'new_retrieval_performed':False,'validation_only':True,'new_generation_verified':False},
 'current_regulatory_retrieval_audit':{'manifest':'data/runs/rag_finalization_2026-10-10_v2/final_regulatory_retrieval/completed.json','api_calls':0,'generation_performed':False,'answer_accuracy_certified':False}}
write_json(RUN/'source_adjudication.json',adjud)
completion={'completed_at':timestamp(),'execution_complete':True,'preprocessing_through_embeddings_complete':True,'release':str(RELEASE),'release_manifest_sha256':complete['release_manifest_sha256'],
 'new_openai_attempts':len(own),'project_api_attempts_today':len(ledger['attempts']),'new_prompt_tokens':sum(a.get('prompt_tokens') or 0 for a in own),'new_completion_tokens':sum(a.get('completion_tokens') or 0 for a in own),
 'stages':stages,'tests':verification['tests_run'],'tests_passed':True,'source_adjudication_sha256':sha256(RUN/'source_adjudication.json'),
 'default_service_provider':'ollama','api_evaluation_provider':'openai','model':'gpt-4o-mini','independent_accuracy_certified':False,'legacy_global_index_replaced':False,
 'service_state_sha256':sha256(RUN/'service_state.json'),'final_test_code_sha256':verification['code_sha256'],
 'full_development_suite_rerun_after_final_source_guards':False,'current_code_fresh_generation_verified':False,
 'post_api_validation_sha256':sha256(VALIDATION/'completed.json'),'offline_regulatory_retrieval_sha256':sha256(RUN/'final_regulatory_retrieval/completed.json'),'rag_quality_complete':False,
 'remaining_source_review_cases':[r['case'] for r in transfer if not r['core_source_supported']]}
write_json(RUN/'evaluation_completion.json',completion)
default_docs=sum(d['eligible_chunks']>0 for d in read(RUN/'served_rag_summary.json')['documents'])
products='\n'.join(f"| {p} | {s['documents']} | {s['chunks']:,} | {s['default_chunks']:,} |" for p,s in complete['by_product'].items())
case_table='\n'.join(f"| {r['case']} | {'핵심 사실 확인' if r['core_source_supported'] else '보완 필요'} | {', '.join(r['statuses'])} → {', '.join(v['statuses'])} | {r['review_note']} |" for r,v in zip(transfer,validation))
usage_table='\n'.join(f"| {Path(s['stage']).name} | {s['new_api_attempts']} | {s['prompt_tokens']:,} | {s['completion_tokens']:,} | {'완료' if s['execution_complete'] else '호출 상한에서 중단'} |" for s in stages)
automatic=read(FINAL/'automatic.json')['summary']
report=f'''# 2026-10-10 전처리·임베딩 및 API 검증 최종 기록

승인된 **92문서·4,160페이지·5상품**의 전처리와 **{complete['vectors']:,}개 BGE-M3 임베딩(1,024차원)** 생성을 완료했다. 서비스는 `{RELEASE.name}`를 명시적으로 사용하여 실행한다. EMA·FDA·HC만 포함하며 평가 보류 9문서는 계속 제외한다.

## 적재 및 서비스

| 상품 | 문서 | 전체 청크/벡터 | 기본 검색 청크 |
|---|---:|---:|---:|
{products}

기본 검색은 {default_docs}문서·{complete['default_chunks']:,}청크다. FDA 허가 서신처럼 비기본 자료는 보조 문서 옵션을 켜야 검색된다. 6건 추가 평가에서 FDA 서신 2건은 이 옵션을 사용했고 문서를 고정하지 않았다.

- RAG: http://127.0.0.1:8765
- 원문 검토: http://127.0.0.1:8766
- 서비스 기본 제공자는 기존 **Ollama**다. 성능평가는 **OpenAI gpt-4o-mini**로 수행했다. 로컬 모델 답변 성능을 OpenAI 결과로 인증하지 않는다.
- 두 서버의 요약·정적 화면·기관/상품 범위 거부·원문 페이지·PDF Range 응답을 실제 HTTP로 확인했다. 서비스가 공급하는 청크 해시는 새 릴리스와 일치했다. 이전 글로벌 색인과 frozen 릴리스는 보존했다.

## 정한 파이프라인과 이번 수정

Native 추출 → 필요한 일반 OCR(Paddle) → native 기하/일반 표 구조 복원 → 남은 복잡 ROI만 Qwen → 출처·페이지·표/그림 문맥 결합 → 청킹 → BGE-M3 → 색인 검증을 유지한다. 이번 최종 보완에서 추가 OCR/Qwen 호출은 0회다. 이전 전처리 실행 전체의 호출 수를 0회라고 의미하지 않는다.

첫 수정 릴리스는 달라진 입력 29개만 임베딩했다. 최종 릴리스에서는 새로 인식한 제목이 있는 2개 구성요소만 다시 청킹했고, **86입력만 새로 임베딩·10,002벡터는 동일 입력/모델 지문 확인 후 재사용**했다. 기존 92문서의 raw 자료와 추출은 반복하지 않았다.

- 이름 없는 시험 제목에서는 이전 시험 조상을 끊고, 등록된 식별자가 있는 일반 PK/임상 시험 제목을 원문 위치에 연결한다. 제목 힌트가 실제 본문의 다른 시험 식별자를 덮어쓰지 못하게 한다.
- 긴 원문 문단을 통째로 제외하지 않고 연속 범위로 나눈다. 성인/소아 인구 제목의 원문 범위를 분리한다.
- 표 본문과 필요한 시험 제목을 토큰 예산에 함께 예약한다. 치료군 열을 행 제목으로 표시하던 문제, 단위 분리 및 요청한 군/비교/체중별 누락 검사를 수정했다.
- 투여 값의 체중 조건은 그 값을 포함한 문장에 연결한다. 초기/유지 간격을 모두 보존하고 제품 식별자 숫자를 용량으로 사용할 수 없게 한다.
- PK 통계, 보관, 적응증, 시판 통지 사건을 다른 용량/행정 조항과 구분한다. 주 분석 조건은 같은 원문 블록의 실제 primary-analysis 문장으로 확인한다.
- 시판 통지 사건과 소아 허가 조건 검사는 ID 선택 평가 경로와 직접 인용 서비스 경로에 공통 적용했다. 허용 값이 없는 문맥은 모델을 호출하지 않고 보류한다.

## 실제 API 결과와 한계

기존 12문항은 `openai_development_v2`의 한 고정 코드에서 11개 답변 가능 문항의 핵심 값이 원문에 맞았고, 잘못된 90 mg 전제의 1건은 수치를 제시하지 않았다. 이 전제를 45 mg로 설명하는 답변은 충분하지 않았다. 용량 답변의 간격 표시 문제도 남아 후속 코드에서 수정했다. 이 12문항을 마지막 코드에서 전부 새 API 호출로 반복하지 않았다.

기존 응답 근거에 없던 승인 문서 버전 6개를 별도로 골랐다. 초기 추가 평가에서는 검색 적중/인용 일치만으로 정답을 보장하지 못했다. 실제로 FDA 시판 통지를 라벨 제출 14일로 답하고, 소아 적응증을 PREA 평가 면제 조항으로 답한 오류를 확인했다. 복잡 OCR 자체보다 **필요한 문맥 선택과 조건·사건·값 역할 연결**이 주요 잔여 원인이었다.

마지막 실제 API 실행(`openai_regulatory_followup`)의 6문항은 한 고정 코드에서 검색·생성을 수행했으며, 요청 본문/스키마/모델이 완전히 같은 실제 API 응답만 재사용했다. 실제 신규 호출은 2회였다. 정답·루브릭·평가 보류 자료는 모델에 보내지 않았다.

이후 허가 서신 요청에 사전 행정 서신을 쓰는 오류와 `submit a`처럼 끊긴 행동 문장을 차단하는 검사를 추가했다. 현재 코드에서는 저장된 6응답에 검사만 재적용했고 새 API 호출·새 답변 생성은 없었다. FDA 통지 답은 partial, 소아 적응증 답은 not_found로 바뀌었다. 아래 화살표는 원래 API 결과 상태 → 현재 검증 후 상태이며, 변경된 코드의 새 생성 성능을 의미하지 않는다.

현재 코드의 FDA 2문항 검색도 저장된 질문 해석으로 별도 재실행했다(API 0회). ABP654 허가 서신 6페이지의 온전한 통지 문단과 CT-P43 허가 서신 5페이지의 소아 6세 이상·PsO/활성 PsA 문단이 답변 후보에 공급됨을 확인했다. 자료가 없는 문제가 아니라 답변 선택·표시 문제임을 뒷받침한다. 소아 문단 후보에 후속 제조 관련 문장이 함께 묶이는 현상은 추가 범위 분리 과제로 남았으며, 검색 문맥 공급 성공을 새 답변 정답으로 점수화하지 않았다.

| 문항 | 원문 판정 | API 상태 → 후속 검사 상태 | 검토 내용 |
|---|---|---|---|
{case_table}

6문항의 핵심 사실 원문 확인: **{adjud['new_source_transfer']['core_supported']}/6**. 모든 필요한 조건·표시가 완전한 답변이라는 뜻은 아니다. FDA 2문항은 통지 내용·올바른 허가 적응증의 새 답변 생성 재평가가 남았다. HC pen 답변에도 냉장 복귀가 31일 기간 종료 전이어야 한다는 조건을 더 명확히 표시해야 한다. 마지막 실제 API 실행의 검색 페이지 적중은 {automatic['page_hit@5']:.1%}, 정답 페이지의 답변 문맥 공급은 {automatic['gold_in_answer_context_n']}/6이다. 인용 일치율은 의미 정확도와 별개다. 이 표는 실패를 보고 수정한 소규모 개발 회귀이고, 독립 미노출 평가·전문가 임상 검증·전체 RA 문서 일반화 점수가 아니다. 9개 보류 문서는 여전히 미적재 상태다.

## 호출과 검증

| 단계 | 실제 신규 API 호출 | 입력 토큰 | 출력 토큰 | 실행 |
|---|---:|---:|---:|---|
{usage_table}

이번 진행의 실제 신규 호출은 **{len(own)}회**, 프로젝트의 오늘 장부는 **{len(ledger['attempts'])}회**(이전 실행 포함)다. 캐시 토큰/호출은 신규 사용량에 중복 집계하지 않았다. 모델 가용성 확인·LLM 심판·자동 HTTP 재시도는 없었다. 초기 전이 평가의 중단은 자체 실행 호출 상한이며 제공자 일일 한도 오류로 해석하지 않는다. 장부는 계정의 다른 사용량을 포함하지 않는다. 캐시가 포함된 지연을 전부 새 생성 속도로 보고하지 않는다.

최종 회귀 **{verification['tests_run']}개 통과**. 최종 코드 복사본, 코드/모델/청크 지문, 신규 API 원본, 원문 이미지와 판정, 서비스 확인 기록을 보존했다.

## 남은 품질 범위

전처리 실행과 임베딩은 완료했지만 표 표현 검토 535건·그림 설명 검토 709건의 표시가 남아 있다. 이는 전부 Qwen 재처리 큐를 의미하지 않는다. 우선 FDA 2문항의 온전한 사실 선택과 적응증 범위 끝을 개선하고, HC 보관 조건을 모두 표시한 뒤 해당 문항을 제한된 API 호출로 재평가해야 한다. 장문·중복 조건의 표시와 보수적인 partial 플래그, 다른 언어/형식의 규제 사건 범위, 독립 자료 평가도 남았다. 전체 상품의 모든 임상 수치가 검증 완료됐다고 표시하지 않는다.

기록(`data/runs/rag_finalization_2026-10-10_v2/`): `completion.json`, `service_safety_verification/verification.json`, `source_adjudication.json`, `evaluation_completion.json`, `safety_validation/completed.json`, `service_state.json`. 마지막 API 실행의 `frozen.json`과 원본 응답은 현재 검사 코드와 구분해 보존한다. 운영 원칙: [PIPELINE_PRINCIPLES.md](../PIPELINE_PRINCIPLES.md).
'''
(ROOT/'docs/rag/FINALIZATION_API_EVAL_2026-10-10.md').write_text(report,encoding='utf-8')
principles=ROOT/'docs/PIPELINE_PRINCIPLES.md';text=principles.read_text(encoding='utf-8');marker='## 2026-10-10 최종 전처리·임베딩 및 서비스 적용'
if marker not in text:
 text+=f'\n\n{marker}\n\n현재 릴리스는 `{RELEASE.name}`이며 92문서·4,160페이지·{complete["vectors"]:,}벡터까지 완료했다. 9개 보류를 유지하고 EMA·FDA·HC와 5상품을 서비스에서 확인했다. OCR/Qwen 재실행은 없다. 최종 회귀 {verification["tests_run"]}개와 OpenAI 원문 대조 기록은 [최종 적용 기록](rag/FINALIZATION_API_EVAL_2026-10-10.md)에 있다.\n\n원문 시험 제목의 연결을 문자 위치로 보존하며 다른 실제 시험 식별자가 있으면 제목 힌트로 덮지 않는다. 긴 문단의 연속 범위를 보존하고, 성인/소아 범위와 투여·통계·보관·허가·통지 사건을 구분한다. 표 본문과 필요한 실제 제목을 함께 예산에 예약한다. 인용이 맞아도 사건·대상·조건이 틀리면 답으로 제시하지 않는다. 허용 값이 없는 문맥은 API 호출 없이 보류한다. 평가의 핵심 사실 확인과 partial 표시, 독립 임상 의미 검증을 별도 상태로 기록한다.\n'
 principles.write_text(text,encoding='utf-8')
readme=ROOT/'rag_service/README.md';text=readme.read_text(encoding='utf-8')
text=text.replace('rag_context_update_2026-10-10_v1',RELEASE.name).replace('10,083','10,088').replace('8,330','8,335').replace('513개',f"{verification['tests_run']}개")
text=text.replace('서비스는 내려 둔 상태다.','서비스는 새 릴리스로 실행 중이며 HTTP 범위·원문 링크를 확인했다.')
text=text.replace('[검색·문맥 업데이트 평가 기록](../docs/rag/CONTEXT_UPDATE_API_EVAL_2026-10-10.md)','[최종 전처리·API 검증 기록](../docs/rag/FINALIZATION_API_EVAL_2026-10-10.md)')
text=text.replace('SB17 31/3,057/2,700','SB17 31/3,059/2,702').replace('ABP654 21/2,406/1,777','ABP654 21/2,408/1,779').replace('BMAB1200 10/1,089/916','BMAB1200 10/1,090/917')
text+='\n\n마지막 OpenAI 추가 평가는 기존 응답 근거에 없던 승인 문서 6버전을 사용했다. 핵심 사실 원문 확인은 4/6이며 FDA 2문항은 보완이 필요하다. 개발 실패를 수정한 회귀이며 독립 일반화 성능으로 해석하지 않는다. 이후 검사만 재적용해 FDA 통지 내용의 불완전한 항목은 제외하고, 소아 적응증의 잘못된 출처는 보류했다. 현재 검사 코드로 새 답변 생성은 재평가하지 않았다. 실제 사건(시판 통지/라벨 제출)과 허가 대상(소아 적응증/PREA)을 원문으로 구분하며, 관련 검사는 직접 인용 서비스 경로에도 적용한다. 전처리·임베딩 실행 완료와 표/그림 의미 검토 상태는 별도로 유지한다.\n'
readme.write_text(text,encoding='utf-8')
status_path=ROOT/'data/runs/corpus_update_2026-10-09/latest_update_status.json';old=read(status_path)
previous=RUN/'previous_latest_update_status.json'
if not previous.exists():shutil.copyfile(status_path,previous)
for field in ('current_release_offline_retrieval','api_evaluation_stages','latest_scope_context_audit','latest_api_core_source_review'):
 if field in old:old['historical_'+field]=old.pop(field)
old.update(updated_at=timestamp(),release=str(RELEASE.relative_to(ROOT)).replace('\\','/'),chunks=complete['chunks'],vectors=complete['vectors'],default_chunks=complete['default_chunks'],
 tests=verification['tests_run'],tests_passed=True,service_started=True,legacy_global_index_replaced=False,actual_api_attempts_today=len(ledger['attempts']),new_openai_calls=len(own),
 new_ocr_calls=0,new_qwen_calls=0,primary_goal_execution_complete=True,rag_quality_complete=False,current_code_fresh_api_generation_verified=False,preprocessing_completion_manifest=str((RUN/'completion.json').relative_to(ROOT)).replace('\\','/'),
 source_adjudication=str((RUN/'source_adjudication.json').relative_to(ROOT)).replace('\\','/'),latest_api_evaluation=str((RUN/'evaluation_completion.json').relative_to(ROOT)).replace('\\','/'),
 service_state=str((RUN/'service_state.json').relative_to(ROOT)).replace('\\','/'),scope_context_report='docs/rag/FINALIZATION_API_EVAL_2026-10-10.md',api_evaluation_stages=stages,
 current_release_default_search={'documents':default_docs,'chunks':complete['default_chunks'],'BAT2206_default_chunks':complete['by_product']['BAT2206']['default_chunks']},
 latest_api_core_source_review={'development_release':'rag_finalization_2026-10-10_v1','development_answerable_core_complete':11,'development_total':11,'safe_control':1,
  'latest_transfer_core_complete':adjud['new_source_transfer']['core_supported'],'transfer_total':6,'independent_holdout':False,'full_development_suite_rerun_after_final_guards':False},
 fresh_api_verification_remaining=completion['remaining_source_review_cases'],remaining_quality_work=['535 table representation and 709 figure-description review markers','Verbose/duplicated source-condition display and conservative partial flags','Independent RA transfer and other-language event/indication coverage','Default local model answer performance is not certified by the OpenAI evaluation'],
 previous_status_snapshot=str(previous.relative_to(ROOT)).replace('\\','/'))
write_json(status_path,old)
print(json.dumps({'release':RELEASE.name,'vectors':complete['vectors'],'tests':verification['tests_run'],'new_api_calls':len(own),'today_project_calls':len(ledger['attempts']),'transfer_core_supported':adjud['new_source_transfer']['core_supported'],'service_running':True,'report':'docs/rag/FINALIZATION_API_EVAL_2026-10-10.md'},ensure_ascii=False))
