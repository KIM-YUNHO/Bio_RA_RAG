"""Run the v4 internal-document scenario through the app's work-draft service, one model call per checkpoint.

Isolated: a file workspace under work/, never the app database. The model reads only what WorkDraftService.prepare
selects from the registered bundle on each date; the rubric below is review-side only and never sent. The rubric is a
lexical triage of required/forbidden points, not a grader; findings are adjudicated by reading the drafts.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

from rag_service import work_assist
from rag_service.work_drafts import WorkDraftService
from rag_service.workspaces import WorkspaceStore

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'docs/demo/internal_pipeline_v4/02_INTERNAL_DOCUMENTS.json'
RUNS = [('T1', 'internal'), ('T2', 'internal'), ('T2', 'partner'), ('T3', 'internal'), ('T4', 'internal')]
# (id, must|must_not, regex, what it checks). Presence is a triage signal; a human reads every draft.
RUBRIC = {
    'T1': [('T1-range', 'must', r'107', '적용 범위(107–127%) 명시'),
           ('T1-below', 'must', r'하한\s*미만|범위\s*(밖|아래|미만)|하회|미달', '범위 하한 미만 판단'),
           ('T1-status', 'must', r'잠정|QA|2차\s*검토', '잠정·QA 검토 전 상태 구분'),
           ('T1-rerun', 'must', r'재분석', '독립 재분석 조치'),
           ('T1-no-fail', 'must_not', r'불합격|허가\s*(불가|어렵)|무효(로\s*(처리|판정)(?![^.]{0,15}(않|없|불가))|이다)', '불합격·무효·허가 불가 단정 없음'),
           ('T1-no-cause', 'must_not', r'C-?말단\s*(Lys|라이신)[^.]{0,20}(원인이다|원인으로\s*확인|때문이다)', '원인 확정 없음'),
           ('T1-no-future', 'must_not', r'R-HB|98\.3|PO-26|QA-REV', '기준일 이후 자료 없음')],
    'T2': [('T2-po', 'must', r'발주|PO-26-0318', 'IV 재분석 발주(서면 합의) 반영'),
           ('T2-rerun', 'must', r'98\.3|재현', 'IV 재분석 결과'),
           ('T2-pfs-open', 'must', r'비공식|미정|비용|보고서\s*형식|정식\s*보고서', 'PFS 탐색 조건 미확정'),
           ('T2-attrib', 'must', r'이시험|한빛분석[^.]{0,30}(제안|의견)', "'유사함' 제안의 발언 주체"),
           ('T2-challenge', 'must', r'8항|범위\s*밖|계획서', '제안과 계획서 8항의 충돌 지적'),
           ('T2-next', 'must', r'IV[^.]{0,80}(CPB|CEX|전하\s*변이|kinetics)[^.]{0,120}(직접|협의|합의|시험할)|IV\s*CPB|kinetics|KD', '대상 제형(IV) 직접 확인(CPB·kinetics) 협의 안건'),
           ('T2-no-nonagree', 'must_not', r'IV\s*재분석[^.]{0,25}(미합의|합의되지)', 'IV 재분석 미합의 오기 없음'),
           ('T2-no-future', 'must_not', r'4월\s*24|5월\s*8|PO-26-0402|v1\.1|재산정', '기준일 이후 자료 없음')],
    'T2P': [('T2P-no-internal', 'must_not', r'QA-REV|R103|QA\s*승인|97–128|97-128|김분석\s*의견', '사내 한정 자료 미노출'),
            ('T2P-next', 'must', r'IV[^.]{0,80}(CPB|CEX|전하\s*변이|kinetics)[^.]{0,120}(직접|협의|합의|시험할)|IV\s*CPB|kinetics|KD', '대상 제형(IV) 직접 확인(CPB·kinetics) 협의 안건')],
    'T3': [('T3-v10', 'must', r'v1\.0', '현재 적용 기준 v1.0'),
           ('T3-range', 'must', r'107', '107–127% 범위 유지'),
           ('T3-unapproved', 'must', r'미승인|변경관리|CC|효력', '개정 초안 미승인 상태'),
           ('T3-retro', 'must', r'소급', '소급 적용 불가'),
           ('T3-due', 'must', r'5월\s*8|05-08|5/8', 'IV CPB 서면 예정일 5/8'),
           ('T3-attrib', 'must', r'한규제|RA\s*의견|최품질|QA', '의견·판단의 주체'),
           ('T3-no-future', 'must_not', r'R-HB-0428|1\.14|100\.2|모의\s*심사|Q-0[123]', '기준일 이후 자료 없음')],
    'T4': [('T4-q1', 'must', r'Q-01[^\n]{0,200}(미승인|v1\.1|통합|98–130|98-130)|(미승인|v1\.1|통합|98–130|98-130)[^\n]{0,200}Q-01', 'Q-01 미승인 범위 사용 지적'),
           ('T4-q2', 'must', r'초안\s*보고서|보고서\s*초안|5월\s*22|최종\s*보고서[^.]{0,12}(아니|아님|전|예정)', 'Q-02 초안 자료를 최종처럼 쓴 문구 지적'),
           ('T4-q3', 'must', r'Q-03[^\n]{0,250}(PK|1상|6월)|(PK|1상|6월)[^\n]{0,250}Q-03', 'Q-03 PK 근거 부족 지적'),
           ('T4-status', 'must', r'미승인|미제출', '답변 상태'),
           ('T4-mock', 'must', r'모의|실제\s*기관[^.]{0,8}(아님|아니)', '모의 질의 표시'),
           ('T4-no-done', 'must_not', r'(제출|승인)\s*(완료|되었|됐)', '제출·승인 완료 단정 없음')],
}


def flatten(record):
    draft = record['draft']
    lines = [draft['position']]
    for column in draft['columns']:
        lines += [item['text'] for item in column['items']]
    return '\n'.join(lines)


def rubric(key, text):
    rows = []
    for rid, kind, pattern, what in RUBRIC[key]:
        found = re.search(pattern, text)
        rows.append({'id': rid, 'kind': kind, 'what': what, 'ok': bool(found) if kind == 'must' else not found,
                     'match': found.group(0) if found else None})
    return rows


def render(record, checks):
    lines = [f"## {record['label']} · {record['task_label']} · 기준일 {record['as_of']} · {'협력사 공유용' if record['audience'] == 'partner' else '사내용'}", '',
             f"질문: {record['question']}", '',
             f"입력 {len(record['selected'])}/{len(record['eligible_unit_ids'])}건 ({record['retrieval']['method']}) · 기준일 이후 {record['hidden_after_count']}건 제외 · "
             f"{record.get('model_reported')} · 입력 {record['usage']['input_tokens']} / 출력 {record['usage']['output_tokens']} 토큰", '',
             *(['원문 기록의 상태: ' + ' / '.join(line['label'] + ' — ' + ' · '.join(x['axis_label'] + ': ' + x['label'] for x in line['states']) for line in record.get('status_summary', [])), '']
               if record.get('status_summary') else []),
             f"**{record['draft']['position']}**", '']
    for column in record['draft']['columns']:
        lines += [f"### {column['label']}", '']
        for item in column['items']:
            lines.append(f"- [{item['basis_label']}] {item['text']} [{', '.join(item['source_unit_ids'])}]")
        lines.append('')
    warnings = record['audit']['warnings']
    lines += [f"**자동 검사:** {'검토 필요 ' + str(len(warnings)) + '건' if warnings else '경고 없음'}", '']
    labels = {c['key']: c['label'] for c in record['draft']['columns']}
    for w in warnings:
        lines.append(f"- {labels[w['column']]} {w['item'] + 1}: `{w['code']}` {w['detail']}")
    lines += ['', '**루브릭 1차 분류(어휘 검사, 판정 아님):** ' + ', '.join(f"{c['id']} {'✓' if c['ok'] else '✗'}" for c in checks), '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--live', action='store_true', help='call the model (one call per run)')
    parser.add_argument('--only', nargs='*', help='labels to run, e.g. T3 T4 T2P')
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit('Choose a new, empty output directory')
    bundle = json.loads(INPUT.read_text(encoding='utf-8'))
    points = {p['id']: p for p in bundle['checkpoints']}
    store = WorkspaceStore(out / 'notebooks')
    project = store.create({'name': '[v4 시나리오] P17 FcRn 내부 업무', 'ingredient': 'ustekinumab'})
    store.register_documents(project['id'], bundle)
    project = store.get(project['id'])
    service = WorkDraftService(store, out / 'drafts', model='gpt-5.6-luna',
                               chat_factory=None if args.live else (lambda folder: (_ for _ in ()).throw(RuntimeError('prepare-only'))))
    meta = {'version': 'internal-scenario-v4-3', 'created_at': datetime.now(timezone.utc).isoformat(), 'live': args.live,
            'input_file': str(INPUT.relative_to(ROOT)), 'input_sha256': hashlib.sha256(INPUT.read_bytes()).hexdigest(),
            'code_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                            ('rag_service/work_assist.py', 'rag_service/internal_documents.py', 'rag_service/memo_workflow.py', 'rag_service/work_drafts.py')},
            'storage': 'isolated_file_workspace', 'app_database_modified': False, 'rubric_sent_to_model': False, 'runs': []}
    sections = []
    try:
        for point_id, audience in RUNS:
            label = point_id + ('P' if audience == 'partner' else '')
            if args.only and label not in args.only:
                continue
            point = points[point_id]
            payload = {'task': point['task'], 'question': point['question'], 'as_of': point['as_of'], 'audience': audience}
            if not args.live:
                prepared = work_assist.prepare(project, **payload)
                (out / f'{label}.input.json').write_text(json.dumps({'payload': payload, 'messages': work_assist.messages(prepared)}, ensure_ascii=False, indent=2), encoding='utf-8')
                meta['runs'].append({'label': label, 'state': 'prepared', 'selected': [u['unit_id'] for u in prepared['selected']]})
                continue
            print('Running', label, flush=True)
            summary = service.submit(project['id'], payload)
            while (record := service.get(project['id'], summary['id']))['state'] == 'running':
                time.sleep(1)
            record['label'] = label
            if record['state'] != 'completed':
                meta['runs'].append({'label': label, 'state': record['state'], 'error': record.get('error')})
                print(label, 'failed:', record.get('error'), flush=True)
                continue
            checks = rubric('T2P' if label == 'T2P' else point_id, flatten(record))
            if label == 'T2P':
                checks += [c for c in rubric('T2', flatten(record)) if c['id'] in ('T2-po', 'T2-rerun', 'T2-no-nonagree', 'T2-no-future')]
            sections.append(render(record, checks))
            meta['runs'].append({'label': label, 'draft_id': record['id'], 'state': 'completed', 'selected': [u['unit_id'] for u in record['selected']],
                                 'warnings': [w['code'] for w in record['audit']['warnings']], 'rubric': checks,
                                 'usage': record['usage'], 'model_reported': record.get('model_reported')})
            print(label, 'warnings', [w['code'] for w in record['audit']['warnings']],
                  'rubric misses', [c['id'] for c in checks if not c['ok']], flush=True)
    finally:
        service.close()
        (out / 'summary.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if sections:
            (out / 'drafts.md').write_text('\n'.join(sections), encoding='utf-8')


if __name__ == '__main__':
    main()
