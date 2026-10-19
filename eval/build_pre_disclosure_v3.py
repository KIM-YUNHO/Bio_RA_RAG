"""Refine the synthetic v2 fixture without changing its scientific observations or historical outputs."""
from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT/'docs/demo/internal_pipeline_v3/02_INTERNAL_DOCUMENTS.json'
LABELS = {'W-IV-RERUN': 'IV FcRn 재분석', 'W-PFS-CPB': 'PFS CPB 탐색',
          'W-IV-CPB': '추가 IV CPB 시험', 'W-RA-RESPONSE': '모의 기관 질의 답변'}


def event(unit_id, scope, axis, value, order=10):
    return {'id': f'{unit_id}:{scope}:{axis}', 'scope_id': scope, 'scope_label': LABELS[scope],
            'axis': axis, 'value': value, 'order': order}


def fixture():
    data = json.loads((ROOT/'docs/demo/internal_pipeline_v2/02_INTERNAL_DOCUMENTS.json').read_text(encoding='utf-8'))
    units = {u['id']: deepcopy(u) for u in data['units']}
    units['U04']['text'] = '[합성 IV 재분석 요청 / S1] 박개발은 한빛분석 이시험에게 IV FcRn 독립 재분석의 실행 가능성과 가용 시료·계획·소요기간을 문의했다. 3근무일 내 회신을 요청했으며 이는 결과 납기 합의가 아니다. S1 현재 수락·납기 합의 자료가 없다. 기관 제출 업무는 아니다.'
    units['U04']['presentation'] = '130 mg IV vial'
    units['U06']['section'] = 'RESULT_TABLE'
    units['U06']['text'] = '[합성 IV 재분석 결과표 / S2] SIM-R102에서 SIM-IV-A=98.3%, SIM-IV-B=100.1%, SIM-IV-C=101.9%다. 첫 run의 98.0/100.0/102.0%와 같은 방향의 낮은 결과다. 합성 lot 요약값이며 반복 측정 원자료가 아니다. preliminary/pending을 유지하며 이전 SIM-R101을 보존한다. 계산·오류에 대한 담당자 의견은 별도 TECHNICAL_COMMENT 절에 있다.'
    units['U06']['related_unit_ids'] = ['U01', 'U02', 'U12', 'U14']
    units['U08']['section'] = 'CPB_RESULT_TABLE'
    units['U08']['source_kind'] = 'synthetic_experiment'
    units['U08']['text'] = '[합성 PFS CPB 탐색 결과표 / S2] 가용 PFS 시료 한 쌍에서 동일 가상 정규화 기준의 FcRn 요약값은 처리 전 P17=99%, 기준약=110%, 처리 후 P17=100%, 기준약=101%다. 모두 합성 수치이며 실제 원자료를 복원한 값이 아니다. 탐색적 차이 감소 관측이고 통계적 동등성·인과 입증·임상적 무영향을 뜻하지 않는다. 제형은 45 mg SC PFS이며 IV 직접 측정값이 아니다. 작업 회신 상태는 별도 WORK_RESPONSE 절을 따른다.'
    units['U08']['related_unit_ids'] = ['U07', 'U13', 'U14', 'U15']
    units['U09']['section'] = 'CMC_COMMENT'
    units['U09']['text'] = '[합성 CMC 의견 / S2] 박개발: IV 차이가 반복되고 PFS에 전하 변이·CPB 관련 단서가 있으므로 단순 분석오류 설명만으로 종료하지 말자. PFS 결과는 IV 직접 관측이 아니지만 적절한 연결 근거와 함께 간접 지지자료로 검토할 수 있다. IV/PFS 연결, 대조약 lot·범위 산정, 시험법·run 검토 및 kinetics 자료의 확보 범위를 확인하자. 공정 변경·출하 중단·임상적 무영향이나 추가시험 면제는 이 의견으로 결정하지 않는다.'
    units['U09']['related_unit_ids'] = ['U01', 'U06', 'U07', 'U08', 'U12', 'U14', 'U15', 'U16']

    def extra(key, base, **fields):
        units[key] = {**deepcopy(units[base]), 'id': key, **fields}

    extra('U12', 'U03', document_id='SIM-EXP-IV-02', section='TECHNICAL_COMMENT', available_from=2,
          related_unit_ids=['U06', 'U14'],
          text='[합성 분석자 검토 의견 / S2] 김분석은 정규화 계산과 대조군 기록을 재확인했고 현재까지 특정 계산 오류나 무효화 사유를 찾지 못했다고 회신했다. 이는 김분석의 기술 검토 의견이며 독립 검증·QA 승인·시험 유효성 확정이 아니다. 상세 시험법 검증 첨부와 QA 최종 승인 자료는 아직 미제공이다. 결과 수치는 별도 RESULT_TABLE 절을 인용한다.')
    extra('U13', 'U04', document_id='SIM-CRO-REQUEST-01', section='PFS_CPB_REQUEST',
          subject='전하 변이·FcRn 연결', presentation='45 mg SC PFS',
          text='[합성 PFS 탐색 요청 / S1] 박개발은 가용 PFS 시료의 전하 변이·CPB 전후 기능 비교 가능성을 이시험에게 문의했다. S1에는 수락·시료·실시 일정의 합의 자료가 없고 기술 검토 회신을 요청한 상태다. IV에 동일 시험을 실시했다는 뜻이 아니다.')
    extra('U14', 'U08', section='WORK_RESPONSE', type='fact', source_kind='synthetic_correspondence',
          subject='분석협력사 작업 회신', presentation='IV/PFS separate', modality='reported',
          data_maturity='not_applicable', run_validity='not_applicable',
          related_unit_ids=['U04', 'U06', 'U08', 'U12', 'U13'],
          text='[합성 한빛분석 작업 회신 / S2] 이시험은 IV 재분석 SIM-R102와 PFS 전하 변이·CPB 탐색의 결과 파일을 각각 회신했다. 따라서 이 두 작업에는 결과 회신 자료가 있다. 해당 시험의 정식 계약·합의문은 이 자료 묶음에 미제공이지만, 이를 현재 전체 업무가 미수락·미실시라는 의미로 바꾸지 않는다. QA 승인이나 기관 제출을 회신한 것은 아니다. IV CPB 추가 시험은 별도 작업으로 분리한다.')
    extra('U15', 'U04', document_id='SIM-CRO-RESPONSE-01', section='ADDITIONAL_IV_CPB', available_from=2,
          presentation='130 mg IV vial', related_unit_ids=['U09', 'U14'],
          text='[합성 추가 IV CPB 협의 상태 / S2] 이시험: 추가 IV vial CPB는 아직 실시하지 않았다. 추가 범위·시료·납기도 합의되지 않았다. ka·kd·KD 표는 현재 회신 묶음에 없다. 새 미팅에서 범위·시료·책임자·기간을 협의해야 한다. 이 미합의 상태는 이미 회신한 IV 재분석·PFS 탐색까지 미실시로 되돌리지 않는다.')
    extra('U16', 'U09', section='RA_COMMENT', subject='P17 기관 대응 준비',
          related_unit_ids=['U09', 'U12', 'U14', 'U15'],
          text='[합성 RA 의견 / S2] 한규제: 품질·기능적 유사성 차이와 비임상·PK 관련성을 연결한 설명 초안을 준비하자. PFS 자료는 IV 직접 측정과 구분하고 간접 지지자료로 쓰기 위한 근거와 한계를 정리한다. 실험 결과 회신, 기술 의견, QA 승인, 회사 답변 제출, 기관 수용은 서로 다른 상태다. 추가 IV CPB 범위·일정의 협의를 미팅 안건으로 올리자. 실제 기관 질의·승인·수용은 현재 자료에 없다.')
    units['U10']['related_unit_ids'] = ['U01', 'U06', 'U08', 'U15', 'U16']
    units['U11']['related_unit_ids'] = ['U10', 'U01', 'U02', 'U06', 'U07', 'U08', 'U09', 'U12', 'U14', 'U15', 'U16']

    roles = {
        'U01': ('protocol', '박개발', ['W-IV-RERUN']),
        'U02': ('experiment_result', 'P17 분석팀', ['W-IV-RERUN']),
        'U03': ('analyst_opinion', '김분석', ['W-IV-RERUN']),
        'U04': ('work_request', '박개발', ['W-IV-RERUN']),
        'U05': ('guideline', 'EMA 품질지침 요약', []),
        'U06': ('experiment_result', 'P17 분석팀', ['W-IV-RERUN']),
        'U07': ('experiment_result', 'P17 분석팀', ['W-PFS-CPB']),
        'U08': ('experiment_result', '한빛분석 시험팀', ['W-PFS-CPB']),
        'U09': ('internal_opinion', '박개발', ['W-IV-RERUN', 'W-PFS-CPB', 'W-IV-CPB']),
        'U10': ('simulated_agency_request', '연습용 기관 질의 작성자', ['W-RA-RESPONSE']),
        'U11': ('sponsor_draft', '한규제', ['W-RA-RESPONSE']),
        'U12': ('analyst_opinion', '김분석', ['W-IV-RERUN']),
        'U13': ('work_request', '박개발', ['W-PFS-CPB']),
        'U14': ('partner_response', '이시험', ['W-IV-RERUN', 'W-PFS-CPB']),
        'U15': ('partner_response', '이시험', ['W-IV-CPB']),
        'U16': ('internal_opinion', '한규제', ['W-IV-RERUN', 'W-PFS-CPB', 'W-IV-CPB'])}
    for key, unit in units.items():
        role, speaker, scopes = roles[key]
        unit['context'] = {'unit_id': key, 'source': {'document_id': unit['document_id'], 'version': '0.2',
            'section': unit['section'], 'source_kind': unit['source_kind']}, 'available_from': unit['available_from'],
            'statement_role': role, 'speaker': speaker, 'scope_ids': scopes, 'events': []}
    for key, scope in [('U04', 'W-IV-RERUN'), ('U13', 'W-PFS-CPB')]:
        units[key]['context']['events'] = [event(key, scope, axis, value) for axis, value in
            [('execution', 'requested'), ('scope_agreement', 'not_recorded'), ('technical_review', 'pending'), ('qa_approval', 'not_recorded')]]
    units['U12']['context']['events'] = [event('U12', 'W-IV-RERUN', 'technical_review', 'analyst_comment_recorded', 20)]
    units['U06']['context']['events'] = [event('U06', 'W-IV-RERUN', 'execution', 'result_reported', 5)]
    units['U08']['context']['events'] = [event('U08', 'W-PFS-CPB', 'execution', 'result_reported', 5)]
    units['U14']['context']['events'] = [event('U14', scope, axis, value) for scope in ('W-IV-RERUN', 'W-PFS-CPB')
        for axis, value in [('execution', 'result_reported'), ('scope_agreement', 'not_recorded'), ('qa_approval', 'not_recorded')]]
    units['U15']['context']['events'] = [event('U15', 'W-IV-CPB', axis, value) for axis, value in
        [('execution', 'not_performed'), ('scope_agreement', 'not_agreed')]]
    units['U11']['context']['events'] = [event('U11', 'W-RA-RESPONSE', axis, value) for axis, value in
        [('approval', 'draft'), ('submission', 'not_submitted'), ('agency_decision', 'no_actual_agency_process')]]
    data.update(schema_version='pre_disclosure_simulation_v3',
                refinement='Same synthetic science; separate observations, speakers and scoped work events.')
    data['units'] = sorted(units.values(), key=lambda u: (u['available_from'], u['id']))
    return data


if __name__ == '__main__':
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(fixture(), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'Created {TARGET.relative_to(ROOT)} (16 units; v2 left unchanged)')
